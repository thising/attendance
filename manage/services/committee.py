"""Named, single-class committee accounts; no shared-code authorization."""
import re
import time
from django.contrib.auth import logout
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from manage.models import CommitteeAccount
from .access import actor_for, get_class, GRANT_SECONDS, validate_class_password
from .errors import BusinessError
from .login_guard import check_login
from .writes import atomic_write, once, event, require_revision
from .credential_store import store_password, read_password

ACTIVE_LIMIT = 5


def account_list(classroom):
    return [{'id':a.pk, 'username':a.username, 'display_name':a.display_name,
             'active':a.active, 'revision':a.revision,'credentials_available':bool(a.credential_ciphertext)}
            for a in classroom.committee_accounts.order_by('-active','id')]


def account_result(classroom):
    accounts = account_list(classroom)
    return {'accounts':accounts, 'active_count':sum(a['active'] for a in accounts), 'limit':ACTIVE_LIMIT,
            'prefix':classroom.committee_prefix}


def display_name(value):
    if not isinstance(value,str) or len(value.strip()) > 40:
        raise BusinessError('invalid_display_name','班委姓名最多40字。')
    return value.strip()


@sensitive_variables()
@atomic_write
def manage_accounts(request, code, payload):
    classroom = get_class(code)
    actor = actor_for(request,classroom,owner_only=True)
    action = payload.get('action')
    if action not in ('create','update','reset_password','deactivate','activate'):
        raise BusinessError('invalid_action','不支持此班委账号操作。')

    def apply():
        if action == 'create':
            username = payload.get('username')
            if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9._-]{3,40}',username):
                raise BusinessError('invalid_username','班委用户名需为3–40位字母、数字、点、下划线或短横线。')
            # The fixed class prefix is a public namespace, never an authorization token.
            username = classroom.committee_prefix + '.' + username.lower()
            if CommitteeAccount.objects.filter(username=username).exists():
                raise BusinessError('username_taken','该班委用户名已被使用，请换一个。',409)
            if classroom.committee_accounts.filter(active=True).count() >= ACTIVE_LIMIT:
                raise BusinessError('committee_limit','每班最多启用5个班委账号，请先停用不再使用的账号。',409)
            password = payload.get('password')
            validate_class_password(password)
            account = CommitteeAccount(inclass=classroom,username=username,
                                       display_name=display_name(payload.get('display_name','')))
            account.set_password(password)
            account.save()
            store_password(account,password)
            summary = f'指定班委账号「{account.username}」，仅可访问本班'
        else:
            if type(payload.get('id')) is not int:
                raise BusinessError('invalid_account','班委账号编号无效。')
            try:
                account = classroom.committee_accounts.get(pk=payload['id'])
            except CommitteeAccount.DoesNotExist:
                raise BusinessError('not_found','班委账号不存在或不属于本班。',404)
            require_revision(account.revision,payload.get('revision'))
            if action == 'activate':
                if not account.active and classroom.committee_accounts.filter(active=True).count() >= ACTIVE_LIMIT:
                    raise BusinessError('committee_limit','每班最多启用5个班委账号。',409)
                account.active = True
                account.auth_version += 1
                summary = f'启用班委账号「{account.username}」'
            elif action == 'deactivate':
                account.active = False
                account.auth_version += 1
                summary = f'停用班委账号「{account.username}」，原登录已失效'
            elif action == 'reset_password':
                password = payload.get('password')
                validate_class_password(password)
                account.set_password(password)
                account.auth_version += 1
                summary = f'重置班委账号「{account.username}」密码，原登录已失效'
            else:
                account.display_name = display_name(payload.get('display_name',''))
                summary = f'更新班委账号「{account.username}」的显示姓名'
            account.revision += 1
            account.save()
            if action == 'reset_password':
                store_password(account,password)
        event(actor,classroom,'committee_'+action,summary,object_id=account.pk,revision=account.revision)
        return {**account_result(classroom), 'receipt':{'id':account.pk,'username':account.username,
                    'revision':account.revision,'credentials_current':True}}
    result = once(actor,classroom,f'committee:{classroom.pk}:{action}',payload,apply)
    # A lost response can be retried after another reset. Never imply that the old
    # submitted password is still current; no plaintext password enters the receipt.
    if result.get('receipt'):
        receipt=result['receipt']
        account=classroom.committee_accounts.filter(pk=receipt['id']).first()
        result['receipt']={**receipt,'username':account.username if account else receipt['username'],
            'credentials_current':bool(account and account.active and account.revision==receipt['revision'])}
    return {**result,**account_result(classroom)}


@sensitive_variables()
@transaction.atomic
def login_credentials(request,code,data):
    classroom=get_class(code)
    actor=actor_for(request,classroom,owner_only=True)
    if type(data.get('id')) is not int:
        raise BusinessError('invalid_account','班委账号编号无效。')
    try:
        account=classroom.committee_accounts.get(pk=data['id'])
    except CommitteeAccount.DoesNotExist:
        raise BusinessError('not_found','班委账号不存在或不属于本班。',404)
    password=read_password(account)
    event(actor,classroom,'committee_credentials_read',f'读取班委账号「{account.username}」登录信息用于复制',
          object_id=account.pk,revision=account.revision)
    return {'username':account.username,'password':password,'class_name':classroom.classname,'active':account.active}


@sensitive_variables()
def sign_in(request, data):
    username, password = data.get('username'), data.get('password')
    if not isinstance(username,str) or not isinstance(password,str) or len(username)>150 or len(password)>128:
        raise BusinessError('invalid_credentials','用户名或密码错误。',401)
    username = username.lower()
    now = time.time()
    def verify():
        account = CommitteeAccount.objects.filter(username=username).first()
        if not account:
            make_password(password)  # Same password-hash work for unknown names.
            return None
        return account if account.active and account.check_password(password) else None
    account = check_login('committee', username, verify)
    if data.get('reauthorize'):
        previous = request.session.get('committee_auth',{})
        if (str(account.inclass_id) != str(data.get('class_id')) or
                (previous and previous.get('account_id') != account.pk)):
            raise BusinessError('reauthorization_mismatch','请使用原班委账号恢复本班登录，当前草稿已保留。',403)
    if data.get('reauthorize') and not request.user.is_authenticated:
        request.session.cycle_key()
    else:
        logout(request)
        request.session.cycle_key()
    request.session.pop('class_grants',None)
    request.session.pop('committee_identity',None)
    request.session['committee_auth'] = {'account_id':account.pk,'version':account.auth_version,
                                         'expires_at':now+GRANT_SECONDS}
    return {'role':'committee','label':'班委','username':account.username,'id':account.pk,
            'expires_at':now+GRANT_SECONDS,'url':f'/classes/{account.inclass_id}/'}
