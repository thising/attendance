"""Remove only expired throttle state; no scheduled task is installed here."""
import time

from django.core.management.base import BaseCommand, CommandError

from manage.models import CommitteeLoginGuard, OwnerLoginGuard
from manage.services.login_guard import WINDOW_SECONDS
from manage.services.writes import atomic_write


@atomic_write
def remove_if_expired(model, pk, now):
    guard = model.objects.filter(pk=pk).first()
    if not guard or guard.blocked_until > now:
        return False
    if guard.first_failure_at > now - WINDOW_SECONDS or any(
            stamp > now - WINDOW_SECONDS for stamp in guard.failure_times):
        return False
    guard.delete()
    return True


class Command(BaseCommand):
    help = '清理已无窗口内失败且未处于阻断期的登录限流状态；不改变账号或日志。'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=1000, help='每种角色本次最多检查的候选数。')

    def handle(self, *args, **options):
        limit = options['limit']
        if limit < 1:
            raise CommandError('--limit 必须为正整数。')
        now = time.time()
        deleted = 0
        for model in (OwnerLoginGuard, CommitteeLoginGuard):
            candidates = list(model.objects.filter(blocked_until__lte=now,
                first_failure_at__lte=now - WINDOW_SECONDS).order_by('pk').values_list('pk', flat=True)[:limit])
            for pk in candidates:
                deleted += remove_if_expired(model, pk, now)
        self.stdout.write(f'已清理 {deleted} 条过期登录限流状态。')
