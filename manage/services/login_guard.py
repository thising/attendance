"""Account-name based login throttling shared by teacher and committee logins."""
import time
import math

from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from manage.models import CommitteeLoginGuard, OwnerLoginGuard
from .access import digest
from .errors import BusinessError

WINDOW_SECONDS = 5 * 60
BLOCK_SECONDS = 30 * 60
FAILURE_LIMIT = 8


@sensitive_variables()
def check_login(role, username, verify):
    model = OwnerLoginGuard if role == 'owner' else CommitteeLoginGuard
    error = None
    result = None
    now = time.time()
    with transaction.atomic():
        guard, _ = model.objects.get_or_create(username_digest=digest(username.strip().casefold()))
        if guard.first_failure_at and guard.blocked_until > now:
            error = BusinessError('authorization_rate_limited', '连续登录失败次数过多，请30分钟后再试。', 429,
                                  {'retry_after': math.ceil(guard.blocked_until - now)})
        else:
            result = verify()
            if result:
                guard.failures = 0
                guard.first_failure_at = 0
                guard.blocked_until = 0
            else:
                if not guard.first_failure_at or now - guard.first_failure_at >= WINDOW_SECONDS:
                    guard.failures = 0
                    guard.first_failure_at = now
                guard.failures += 1
                if guard.failures >= FAILURE_LIMIT:
                    guard.blocked_until = now + BLOCK_SECONDS
                    error = BusinessError('authorization_rate_limited', '连续登录失败次数过多，请30分钟后再试。', 429,
                                          {'retry_after': BLOCK_SECONDS})
                else:
                    error = BusinessError('invalid_credentials', '用户名或密码错误。', 401)
            guard.save(update_fields=['failures', 'first_failure_at', 'blocked_until'])
    # Keep rejected attempts committed; an exception inside atomic would roll them back.
    if error:
        raise error
    return result
