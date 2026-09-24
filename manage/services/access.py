import hashlib
import time
from dataclasses import dataclass
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from manage.models import Class, CommitteeAccount
from .errors import BusinessError

GRANT_SECONDS = 3 * 60 * 60


@dataclass(frozen=True)
class Actor:
    role: str
    key: str
    user_id: int | None = None
    expires_at: float | None = None
    username: str = ""
    display_name: str = ""

    @property
    def label(self):
        return '班主任' if self.role == 'owner' else '班委'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def owner_actor(request):
    if not request.user.is_authenticated:
        raise BusinessError('authentication_required', '请先登录负责人账号。', 401)
    return Actor('owner', digest(f'owner:{request.user.pk}'), request.user.pk, username=request.user.get_username())


def get_class(code):
    try:
        if not str(code).isascii() or not str(code).isdigit():
            raise Class.DoesNotExist
        return Class.objects.select_related('owner').get(pk=int(code))
    except (Class.DoesNotExist, ValueError, OverflowError):
        raise BusinessError('not_found', '班级不存在。', 404)


def committee_actor(request):
    auth = request.session.get('committee_auth', {})
    if not isinstance(auth, dict) or time.time() >= auth.get('expires_at', 0):
        raise BusinessError('class_authorization_required', '班委登录尚未建立或已到期，请重新登录。', 401)
    account = CommitteeAccount.objects.filter(pk=auth.get('account_id'), active=True).select_related('inclass').first()
    if not account or account.auth_version != auth.get('version'):
        raise BusinessError('class_authorization_required', '班委账号已停用或密码已变更，请重新登录。', 401)
    return account, Actor('committee', digest(f'committee-account:{account.pk}'), account.pk,
                         auth['expires_at'], account.username, account.display_name)


def actor_for(request, classroom, owner_only=False):
    if request.user.is_authenticated and request.user.pk == classroom.owner_id:
        return owner_actor(request)
    if owner_only:
        raise BusinessError('permission_denied', '此操作仅限该班班主任。', 403)
    if request.user.is_authenticated:
        raise BusinessError('permission_denied', '你没有此班级的访问权限。', 403)
    account, actor = committee_actor(request)
    if account.inclass_id != classroom.pk:
        raise BusinessError('permission_denied', '班委账号仅可访问指定班级。', 403)
    return actor


def authorize(request, classroom, password):
    raise BusinessError('legacy_authorization_retired', '班级码和共享密码入口已停用，请使用班委账号登录。', 410)


def validate_class_password(password):
    if not isinstance(password, str) or len(password) > 128:
        raise BusinessError('invalid_password', '密码长度无效。')
    try:
        validate_password(password)
    except ValidationError as exc:
        raise BusinessError('invalid_password', ' '.join(exc.messages))


def require_ready(classroom):
    if classroom.legacy_pending:
        raise BusinessError('legacy_baseline_pending', '旧库迁移基线尚待核对，暂不开放业务修改。', 409)
    if classroom.archived:
        raise BusinessError('class_archived', '班级已归档，仅可查看。', 403)
