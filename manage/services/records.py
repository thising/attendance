from datetime import date
from manage.models import Activity, Report, ClassDailyUsage, Class
from . import calendar
from .access import actor_for, get_class, require_ready
from .errors import BusinessError
from .scoring import roster_for, rebuild_months
from .report_snapshots import mark_report_dirty
from .writes import atomic_write, once, event, require_revision

VALUES = {'class': ('normal','late','absent','leave'), 'activity': ('normal','low','mid','high'),
          'discipline': ('normal','dlow','dmid','dhigh')}


def record_roster(classroom, term, activity=None):
    rows = roster_for(classroom,term,include_inactive=True)
    existing = set(activity.report_set.values_list('student_id',flat=True)) if activity else set()
    return [r for r in rows if r['active'] or r['id'] in existing]


def validate_record(data, roster, existing=None):
    if not isinstance(data,dict):
        raise BusinessError('invalid_record','记录格式无效。')
    kind, name = data.get('kind'), data.get('name')
    details = data.get('details', existing.details if existing else '')
    if not isinstance(kind,str) or kind not in VALUES or not isinstance(name,str) or not 1 <= len(name.strip()) <= 64:
        raise BusinessError('invalid_record','请选择有效类型并填写1–64字记录名称。')
    if not isinstance(details,str) or len(details)>500:
        raise BusinessError('invalid_record_details','详情最多500字。')
    try:occurred_on=date.fromisoformat(data.get('date',''))
    except (ValueError,TypeError):raise BusinessError('invalid_record_date','发生日期无效。')
    items = data.get('students')
    if not isinstance(items,list) or len(items)>1000:
        raise BusinessError('invalid_roster','本次名单格式无效或超过1000人。')
    values = {}
    for item in items:
        if not isinstance(item,dict) or type(item.get('id')) is not int or item.get('value') not in VALUES[kind]:
            raise BusinessError('invalid_student_value','名单中有无效的学生或状态。')
        if item['id'] in values:
            raise BusinessError('duplicate_student','同一次记录中学生不能重复。')
        values[item['id']] = item['value']
    if set(values) != {r['id'] for r in roster}:
        raise BusinessError('roster_changed','名单已变化或包含其他班级学生，请重新核对完整名单。',409)
    return kind,name.strip(),details.strip(),occurred_on,values


@atomic_write
def save_record(request,code,payload,record_id=None):
    classroom=get_class(code)
    deleting=payload.get('action')=='delete'
    actor=actor_for(request,classroom,owner_only=deleting)
    require_ready(classroom)
    current=calendar.writable_term(payload.get('term_key'))
    scope=f'record:{classroom.pk}:{"delete" if deleting else "edit" if record_id else "create"}:{record_id or 0}'
    def apply():
        activity=None
        if record_id:
            try:activity=Activity.objects.get(pk=record_id,inclass=classroom)
            except Activity.DoesNotExist:raise BusinessError('not_found','记录不存在或不属于本班。',404)
            if not current.contains(activity.occurred_on):
                raise BusinessError('term_read_only','已结束学期的记录仅可查看。',403)
            require_revision(activity.revision,payload.get('revision'))
        elif deleting:
            raise BusinessError('not_found','请选择要删除的记录。',404)
        if deleting:
            ids=set(activity.report_set.values_list('student_id',flat=True))
            day,pk=activity.occurred_on,activity.pk
            activity.delete()
            rebuild_months(classroom,ids,{(day.year,day.month)})
            event(actor,classroom,'record_deleted',
                  f'删除{dict(Activity.enum_activity_type).get(activity.activity_type)}记录「{activity.name}」（{day.isoformat()}）',
                  len(ids),pk,activity.revision)
            mark_report_dirty(classroom)
            return {'id':pk,'deleted':True,'url':f'/classes/{code}/'}
        try:
            require_revision(classroom.revision,payload.get('roster_revision'))
        except BusinessError as exc:
            if exc.code!='revision_conflict':raise
            raise BusinessError('roster_changed','学生名单已更新，请刷新页面并重新点名后提交。',409) from None
        roster=record_roster(classroom,current,activity)
        kind,name,details,occurred_on,values=validate_record(payload.get('record'),roster,activity)
        calendar.writable_term(payload.get('term_key'),occurred_on)
        old_ids=set(activity.report_set.values_list('student_id',flat=True)) if activity else set()
        months={(occurred_on.year,occurred_on.month)}
        if activity:
            if kind != activity.activity_type:
                raise BusinessError('record_kind_immutable','已有记录的类型不能改变，请分别录入。')
            months.add((activity.occurred_on.year,activity.occurred_on.month))
            activity.name=name
            activity.details=details
            activity.occurred_on=occurred_on
            activity.revision+=1
            activity.save(update_fields=['name','details','occurred_on','revision'])
            activity.report_set.all().delete()
        else:
            require_revision(0,payload.get('revision',0))
            usage,_=ClassDailyUsage.objects.get_or_create(inclass=classroom,day=calendar.business_today())
            if actor.role=='committee' and usage.created_count>=30:
                raise BusinessError('daily_quota_exceeded','本班今日新增记录已达30条，请联系负责人。',429)
            usage.created_count+=1
            usage.save(update_fields=['created_count'])
            activity=Activity.objects.create(inclass=classroom,name=name,details=details,activity_type=kind,occurred_on=occurred_on)
        reports=[]
        for sid,value in values.items():
            if value=='normal':continue
            field='status' if kind=='class' else 'level' if kind=='activity' else 'discipline'
            reports.append(Report(activity=activity,student_id=sid,**{field:value[1:] if kind=='discipline' else value}))
        Report.objects.bulk_create(reports)
        rebuild_months(classroom,old_ids|{r.student_id for r in reports},months)
        event(actor,classroom,'record_updated' if record_id else 'record_created',
              f'{"修改" if record_id else "新增"}{dict(Activity.enum_activity_type)[kind]}记录「{name}」（{occurred_on.isoformat()}）',
              len(values),activity.pk,activity.revision)
        mark_report_dirty(classroom)
        return {'id':activity.pk,'revision':activity.revision,'url':f'/classes/{code}/records/{activity.pk}/'}
    return once(actor,classroom,scope,payload,apply)
