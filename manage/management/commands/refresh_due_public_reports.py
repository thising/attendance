"""Lightweight calendar-boundary refresh; safe to run every Beijing morning."""
from django.core.management.base import BaseCommand

from manage.models import Class
from manage.services import calendar
from manage.services.report_snapshots import refresh_report


class Command(BaseCommand):
    help = '刷新缺失或跨月/跨学期的公开报告快照（建议北京时间每日 00:01 执行）'

    def handle(self, *args, **options):
        today = calendar.business_today()
        checked = refreshed = 0
        for class_id in Class.objects.order_by('pk').values_list('pk', flat=True).iterator():
            _, changed = refresh_report(class_id, today=today)
            checked += 1
            refreshed += bool(changed)
        self.stdout.write(f'公开报告边界刷新完成：班级 {checked}，重建 {refreshed}，日期 {today.isoformat()}。')
