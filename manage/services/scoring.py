import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import ExtractYear, ExtractMonth
from manage.models import (Activity, Class, ClassTerm, Report, RosterVersion, ScoringPolicyVersion,
    OwnerScoringSettings, WEIGHT_DEFAULTS, COUNT_FIELDS, SummaryCount)
from . import calendar
from .errors import BusinessError
from .read_snapshot import consistent_read, read_snapshot
from .timestamps import beijing_iso

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


def ended_term(classroom):
    """Require trustworthy lifecycle metadata; never infer an old end date."""
    if not classroom.archived:
        return None
    if not classroom.ended_at or not classroom.ended_term_key:
        raise BusinessError('class_archive_unverified', '该班级结束管理依据待核实，不能补造名单或成绩。', 409)
    try:
        term = calendar.parse_term(classroom.ended_term_key)
    except BusinessError:
        raise BusinessError('class_archive_unverified', '该班级结束管理学期无效，需核实归档依据。', 409) from None
    stamp = classroom.ended_at
    day = timezone.localtime(stamp).date() if timezone.is_aware(stamp) else stamp.date()
    if calendar.term_for_date(day) != term:
        raise BusinessError('class_archive_unverified', '该班级结束时间与归档学期不一致，需核实归档依据。', 409)
    return term



def validate_ended_archive(classroom):
    """Validate the evidence shape, without reading facts or recomputing scores.

    Only the new class-ending format is accepted as an end-of-management
    baseline. Normal term archives retain their separate legacy compatibility.
    """
    final_term = ended_term(classroom)
    if final_term is None:
        raise BusinessError('class_archive_unverified', '班级尚无结束管理依据。', 409)
    archive = ClassTerm.objects.filter(inclass=classroom, term_key=final_term.key).first()
    report = archive.archived_data if archive else None

    def require(condition):
        if not condition:
            raise ValueError

    def integer(value, minimum=0):
        require(type(value) is int and value >= minimum)
        return value

    def amount(value):
        require(isinstance(value, str))
        result = Decimal(value)
        require(result.is_finite() and result >= 0)
        return result

    def counts(value):
        require(isinstance(value, dict) and set(value) == set(WEIGHT_DEFAULTS))
        for count in value.values():
            integer(count)

    def identity(row):
        require(isinstance(row, dict))
        integer(row['id'], 1)
        require(isinstance(row['number'], str) and bool(row['number']))
        require(isinstance(row['name'], str) and bool(row['name']))
        require((row['sex'] is None or isinstance(row['sex'], str)) and type(row['active']) is bool)

    try:
        require(isinstance(report, dict))
        require(report['frozen_reason'] == 'class-ended' and report['frozen'] is True)
        require(integer(report['snapshot_version']) == 1 and report['term_key'] == final_term.key)
        ended_day = datetime.fromisoformat(beijing_iso(classroom.ended_at)).date()
        frozen_day = datetime.fromisoformat(beijing_iso(report['frozen_at'])).date()
        require(frozen_day == ended_day)
        expected_months = [f'{year:04d}-{month:02d}' for year, month in final_term.months(ended_day)]
        require(report['month_keys'] == expected_months)
        require(report['algorithm'] == 'calendar-month-v1')
        policy = report['policy']
        require(isinstance(policy, dict) and isinstance(policy['weights'], dict))
        require(set(policy['weights']) == set(WEIGHT_DEFAULTS))
        for value in policy['weights'].values():
            amount(value)
        integer(policy['version'])
        integer(policy['revision'])
        require(integer(policy['average_decimal_places']) <= 4)
        require(policy['algorithm'] == report['algorithm'])
        require(archive.owner_id == classroom.owner_id and archive.policy_id == (policy['version'] or None))
        monthly = policy['monthly']
        require(isinstance(monthly, dict) and set(monthly) == {'base', 'minimum', 'maximum'})
        require(amount(monthly['minimum']) <= amount(monthly['base']))
        if monthly['maximum'] is not None:
            require(amount(monthly['maximum']) >= amount(monthly['base']))
        rows, roster = report['rows'], report['record_roster']
        require(isinstance(rows, list) and isinstance(roster, list))
        roster_by_id = {}
        for row in roster:
            identity(row)
            require(row['id'] not in roster_by_id)
            roster_by_id[row['id']] = row
        seen = set()
        for row in rows:
            identity(row)
            require(row['active'] and row['id'] not in seen)
            seen.add(row['id'])
            require(row['id'] in roster_by_id)
            require(all(row[key] == roster_by_id[row['id']][key] for key in ('number', 'name', 'sex', 'active')))
            amount(row['score'])
            counts(row['counts'])
            require(isinstance(row['months'], list))
            require([month['key'] for month in row['months']] == expected_months)
            totals = dict.fromkeys(WEIGHT_DEFAULTS, 0)
            for month in row['months']:
                amount(month['score'])
                require(isinstance(month['label'], str))
                counts(month['counts'])
                for key in totals:
                    totals[key] += month['counts'][key]
            require(row['counts'] == totals)
        require(seen == {row['id'] for row in roster if row['active']})
        summary = report['summary']
        require(isinstance(summary, dict))
        require(integer(summary['student_count']) == len(rows))
        require(integer(summary['month_count']) == len(expected_months))
        amount(summary['average'])
        require(isinstance(report['activities'], list))
        activity_ids = set()
        allowed_values = {'class': {'normal', 'late', 'absent', 'leave'},
                          'activity': {'normal', 'low', 'mid', 'high'},
                          'discipline': {'normal', 'dlow', 'dmid', 'dhigh'}}
        for activity in report['activities']:
            require(isinstance(activity, dict))
            activity_id = integer(activity['id'], 1)
            require(activity_id not in activity_ids)
            activity_ids.add(activity_id)
            require(activity['kind'] in allowed_values)
            require(isinstance(activity['name'], str) and bool(activity['name']))
            require(isinstance(activity['details'], str))
            require(activity['status'] in ('preview', 'release'))
            integer(activity['revision'], 1)
            day = date.fromisoformat(activity['date'])
            require(final_term.contains(day) and day <= ended_day)
            beijing_iso(activity['time'])
            require(activity['url'] == f'/classes/{classroom.code}/records/{activity_id}/')
            require(isinstance(activity['student_values'], dict))
            for student_id, value in activity['student_values'].items():
                require(isinstance(student_id, str) and student_id.isascii() and student_id.isdigit())
                require(str(int(student_id)) == student_id and int(student_id) in roster_by_id)
                require(value in allowed_values[activity['kind']])
    except (KeyError, ValueError, TypeError, AttributeError, InvalidOperation, OverflowError):
        raise BusinessError('class_archive_unverified', '结束管理快照不完整或与结束依据不一致，需核实后查看。', 409) from None
    return report


def report_is_frozen(report):
    return bool(report.get('frozen') or report.get('snapshot_version') or report.get('frozen_at'))


def report_month_keys(report, term, today=None):
    """Frozen reports own their month range, including an empty frozen roster."""
    if 'month_keys' in report:
        return list(report['month_keys'])
    return [f'{year:04d}-{month:02d}' for year, month in term.months(today)]


def roster_for(classroom, term, include_inactive=False):
    classroom = Class.objects.get(pk=classroom.pk)
    if classroom.archived:
        final_term = ended_term(classroom)
        final_report = validate_ended_archive(classroom)
        if term == final_term:
            return final_report['record_roster'] if include_inactive else final_report['rows']
        snapshot = ClassTerm.objects.filter(inclass=classroom, term_key=term.key).first()
        if snapshot and snapshot.archived_data is not None:
            report = snapshot.archived_data
            return report.get('record_roster', report.get('rows', [])) if include_inactive else report.get('rows', [])
        if term.start > final_term.start:
            return []
    return _roster_versions(classroom, term, include_inactive)


def _roster_versions(classroom, term, include_inactive=False):
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
             'date':a.occurred_on.isoformat(), 'time':beijing_iso(a.time),
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
    classroom = Class.objects.get(pk=classroom.pk)
    ended = term.end <= today
    if classroom.archived:
        final_term = ended_term(classroom)
        final_report = validate_ended_archive(classroom)
        if term == final_term:
            return final_report
        if term.start > final_term.start:
            return {'rows': [], 'summary': {'average': None, 'student_count': 0, 'month_count': 0},
                    'policy': final_report['policy'], 'term_key': term.key,
                    'algorithm': 'calendar-month-v1', 'month_keys': [], 'activities': [],
                    'frozen': True, 'frozen_reason': 'class-ended', 'ended': True}
    archive = ClassTerm.objects.filter(inclass=classroom, term_key=term.key).first() if ended else None
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
            'policy':policy,'term_key':term.key,'algorithm':'calendar-month-v1',
            'month_keys':[f'{year:04d}-{month:02d}' for year,month in months]}
    if ended:
        _store_frozen_report(classroom, term, report, archive=archive)
    return report


def _store_frozen_report(classroom, term, report, archive=None, reason='term-ended'):
    report.update(snapshot_version=1, frozen=True, frozen_reason=reason,
                  frozen_at=beijing_iso(timezone.now()),
                  activities=snapshot_activities(classroom, term),
                  record_roster=_roster_versions(classroom, term, include_inactive=True))
    archive = archive or ClassTerm.objects.filter(inclass=classroom, term_key=term.key).first()
    if archive and archive.archived_data is not None:
        raise BusinessError('class_archive_exists', '该学期已有冻结依据，不能覆盖。', 409)
    if archive:
        archive.archived_data = report
        archive.policy_id = report['policy']['version'] or None
        if not archive.baseline_source:
            archive.baseline_source = 'ended-class-facts' if reason == 'class-ended' else 'frozen-term-facts'
        archive.save(update_fields=['archived_data', 'policy', 'baseline_source'])
    else:
        ClassTerm.objects.create(inclass=classroom, term_key=term.key, owner_id=classroom.owner_id,
            policy_id=report['policy']['version'] or None, archived_data=report,
            baseline_source='ended-class-facts' if reason == 'class-ended' else 'frozen-term-facts')
    return report


def freeze_class_term(classroom, term, today=None):
    """Called only inside the class-ending write transaction before its flag flips."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('Class freezing requires the class-management transaction.')
    today = today or calendar.business_today()
    if calendar.term_for_date(today) != term or classroom.archived:
        raise BusinessError('class_archive_invalid', '仅能结束仍在管理班级的当前学期。', 409)
    report = _class_report(classroom, term, today, None, None)
    return _store_frozen_report(classroom, term, report, reason='class-ended')


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
