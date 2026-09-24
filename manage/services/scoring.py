import re
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import ExtractYear, ExtractMonth
from manage.models import (Activity, ClassTerm, Report, RosterVersion, ScoringPolicyVersion,
    OwnerScoringSettings, WEIGHT_DEFAULTS, COUNT_FIELDS, SummaryCount)
from . import calendar
from .errors import BusinessError
from .read_snapshot import consistent_read, read_snapshot

MONTHLY_DEFAULTS = {'base': '60.00', 'minimum': '0.00', 'maximum': None}


def money(value):
    return str(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def average_display(value, places):
    return format(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), f'.{places}f')


@consistent_read
def policy_for(owner_id, term):
    version = ScoringPolicyVersion.objects.filter(owner_id=owner_id,
        effective_term_start__lte=term.start).order_by('-effective_term_start', '-id').first()
    revision = OwnerScoringSettings.objects.filter(owner_id=owner_id).values_list('revision', flat=True).first() or 0
    return {'revision': revision, 'version': version.pk if version else 0,
        'weights': version.weights if version else {k: money(v) for k,v in WEIGHT_DEFAULTS.items()},
        'monthly': version.monthly if version else dict(MONTHLY_DEFAULTS),
        'average_decimal_places': version.average_decimal_places if version else 2,
        'algorithm': version.algorithm if version else 'calendar-month-v1'}


def roster_for(classroom, term, include_inactive=False):
    versions = RosterVersion.objects.filter(inclass=classroom,
        effective_term_start__lte=term.start).order_by('student_id', '-effective_term_start', '-id')
    seen, rows = set(), []
    for v in versions:
        if v.student_id in seen:
            continue
        seen.add(v.student_id)
        if v.active or include_inactive:
            rows.append({'id': v.student_id, 'number': v.number, 'name': v.name,
                         'sex': v.sex, 'active': v.active})
    return sorted(rows, key=lambda r: (r['number'], r['id']))


def class_top_three(rows):
    """Rank nonzero per-student counts in the selected term, not score weights."""
    categories = (
        ('absent', '缺勤', ('absent',)),
        ('late', '迟到', ('late',)),
        ('leave', '请假', ('leave',)),
        ('activity', '活动', ('low', 'mid', 'high')),
        ('discipline', '违纪', ('dlow', 'dmid', 'dhigh')),
    )
    result = []
    for key, label, fields in categories:
        ranked = [{**{field: row.get(field) for field in ('id', 'name', 'number', 'sex')},
                   'count': sum(int((row.get('counts') or {}).get(field, 0)) for field in fields)}
                  for row in rows]
        ranked = sorted((row for row in ranked if row['count'] > 0),
                        key=lambda row: (-row['count'],
                            tuple((1, int(part)) if part.isdecimal() else (0, part.casefold())
                                  for part in re.split(r'(\d+)', row['number'])),
                            row['number'], row['id'] or 0))[:3]
        result.append({'key': key, 'label': label, 'people': ranked})
    return result


def aggregate_counts(classroom, start, end, student_ids=None):
    reports = Report.objects.filter(activity__inclass=classroom,
        activity__occurred_on__gte=start, activity__occurred_on__lt=end)
    if student_ids is not None:
        reports = reports.filter(student_id__in=student_ids)
    filters = {k: Q(activity__activity_type='class', status=k) for k in ('absent','late','leave')}
    filters.update({k: Q(activity__activity_type='activity', level=k) for k in ('low','mid','high')})
    filters.update({'d'+k: Q(activity__activity_type='discipline', discipline=k) for k in ('low','mid','high')})
    data = reports.order_by().annotate(y=ExtractYear('activity__occurred_on'), m=ExtractMonth('activity__occurred_on'))
    data = data.values('student_id','y','m').annotate(**{k: Count('id',filter=v) for k,v in filters.items()})
    return {(r['student_id'], r['y'], r['m']): {k:r[k] for k in WEIGHT_DEFAULTS} for r in data}


def month_score(counts, weights, monthly=None):
    monthly = MONTHLY_DEFAULTS if monthly is None else monthly
    result = Decimal(monthly['base'])
    for k in WEIGHT_DEFAULTS:
        result += counts.get(k,0) * Decimal(weights[k]) * (1 if k in ('low','mid','high') else -1)
    result = max(Decimal(monthly['minimum']), result)
    return min(Decimal(monthly['maximum']), result) if monthly['maximum'] is not None else result


def snapshot_activities(classroom, term):
    """Copy the complete term's records into the immutable class-term view."""
    def value_for(kind, report):
        value = (report['status'] if kind=='class' else report['level'] if kind=='activity'
                 else 'd'+report['discipline'])
        return 'normal' if value in ('present','none','dnone') else value

    activities = list(Activity.objects.filter(inclass=classroom, occurred_on__gte=term.start,
        occurred_on__lt=term.end).order_by('-occurred_on', '-id'))
    reports = Report.objects.filter(activity_id__in=[a.pk for a in activities]).values(
        'activity_id', 'student_id', 'status', 'level', 'discipline')
    by_activity = {}
    for report in reports:
        by_activity.setdefault(report['activity_id'], []).append(report)
    return [{'id':a.pk, 'name':a.name, 'details':a.details, 'kind':a.activity_type,
             'date':a.occurred_on.isoformat(), 'time':a.time.isoformat(timespec='seconds'),
             'status':a.status, 'revision':a.revision,
             'url':f'/classes/{classroom.code}/records/{a.pk}/',
             'student_values':{str(r['student_id']):value_for(a.activity_type,r)
                               for r in by_activity.get(a.pk, [])}}
            for a in activities]


def class_report(classroom, term, today=None, weights=None, monthly=None):
    today = today or calendar.business_today()
    if term.end <= today:
        # A completed term may need to create its one immutable snapshot.
        with transaction.atomic():
            return _class_report(classroom, term, today, weights, monthly)
    with read_snapshot():
        return _class_report(classroom, term, today, weights, monthly)


def _class_report(classroom, term, today, weights, monthly):
    today = today or calendar.business_today()
    ended = term.end <= today
    archive = ClassTerm.objects.select_for_update().filter(inclass=classroom, term_key=term.key).first() if ended else None
    if archive and archive.archived_data is not None:
        return archive.archived_data
    if ended and (weights is not None or monthly is not None):
        raise BusinessError('term_read_only', '历史快照不能用临时评分设置重算。', 403)
    if classroom.legacy_pending:
        raise BusinessError('legacy_baseline_pending', '旧库历史名单与成绩需要先完成迁移核对。', 409)
    if ended and classroom.started_on >= term.end:
        return {'rows':[], 'summary':{'average':'0.00','student_count':0,'month_count':0},
                'policy':policy_for(classroom.owner_id,term),'term_key':term.key,
                'algorithm':'calendar-month-v1','snapshot_version':1,'activities':[]}
    if (ended and not RosterVersion.objects.filter(inclass=classroom,
            effective_term_start__lte=term.start).exists() and
            Activity.objects.filter(inclass=classroom,occurred_on__gte=term.start,
                                    occurred_on__lt=term.end).exists()):
        raise BusinessError('historical_roster_unverified',
            '该学期有记录但没有可信名单，不能自动生成历史快照。',409)
    months = term.months(today)
    policy = policy_for(classroom.owner_id, term)
    weights = policy['weights'] if weights is None else weights
    monthly = policy['monthly'] if monthly is None else monthly
    policy = {**policy, 'weights': weights, 'monthly': monthly}
    identities = roster_for(classroom,term)
    counts = aggregate_counts(classroom,term.start,term.end,[r['id'] for r in identities])
    result, exact_averages = [], []
    for identity in identities:
        total_counts = {k:0 for k in WEIGHT_DEFAULTS}
        detail, total = [], Decimal('0')
        for y,m in months:
            values = counts.get((identity['id'],y,m), {k:0 for k in WEIGHT_DEFAULTS})
            score = month_score(values,weights,monthly)
            total += score
            for k,v in values.items():total_counts[k] += v
            detail.append({'key':f'{y}-{m:02d}','label':f'{y}年{m}月','score':money(score),'counts':values})
        exact_average = total/len(months) if months else Decimal('0')
        exact_averages.append(exact_average)
        result.append({**identity,'score':average_display(exact_average,policy['average_decimal_places']),
                       'counts':total_counts,'months':detail})
    average = sum(exact_averages,Decimal('0')) / len(exact_averages) if exact_averages else Decimal('0')
    report = {'rows':result, 'summary':{'average':average_display(average,policy['average_decimal_places']),'student_count':len(result),'month_count':len(months)},
            'policy':policy,'term_key':term.key,'algorithm':'calendar-month-v1'}
    if ended:
        report.update(snapshot_version=1, frozen_at=timezone.now().isoformat(),
                      activities=snapshot_activities(classroom,term),
                      record_roster=roster_for(classroom,term,include_inactive=True))
        if archive:
            archive.archived_data=report
            archive.save(update_fields=['archived_data'])
        else:
            ClassTerm.objects.create(inclass=classroom,term_key=term.key,owner_id=classroom.owner_id,
                policy_id=policy['version'] or None,archived_data=report,
                baseline_source='frozen-term-facts')
    return report


def rebuild_months(classroom, student_ids, months):
    """Only called for current-term changes, in the same transaction as facts."""
    from datetime import date
    if not student_ids:
        return
    rows = []
    for year, month in set(months):
        start = date(year,month,1)
        end = date(year+(month==12), month%12+1,1)
        counts = aggregate_counts(classroom,start,end,student_ids)
        for student_id in student_ids:
            data = counts.get((student_id,year,month),{})
            rows.append(SummaryCount(student_id=student_id,year=year,month=month,
                **{field:data.get(k,0) for k,field in COUNT_FIELDS.items()}))
    SummaryCount.objects.bulk_create(rows, update_conflicts=True,
        unique_fields=['student','year','month'],update_fields=list(COUNT_FIELDS.values()))
