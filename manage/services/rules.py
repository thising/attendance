from decimal import Decimal, InvalidOperation
from datetime import date
from manage.models import Class, OwnerScoringSettings, OwnerTermPolicy, ScoringPolicyVersion, WEIGHT_DEFAULTS
from . import calendar
from .access import owner_actor, require_ready
from .errors import BusinessError
from .scoring import class_report, policy_for, money
from .writes import atomic_write, once, event, require_revision
from .report_snapshots import mark_report_dirty


def ensure_default_policy(owner_id):
    settings,_=OwnerScoringSettings.objects.get_or_create(owner_id=owner_id)
    if settings.default_policy_id is None:
        baseline=ScoringPolicyVersion.objects.create(owner_id=owner_id,effective_term_start=date(1900,2,1),**WEIGHT_DEFAULTS)
        settings.default_policy=baseline
        settings.save(update_fields=['default_policy'])
    return settings


def validate_weights(data):
    if not isinstance(data,dict) or set(data)!=set(WEIGHT_DEFAULTS):
        raise BusinessError('invalid_weights','请完整填写九项评分分值。')
    result={}
    for key,value in data.items():
        try:
            if isinstance(value,bool):raise ValueError
            number=Decimal(str(value))
            if not number.is_finite() or number<0 or number>Decimal('999999.99') or number % Decimal('0.5'):
                raise ValueError
            result[key]=money(number)
        except (InvalidOperation,ValueError,TypeError):
            raise BusinessError('invalid_weights','分值需为非负数，且每次以0.5分递增。',details={'field':key})
    return result


def validate_monthly(data):
    if not isinstance(data,dict) or set(data) != {'base','minimum','maximum'}:
        raise BusinessError('invalid_monthly','请完整填写月度基础分、最低分和最高分；无上限时最高分为空。')
    result={}
    for key,value in data.items():
        if key=='maximum' and value is None:
            result[key]=None
            continue
        try:
            if isinstance(value,bool):raise ValueError
            number=Decimal(str(value))
            if not number.is_finite() or number<0 or number>Decimal('999999.99') or number % Decimal('0.5'):
                raise ValueError
            result[key]=money(number if number else Decimal('0'))
        except (InvalidOperation,ValueError,TypeError):
            raise BusinessError('invalid_monthly','月度分值需为非负数，且每次以0.5分递增。',
                                details={'field':'monthly.'+key})
    if Decimal(result['minimum'])>Decimal(result['base']):
        raise BusinessError('invalid_monthly','最低分不能高于基础分。',details={'field':'monthly.minimum'})
    if result['maximum'] is not None and Decimal(result['base'])>Decimal(result['maximum']):
        raise BusinessError('invalid_monthly','最高分不能低于基础分。',details={'field':'monthly.maximum'})
    return result


def validate_average_decimal_places(value):
    if isinstance(value, bool) or type(value) not in (int, str) or str(value) not in ('0', '1', '2', '3', '4'):
        raise BusinessError('invalid_average_decimal_places', '平均分显示位数须为0到4位整数。',
                            details={'field':'average_decimal_places'})
    return int(value)


def preview_rules(owner_id,current,weights,monthly=None):
    affected=[]
    total=0
    for classroom in Class.objects.filter(owner_id=owner_id):
        if classroom.legacy_pending:
            raise BusinessError('legacy_baseline_pending','旧库历史基线尚待核对。',409)
        before=class_report(classroom,current)
        after=class_report(classroom,current,weights=weights,monthly=monthly)
        total+=before['summary']['student_count']
        old_scores={row['id']:Decimal(row['score']) for row in before['rows']}
        differences=[abs(Decimal(row['score'])-old_scores[row['id']]) for row in after['rows']]
        affected.append({'id':classroom.pk,'name':classroom.classname,
            'student_count':len(differences),'changed_count':sum(value!=0 for value in differences),
            'max_absolute_change':format(max(differences,default=Decimal('0')),'.2f')})
    return {'classes':affected,'student_count':total}


@atomic_write
def change_rules(request,payload):
    actor=owner_actor(request)
    current=calendar.writable_term(payload.get('term_key'))
    weights=validate_weights(payload.get('weights'))
    monthly=validate_monthly(payload['monthly']) if 'monthly' in payload else None
    current_policy=policy_for(actor.user_id,current)
    average_decimal_places=validate_average_decimal_places(payload['average_decimal_places']) if 'average_decimal_places' in payload else current_policy['average_decimal_places']
    if payload.get('action')=='preview':
        require_revision(current_policy['revision'],payload.get('revision'))
        effective_monthly=current_policy['monthly'] if monthly is None else monthly
        return {'preview':preview_rules(actor.user_id,current,weights,effective_monthly)}
    def apply():
        settings=ensure_default_policy(actor.user_id)
        require_revision(settings.revision,payload.get('revision'))
        effective_monthly=policy_for(actor.user_id,current)['monthly'] if monthly is None else monthly
        preview=preview_rules(actor.user_id,current,weights,effective_monthly)
        policy=ScoringPolicyVersion.objects.create(owner_id=actor.user_id,effective_term_start=current.start,**weights,
            base_score=effective_monthly['base'],minimum_score=effective_monthly['minimum'],maximum_score=effective_monthly['maximum'],
            average_decimal_places=average_decimal_places)
        settings.default_policy=policy
        settings.revision+=1
        settings.save(update_fields=['default_policy','revision'])
        OwnerTermPolicy.objects.update_or_create(owner_id=actor.user_id,term_key=current.key,defaults={'policy':policy})
        for classroom in Class.objects.filter(owner_id=actor.user_id):
            event(actor,classroom,'rules_updated','更新负责人评分规则，对当前及未来学期生效',
                  classroom.student_set.filter(active=True).count(),revision=settings.revision)
            mark_report_dirty(classroom)
        return {'policy':{'revision':settings.revision,'weights':policy.weights,'monthly':policy.monthly,
                          'average_decimal_places':policy.average_decimal_places,
                          'version':policy.pk,'algorithm':policy.algorithm},'preview':preview}
    return once(actor,None,f'rules:{actor.user_id}',payload,apply)
