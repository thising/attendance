"""Read-only report and backup health, suitable for an existing monitor."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError
from manage.models import Class, CurrentClassReport
from manage.services import calendar
from manage.services.report_snapshots import is_current
from manage.services.scoring import validate_ended_archive
from manage.services.errors import BusinessError


class Command(BaseCommand):
    help = '只读检查公开报告是否落后及异地复制状态；不生成报告或发送外部通知'

    def add_arguments(self, parser):
        parser.add_argument('--offsite-status', type=Path)
        parser.add_argument('--max-backup-age-hours', type=float)

    def handle(self, *args, **options):
        today = calendar.business_today()
        snapshots = {item.inclass_id: item for item in CurrentClassReport.objects.all()}
        stale, unverified = [], []
        for classroom in Class.objects.iterator():
            if not is_current(snapshots.get(classroom.pk), classroom, today): stale.append(classroom.pk)
            if classroom.archived:
                try: validate_ended_archive(classroom)
                except BusinessError: unverified.append(classroom.pk)
        result = {'reports_current': not stale and not unverified, 'stale_class_ids': stale,
                  'archive_unverified_ids': unverified}
        errors = bool(stale or unverified)
        path = options.get('offsite_status')
        if path:
            try:
                status = json.loads(path.read_text())
                stamp = datetime.fromisoformat(status['latest_backup_created_at'])
                checked = datetime.fromisoformat(status['last_success_at'])
                if stamp.tzinfo is None or checked.tzinfo is None: raise ValueError('missing timezone')
                now = datetime.now(ZoneInfo('Asia/Shanghai'))
                age = (now - stamp).total_seconds()/3600
                limit = options.get('max_backup_age_hours')
                healthy = bool(status.get('ok')) and age >= 0 and (limit is None or age <= limit)
                result['offsite'] = {'ok': healthy, 'age_hours': round(age, 2),
                                     'verification_age_hours': round((now - checked).total_seconds()/3600, 2),
                                     'legacy_pending': status.get('legacy_pending', 0)}
                errors |= not healthy
            except (OSError, ValueError, KeyError, TypeError):
                result['offsite'] = {'ok': False, 'error': 'missing_or_invalid_status'}
                errors = True
        elif options.get('max_backup_age_hours') is not None:
            raise CommandError('--max-backup-age-hours requires --offsite-status')
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if errors: raise CommandError('运行状态异常；本次只读检查没有修复或发送通知。')
