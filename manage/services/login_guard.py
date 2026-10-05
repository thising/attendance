"""Rolling account-name throttles with expensive verification outside write locks."""
import math
import time

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password, verify_password
from django.views.decorators.debug import sensitive_variables

from manage.models import CommitteeAccount, CommitteeLoginGuard, OwnerLoginGuard
from .access import digest
from .errors import BusinessError
from .writes import atomic_write

WINDOW_SECONDS = 5 * 60
BLOCK_SECONDS = 30 * 60
FAILURE_LIMIT = 8


def rate_limit_error(until, now):
    return BusinessError('authorization_rate_limited', '连续登录失败次数过多，请30分钟后再试。', 429,
                         {'retry_after': max(1, math.ceil(until - now))})


@sensitive_variables()
def authenticate_owner(request, username=None, password=None):
    """Verify and compute upgrades without Django's password-writing setter."""
    user_model = get_user_model()
    try:
        user = user_model._default_manager.get_by_natural_key(username)
    except user_model.DoesNotExist:
        make_password(password)
        return None
    correct, needs_upgrade = verify_password(password, user.password)
    if not correct or not user.is_active:
        return None
    if needs_upgrade:
        user._duxing_password_upgrade = make_password(password)
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    return user


@sensitive_variables()
def _candidate_current(role, candidate):
    if not candidate:
        return False
    model = get_user_model() if role == 'owner' else CommitteeAccount
    criteria = {'pk': candidate.pk, 'password': candidate.password}
    if role == 'owner':
        criteria['is_active'] = True
    else:
        criteria.update(active=True, auth_version=candidate.auth_version)
    # A disabled/reset account cannot establish a session from an in-flight hash.
    current = model.objects.filter(**criteria)
    if not current.exists():
        return False
    upgraded = getattr(candidate, '_duxing_password_upgrade', None)
    if upgraded:
        if current.update(password=upgraded) != 1:
            return False
    return True


@sensitive_variables()
@atomic_write
def _finish_login(model, name_digest, role, candidate):
    # Completion order serializes attempts. Recheck after hashing so the eighth
    # failure also rejects a successful verification that is still in flight.
    now = time.time()
    guard, _ = model.objects.get_or_create(username_digest=name_digest)
    if guard.blocked_until > now:
        return None, rate_limit_error(guard.blocked_until, now)
    failure_times = [stamp for stamp in guard.failure_times if stamp > now - WINDOW_SECONDS]
    if _candidate_current(role, candidate):
        guard.failure_times = []
        guard.failures = 0
        guard.first_failure_at = 0
        guard.blocked_until = 0
        error = None
    else:
        failure_times.append(now)
        guard.failure_times = failure_times[-FAILURE_LIMIT:]
        guard.failures = len(guard.failure_times)
        guard.first_failure_at = guard.failure_times[0]
        guard.blocked_until = now + BLOCK_SECONDS if guard.failures >= FAILURE_LIMIT else 0
        error = (rate_limit_error(guard.blocked_until, now) if guard.blocked_until else
                 BusinessError('invalid_credentials', '用户名或密码错误。', 401))
    guard.save(update_fields=['failure_times', 'failures', 'first_failure_at', 'blocked_until'])
    # Raising after atomic_write returns keeps rejected attempts committed.
    return (None if error else candidate), error


@sensitive_variables()
def check_login(role, username, verify):
    if role not in ('owner', 'committee'):
        raise ValueError('Unknown login role.')
    model = OwnerLoginGuard if role == 'owner' else CommitteeLoginGuard
    name_digest = digest(username.strip().casefold())
    now = time.time()
    guard = model.objects.filter(username_digest=name_digest).first()
    if guard and guard.blocked_until > now:
        raise rate_limit_error(guard.blocked_until, now)
    candidate = verify()
    result, error = _finish_login(model, name_digest, role, candidate)
    if error:
        raise error
    # Publish the upgraded value on the returned User only after commit; a busy
    # transaction may retry, and must still compare against the original hash.
    if getattr(result, '_duxing_password_upgrade', None):
        result.password = result._duxing_password_upgrade
    return result
