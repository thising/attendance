"""Repair missing, outdated or divergent current-term public snapshots."""
from django.core.management.base import BaseCommand

from manage.models import Class
from manage.services import calendar
from manage.services.report_snapshots import refresh_report


class Command(BaseCommand):
    help = '校验并修复所有班级的本学期公开报告快照（建议北京时间每日 04:00 执行）'

    def handle(self, *args, **options):
        today = calendar.business_today()
        checked = repaired = 0
        for class_id in Class.objects.order_by('pk').values_list('pk', flat=True).iterator():
            _, changed = refresh_report(class_id, today=today, verify_content=True)
            checked += 1
            repaired += bool(changed)
        self.stdout.write(f'公开报告校验完成：班级 {checked}，重建 {repaired}，日期 {today.isoformat()}。')
