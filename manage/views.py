"""Authenticated workspace adapters; business writes live in services."""
import json
from functools import wraps
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db import OperationalError
from django.db.models import Count, Max, Min, Q
from django.http import JsonResponse, HttpResponseRedirect, Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST
from manage.models import Class, ClassTerm, Activity, AuditEvent, Student, PublicClassReportLink, WEIGHT_DEFAULTS
from manage.services import calendar
from manage.services.access import get_class, actor_for, owner_actor, authorize, committee_actor
from manage.services.errors import BusinessError
from manage.services.scoring import class_report, class_top_three, policy_for, roster_for
from manage.services.records import save_record, record_roster
from manage.services.roster import change_roster, create_class
from manage.services.rules import change_rules
from manage.services.committee import sign_in, manage_accounts, account_list, ACTIVE_LIMIT, login_credentials
from manage.services.login_guard import check_login
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.cache import never_cache
from manage.services import monthly
from manage.services.public_reports import change_link, link_details, token_digest
from manage.services.report_snapshots import get_current_report
from manage.services.read_snapshot import read_snapshot_view
from manage.services.class_management import manage_class, management_status


def payload(request):
    if request.content_type=='application/json':
        try:
            value=json.loads(request.body)
            if not isinstance(value,dict):raise ValueError
            return value
        except (ValueError,UnicodeDecodeError):raise BusinessError('invalid_json','请求格式无效。')
    return request.POST.dict()


def success(data):
    return JsonResponse({'ok':True,'data':data})


def guarded(fn):
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        try:return fn(request,*args,**kwargs)
        except BusinessError as exc:
            return failure(request,exc)
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():raise
            return failure(request,BusinessError('database_busy','系统正在处理其他操作，请稍后重试。',503))
    return wrapped


def failure(request,exc):
    error={'code':exc.code,'message':exc.message,'details':exc.details}
    if request.method!='GET' or request.GET.get('format')=='json':
        response=JsonResponse({'ok':False,'error':error},status=exc.status)
    else:
        # Error recovery must not acquire another database lock.
        term=calendar.display_term()
        data={'classes':[],'classroom':None,'actor':{'role':'anonymous','label':'访客'},
            'term':{'key':term.key,'label':term.label},'terms':[],'readonly':True,'error':error}
        response=render(request,'workspace/page.html',{'page':'error','data':data},status=exc.status)
    if exc.status==503:response['Retry-After']='1'
    if exc.code=='authorization_rate_limited':response['Retry-After']=str(exc.details.get('retry_after',1800))
    return response


def class_item(c):
    return {'id':c.pk,'code':c.code,'name':c.classname,'revision':c.revision,'archived':c.archived}


def actor_item(actor):
    return {'role':actor.role,'label':actor.label,'id':actor.user_id,
            'username':actor.username,'display_name':actor.display_name,'expires_at':actor.expires_at}


def common(request,classroom=None,term=None,actor=None):
    today=calendar.business_today()
    term=term or calendar.display_term(today)
    classes=[]
    identity={'role':'anonymous','label':'访客','id':None,'expires_at':None}
    if request.user.is_authenticated:
        classes=list(Class.objects.filter(owner=request.user).order_by('classname','id'))
        identity=actor_item(owner_actor(request))
    else:
        try:
            account,granted=committee_actor(request)
            classes=[account.inclass]
            identity=actor_item(granted)
        except BusinessError:
            pass
    if actor:identity=actor_item(actor)
    readonly=calendar.term_for_date(today)!=term or bool(classroom and (classroom.archived or classroom.legacy_pending))
    if today.month==8:reason='8 月暂停业务录入，仅可查看历史数据。'
    elif classroom and classroom.legacy_pending:reason='旧库迁移基线尚待核对。'
    elif classroom and classroom.archived:reason='班级已归档，仅可查看。'
    elif readonly:reason='已结束或未开始学期 · 只读'
    else:reason=''
    return {'classes':[class_item(c) for c in classes],'classroom':class_item(classroom) if classroom else None,
        'actor':identity,'term':{'key':term.key,'label':term.label},'today':today.isoformat(),
        'start_date':term.start.isoformat(),'readonly':readonly,'historical':term.end <= today,'readonly_reason':reason,
        'readonly_label':'数据待核实' if classroom and classroom.legacy_pending else '班级已归档' if classroom and classroom.archived else '历史快照 · 只读' if term.end <= today else '非当前学期 · 只读',
        'terms':term_options(classroom,today,classes),'user_label':identity.get('username',''),
        'login_role':'committee' if request.GET.get('role')=='committee' else 'owner'}


def class_metrics(classroom,student_count,term,report=None):
    if term.end <= calendar.business_today():
        activities = (report or class_report(classroom,term)).get('activities')
        if activities is None:
            return {'student_count':student_count,'activity_count':None,'last_activity_at':None,
                    'records_unavailable':True}
        return {'student_count':student_count,'activity_count':len(activities),
                'last_activity_at':max((a['time'] for a in activities),default=None)}
    stats=classroom.activity_set.filter(occurred_on__gte=term.start,occurred_on__lt=term.end).aggregate(
        activity_count=Count('id'),last_activity_at=Max('time'))
    stats['last_activity_at']=stats['last_activity_at'].isoformat(timespec='seconds') if stats['last_activity_at'] else None
    return {'student_count':student_count,**stats}


def term_options(classroom,today,classes=None):
    term=calendar.display_term(today)
    authorized=[classroom] if classroom else list(classes or [])
    starts=[c.started_on for c in authorized]
    if authorized:
        ids=[c.pk for c in authorized]
        earliest_activity=Activity.objects.filter(inclass_id__in=ids).aggregate(day=Min('occurred_on'))['day']
        if earliest_activity:starts.append(earliest_activity)
        for key in ClassTerm.objects.filter(inclass_id__in=ids).values_list('term_key',flat=True):
            starts.append(calendar.parse_term(key).start)
    start=calendar.term_for_date(min(starts)) if starts else term
    if start is None:start=calendar.term_for_date(calendar.previous_term(term).start)
    start=start or calendar.display_term(today)
    result=[]
    while term.start>=start.start and len(result)<80:
        result.append({'key':term.key,'label':term.label,'readonly':calendar.term_for_date(today)!=term})
        term=calendar.previous_term(term)
    return result


def selected_term(request):
    key=request.GET.get('term')
    return calendar.parse_term(key) if key else calendar.display_term()


def page(request,name,data,status=200):
    if request.GET.get('format')=='json':return success(data)
    return render(request,'workspace/page.html',{'page':name,'data':data},status=status)


def class_context(request,code,owner_only=False,term=None):
    classroom=get_class(code)
    try:actor=actor_for(request,classroom,owner_only)
    except BusinessError as exc:
        if exc.status==401 and not owner_only and request.method=='GET' and request.GET.get('format')!='json':
            data=common(request)
            data['classroom']=None
            data['classes']=[]
            data['actor']={'role':'anonymous','label':'访客','expires_at':None}
            return classroom,None,page(request,'login',data)
        raise
    return classroom,actor,common(request,classroom,term or selected_term(request),actor)


def record_item(activity):
    return {'id':activity.pk,'name':activity.name,'details':activity.details,'kind':activity.activity_type,'date':activity.occurred_on.isoformat(),
            'time':activity.time.isoformat(timespec='minutes'),
            'revision':activity.revision,'status':activity.status,'student_count':getattr(activity,'student_count',None),
            'url':f'/classes/{activity.inclass.code}/records/{activity.pk}/'}


def counted_records(query, weights):
    positive_activity=[key for key in ('low','mid','high') if float(weights.get(key,0))>0]
    positive_discipline=[key[1:] for key in ('dlow','dmid','dhigh') if float(weights.get(key,0))>0]
    included=(Q(activity_type='class',report__status__in=('absent','late','leave')) |
              Q(activity_type='activity',report__level__in=positive_activity) |
              Q(activity_type='discipline',report__discipline__in=positive_discipline))
    return query.annotate(student_count=Count('report',filter=included)).order_by('-occurred_on','-id')


def archived_record_count(record, weights):
    values=record.get('student_values',{}).values()
    kind=record.get('kind')
    if kind=='class':return sum(value in ('absent','late','leave') for value in values)
    if kind=='activity':return sum(value in ('low','mid','high') and float(weights.get(value,0))>0 for value in values)
    return sum(value in ('dlow','dmid','dhigh') and float(weights.get(value,0))>0 for value in values)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET','POST'])
@read_snapshot_view
def index(request):
    if request.method=='POST':return success(create_class(request,payload(request)))
    term=selected_term(request)
    data=common(request,term=term)
    if data['actor']['role']=='anonymous':
        return page(request,'login',data)
    data['class_summaries']=[]
    data['dashboard_students']=[]
    for item in data['classes']:
        c=get_class(item['code'])
        if c.legacy_pending:
            data['class_summaries'].append({**item,'pending':True})
        else:
            report=class_report(c,term)
            if 'policy' not in data:data['policy']=report['policy']
            data['class_summaries'].append({**item,**class_metrics(c,report['summary']['student_count'],term,report)})
            data['dashboard_students'].extend({**row,'class_id':c.pk,'class_name':c.classname,'class_code':c.code,
                'score_base':monthly.score_base(report),
                'average_decimal_places':report.get('policy',{}).get('average_decimal_places',2)}
                for row in report['rows'])
    return page(request,'dashboard',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET','POST'])
@sensitive_post_parameters('password')
@sensitive_variables()
def user_login(request):
    if request.method=='POST':
        data=payload(request)
        role=data.get('role','owner')
        if role=='committee':return success(sign_in(request,data))
        if role!='owner':raise BusinessError('invalid_login_role','请选择班主任或班委登录。')
        username,password=data.get('username'),data.get('password')
        if not isinstance(username,str) or not isinstance(password,str) or len(username)>150 or len(password)>128:
            raise BusinessError('invalid_credentials','用户名或密码错误。',401)
        user=check_login('owner',username,lambda:authenticate(request,username=username,password=password))
        request.session.pop('committee_auth',None)
        request.session.pop('class_grants',None)
        login(request,user)
        return success({'url':'/'})
    return page(request,'login',common(request))


@guarded
@require_POST
def user_logout(request):
    logout(request)
    return success({'url':'/'})


@guarded
@require_POST
def class_authorize(request,code):
    raise BusinessError('legacy_authorization_retired','共享班级授权已停用，请使用班委账号登录。',410)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET'])
@read_snapshot_view
def class_info(request,code):
    classroom,actor,data=class_context(request,code)
    if actor is None:return data
    term=selected_term(request)
    report=class_report(classroom,term)
    data.update(students=report['rows'],top_three=class_top_three(report['rows']),
        summary=class_metrics(classroom,report['summary']['student_count'],term,report),
        policy=report['policy'],monthly_overview=monthly.term_monthly_overview(classroom,term,report=report))
    data['august_years']=[day.year for day in Activity.objects.filter(inclass=classroom,
        occurred_on__month=8).dates('occurred_on','year',order='DESC')]
    data['public_report']=link_details(request,classroom)
    kind=request.GET.get('kind','')
    name=request.GET.get('name','').strip()[:64]
    day=request.GET.get('date','').strip()
    if day:
        from datetime import date
        try:day=date.fromisoformat(day).isoformat()
        except ValueError:raise BusinessError('invalid_record_date','查询日期无效。')
    data['record_filters']={'kind':kind,'name':name,'date':day}
    if term.end <= calendar.business_today():
        data['records_unavailable']='activities' not in report
        records=[{**record,'student_count':archived_record_count(record,report['policy']['weights'])}
                 for record in report.get('activities',[])]
        if kind in ('class','activity','discipline'):records=[r for r in records if r['kind']==kind]
        if name:records=[r for r in records if name.casefold() in r['name'].casefold()]
        if day:records=[r for r in records if r['date']==day]
    else:
        records=counted_records(classroom.activity_set.select_related('inclass').filter(
            occurred_on__gte=term.start,occurred_on__lt=term.end),report['policy']['weights'])
        if kind in ('class','activity','discipline'):records=records.filter(activity_type=kind)
        if name:records=records.filter(name__icontains=name)
        if day:records=records.filter(occurred_on=day)
    listing=Paginator(records,30).get_page(request.GET.get('page',1))
    data['records']=list(listing) if term.end <= calendar.business_today() else [record_item(r) for r in listing]
    data['pagination']={'page':listing.number,'pages':listing.paginator.num_pages,'count':listing.paginator.count}
    return page(request,'class',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET','POST'])
@read_snapshot_view
def record_page(request,code,record_id=None):
    if request.method=='POST':return success(save_record(request,code,payload(request),record_id))
    classroom,actor,data=class_context(request,code)
    if actor is None:return data
    activity=None
    archived=None
    archive_term=None
    if record_id:
        if request.GET.get('term'):
            candidate=selected_term(request)
            if candidate.end <= calendar.business_today():
                historical=class_report(classroom,candidate)
                archived=next((a for a in historical.get('activities',[]) if a['id']==record_id),None)
                if archived:archive_term=candidate
        if archived is None:
            from manage.models import ClassTerm
            for snapshot in ClassTerm.objects.filter(inclass=classroom,archived_data__isnull=False):
                candidate=calendar.parse_term(snapshot.term_key)
                if candidate.end > calendar.business_today():continue
                archived=next((a for a in snapshot.archived_data.get('activities',[]) if a['id']==record_id),None)
                if archived:
                    archive_term=candidate
                    break
        if archived is None:
            try:activity=Activity.objects.get(pk=record_id,inclass=classroom)
            except Activity.DoesNotExist:raise BusinessError('not_found','记录不存在或不属于本班。',404)
    august_legacy=bool(activity and activity.occurred_on.month==8)
    term=archive_term or (calendar.term_for_date(activity.occurred_on) if activity else calendar.display_term())
    term=term or calendar.display_term()
    data=common(request,classroom,term,actor)
    if august_legacy:
        data.update(readonly=True,historical=True,readonly_label='8 月旧记录 · 只读',
                    readonly_reason='8 月不属于计分学期；仅展示当时明确保存的个人记录，不推算完整名单或成绩。',
                    record_return_url=f'/classes/{classroom.code}/august/?year={activity.occurred_on.year}')
    if classroom.legacy_pending:raise BusinessError('legacy_baseline_pending','旧库记录尚待迁移核对。',409)
    if term.end <= calendar.business_today() and record_id and not august_legacy:
        report=class_report(classroom,term)
        archived=next((a for a in report.get('activities',[]) if a['id']==record_id),None)
        if archived is None:
            raise BusinessError('historical_record_unavailable','该记录尚无经核实的历史快照。',409)
        students=report.get('record_roster',report['rows'])
        values={r['id']:(archived['student_values'].get(str(r['id'])) or 'normal') for r in students}
        record=archived
    elif august_legacy:
        facts=list(activity.report_set.select_related('student').order_by('student__number','student_id'))
        students=[{'id':r.student_id,'name':r.student.name,'number':r.student.number,'sex':r.student.sex} for r in facts]
        values={r.student_id:(r.status if activity.activity_type=='class' else r.level if activity.activity_type=='activity' else 'd'+r.discipline) for r in facts}
        record=record_item(activity)
    else:
        students=record_roster(classroom,term,activity)
        values={r['id']:'normal' for r in students}
        if activity:
            for r in activity.report_set.all():
                values[r.student_id]=r.status if activity.activity_type=='class' else r.level if activity.activity_type=='activity' else 'd'+r.discipline
                if values[r.student_id] in ('present','none','dnone'):values[r.student_id]='normal'
        record=record_item(activity) if activity else {'id':None,'kind':'class','name':'','details':'','date':data['today'],'revision':0}
    data['students']=students
    data['record']={**record,
        'students':[{'id':sid,'value':value} for sid,value in values.items()]}
    return page(request,'record',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET'])
@read_snapshot_view
def august_records(request,code):
    classroom,actor,data=class_context(request,code)
    if actor is None:return data
    years=list(Activity.objects.filter(inclass=classroom,occurred_on__month=8)
        .dates('occurred_on','year',order='DESC'))
    if not years:raise BusinessError('not_found','本班没有 8 月旧记录。',404)
    try:year=int(request.GET.get('year',years[0].year))
    except (ValueError,TypeError):raise BusinessError('invalid_year','年份无效。')
    if year not in [day.year for day in years]:raise BusinessError('not_found','该年没有 8 月旧记录。',404)
    records=Activity.objects.filter(inclass=classroom,occurred_on__year=year,occurred_on__month=8).order_by('-occurred_on','-id')
    listing=Paginator(records,30).get_page(request.GET.get('page',1))
    data.update(readonly=True,historical=True,readonly_label='8 月旧记录 · 只读',
        readonly_reason='8 月不属于计分学期；记录单独展示，不参与任何学期成绩。',
        august_year=year,august_years=[day.year for day in years],
        records=[record_item(r) for r in listing],
        pagination={'page':listing.number,'pages':listing.paginator.num_pages,'count':listing.paginator.count})
    return page(request,'august',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET'])
@read_snapshot_view
def student_detail(request,code,student_id):
    classroom,actor,data=class_context(request,code)
    if actor is None:return data
    term=selected_term(request)
    month_start=monthly.selected_month(request.GET['month'],term) if 'month' in request.GET else None
    report=class_report(classroom,term)
    student=next((s for s in report['rows'] if s['id']==student_id),None)
    if student is None:raise BusinessError('not_found','该学期名单中没有此学生。',404)
    data['student']=student
    data['months']=student.get('months',[])
    data['policy']=report['policy']
    data['selected_month']=monthly.student_month(classroom,student_id,month_start,report) if month_start else None
    data['term_detail_url']=f'/classes/{classroom.code}/students/{student_id}/?term={term.key}'
    kind=request.GET.get('kind','')
    name=request.GET.get('name','').strip()[:64]
    day=request.GET.get('date','').strip()
    if day:
        from datetime import date
        try:day=date.fromisoformat(day).isoformat()
        except ValueError:raise BusinessError('invalid_record_date','查询日期无效。')
    data['record_filters']={'kind':kind,'name':name,'date':day}
    historical=term.end <= calendar.business_today()
    if historical:
        data['records_unavailable']='activities' not in report
        records=[{**a,'value':a['student_values'][str(student_id)]} for a in report.get('activities',[])
                 if str(student_id) in a['student_values']
                 and (not month_start or month_start.isoformat()<=a['date']<monthly.next_month(month_start).isoformat())]
        if kind in ('class','activity','discipline'):records=[r for r in records if r['kind']==kind]
        if name:records=[r for r in records if name.casefold() in r['name'].casefold()]
        if day:records=[r for r in records if r['date']==day]
    else:
        records=classroom.activity_set.select_related('inclass').filter(report__student_id=student_id,
            occurred_on__gte=month_start or term.start,
            occurred_on__lt=monthly.next_month(month_start) if month_start else term.end)
        if kind in ('class','activity','discipline'):records=records.filter(activity_type=kind)
        if name:records=records.filter(name__icontains=name)
        if day:records=records.filter(occurred_on=day)
    listing=Paginator(records,30).get_page(request.GET.get('page',1))
    if historical:
        data['records']=list(listing)
    else:
        student_values={r.activity_id:r for r in Student.objects.get(pk=student_id).report_set.filter(activity_id__in=[a.pk for a in listing])}
        data['records']=[]
        for a in listing:
            fact=student_values[a.pk]
            value=fact.status if a.activity_type=='class' else fact.level if a.activity_type=='activity' else 'd'+fact.discipline
            data['records'].append({**record_item(a),'value':value})
    data['pagination']={'page':listing.number,'pages':listing.paginator.num_pages,'count':listing.paginator.count}
    return page(request,'student',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET','POST'])
@read_snapshot_view
def roster_page(request,code):
    if request.method=='POST':return success(change_roster(request,code,payload(request)))
    classroom,actor,data=class_context(request,code,owner_only=True)
    term=selected_term(request)
    data['students']=class_report(classroom,term)['rows'] if term.end <= calendar.business_today() else roster_for(classroom,term)
    data['committee_accounts']=account_list(classroom)
    data['committee_limit']=ACTIVE_LIMIT
    data['committee_prefix']=classroom.committee_prefix
    current=calendar.term_for_date(calendar.business_today())
    data['class_management']=management_status(classroom,current) if current and term==current else None
    return page(request,'roster',data)


@guarded
@require_POST
def class_management(request,code):
    return success(manage_class(request,code,payload(request)))


@guarded
@require_http_methods(['GET','POST'])
@sensitive_post_parameters('password')
@sensitive_variables()
def committee_accounts(request,code):
    if request.method=='POST':return success(manage_accounts(request,code,payload(request)))
    classroom=get_class(code)
    actor_for(request,classroom,owner_only=True)
    return success({'accounts':account_list(classroom),'limit':ACTIVE_LIMIT,'prefix':classroom.committee_prefix})


@never_cache
@guarded
@require_POST
@sensitive_variables()
def committee_credentials(request,code):
    return success(login_credentials(request,code,payload(request)))


@guarded
@require_POST
def import_students(request,code):
    from manage.services.student_import import parse_student_workbook
    classroom=get_class(code)
    actor_for(request,classroom,owner_only=True)
    # Refuse August/history/stale rosters before parsing an uploaded workbook.
    from manage.services.access import require_ready
    from manage.services.writes import require_revision
    require_ready(classroom)
    calendar.writable_term(request.POST.get('term_key'))
    try:revision=int(request.POST.get('revision',''))
    except (ValueError,TypeError):raise BusinessError('invalid_revision','名单版本无效。')
    require_revision(classroom.revision,revision)
    parsed=parse_student_workbook(request.FILES.get('file'))
    preview=change_roster(request,code,{'action':'preview_add','students_text':parsed['students_text'],
        'term_key':request.POST.get('term_key'),'revision':revision})
    return success({**preview,'students_text':parsed['students_text']})


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET','POST'])
@read_snapshot_view
def rules_page(request):
    actor=owner_actor(request)
    if request.method=='POST':return success(change_rules(request,payload(request)))
    term=selected_term(request)
    data=common(request,term=term,actor=actor)
    data['policy']=policy_for(actor.user_id,term)
    return page(request,'rules',data)


@ensure_csrf_cookie
@guarded
@require_http_methods(['GET'])
@read_snapshot_view
def events_page(request,code):
    classroom,actor,data=class_context(request,code,owner_only=True)
    query=AuditEvent.objects.filter(inclass=classroom)
    kind=request.GET.get('kind')
    if kind:query=query.filter(kind=kind)
    listing=Paginator(query,30).get_page(request.GET.get('page',1))
    data['events']=[{'id':e.pk,'time':e.created_at.isoformat(timespec='seconds'),'actor_label':(('班主任' if e.actor_role=='owner' else '班委') + (' · '+e.actor_label if e.actor_label else '（旧共享授权）' if e.actor_role=='committee' else '')),
        'actor_id':e.actor_id,
        'kind':e.kind,'summary':e.summary,'affected_count':e.affected_count,'source':'网页','revision':e.revision} for e in listing]
    data['pagination']={'page':listing.number,'pages':listing.paginator.num_pages,'count':listing.paginator.count}
    return page(request,'events',data)


@never_cache
@guarded
@require_http_methods(['GET','POST'])
def public_report_settings(request,code):
    classroom=get_class(code)
    actor_for(request,classroom)
    if request.method=='POST':return success(change_link(request,classroom,payload(request).get('action')))
    return success(link_details(request,classroom))


@never_cache
@guarded
@require_http_methods(['GET'])
def public_report(request,token):
    # The URL itself is the only credential. Never accept a class or term selector.
    if len(token)!=43 or not all(char.isalnum() or char in '-_' for char in token):
        raise Http404
    link=PublicClassReportLink.objects.select_related('inclass').filter(token_digest=token_digest(token),active=True).first()
    if not link:
        raise Http404
    snapshot=get_current_report(link.inclass)
    response=render(request,'workspace/public_report.html',{
        **snapshot.payload, 'generated_at':snapshot.generated_at})
    response['Cache-Control']='no-store, private'
    response['Referrer-Policy']='no-referrer'
    response['X-Robots-Tag']='noindex, nofollow, noarchive'
    return response


@guarded
@require_http_methods(['GET','POST'])
def legacy_endpoint(request):
    if request.method=='POST':
        raise BusinessError('legacy_endpoint_retired','旧版写入入口已停用，请刷新并使用新版工作台。',410)
    return HttpResponseRedirect('/')
