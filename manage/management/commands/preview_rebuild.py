"""Read-only comparison: rebuilding history must never rewrite archived grades."""
import json
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, transaction
from django.db.models import Q
from manage.models import Class, ClassTerm, SummaryCount, COUNT_FIELDS
from manage.services.calendar import business_today, parse_term, display_term
from manage.services.errors import BusinessError
from manage.services.scoring import aggregate_counts, roster_for


class Command(BaseCommand):
    help = '只读比较指定学期的明细与次数缓存，不重建缓存或覆盖历史归档。'

    def add_arguments(self, parser):
        parser.add_argument('--class-id', type=int, required=True)
        parser.add_argument('--term')

    def handle(self, *args, **options):
        today = business_today()
        try:
            term = parse_term(options['term']) if options['term'] else display_term(today)
            # One consistent snapshot prevents a concurrent write from creating a
            # false facts/cache difference. No business writes occur in this block.
            with transaction.atomic():
                classroom = Class.objects.get(pk=options['class_id'])
                facts = aggregate_counts(classroom, term.start, term.end)
                month_filter = Q()
                for year, month in term.months(term.end):
                    month_filter |= Q(year=year, month=month)
                cached = {
                    (row.student_id, row.year, row.month): {
                        key: getattr(row, field) for key, field in COUNT_FIELDS.items()
                    }
                    for row in SummaryCount.objects.filter(
                        month_filter, student__inclass=classroom,
                    )
                }
                zero_counts = dict.fromkeys(COUNT_FIELDS, 0)
                keys = facts.keys() | cached.keys()
                missing_cache = facts.keys() - cached.keys()
                stale_cache = {
                    key for key in cached.keys() - facts.keys()
                    if cached[key] != zero_counts
                }
                changed_cache = {
                    key for key in facts.keys() & cached.keys()
                    if facts[key] != cached[key]
                }
                different = missing_cache | stale_cache | changed_cache
                calendar_only = None
                if not classroom.legacy_pending:
                    calendar_only = sum(
                        (student['id'], year, month) not in keys
                        for student in roster_for(classroom, term)
                        for year, month in term.months(today)
                    )
                archive = ClassTerm.objects.filter(inclass=classroom, term_key=term.key).first()
                archived_baseline_present = bool(
                    term.end <= today and archive and archive.archived_data is not None
                )
                result = {
                    'class_id': classroom.pk, 'term': term.key, 'read_only': True,
                    'start_date': term.start.isoformat(),
                    'end_date_exclusive': term.end.isoformat(),
                    'fact_months': len(facts), 'cached_months': len(cached),
                    'compared_months': len(keys), 'different_months': len(different),
                    'missing_cache_months': len(missing_cache),
                    'stale_cache_months': len(stale_cache),
                    'changed_cache_months': len(changed_cache),
                    'calendar_only_months': calendar_only,
                    'calendar_basis': 'unverified_legacy_roster' if classroom.legacy_pending else 'roster_versions',
                    'archived_baseline_present': archived_baseline_present,
                    'archived_baseline_compared': False,
                    'note': (
                        'calendar-only 表示名单内已进入月份无事实且无缓存，按日历有基础分，'
                        '不是漏计差异；旧库名单未核实时该数量为 null。'
                        '本命令只比较事实次数与缓存，不校验或覆盖 ClassTerm.archived_data；'
                        '即使发现历史差异，也不改变归档名单、记录、规则或成绩。'
                    ),
                }
        except Class.DoesNotExist as exc:
            raise CommandError('班级不存在。') from exc
        except BusinessError as exc:
            raise CommandError(exc.message) from exc
        except OperationalError as exc:
            if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                raise
            raise CommandError('数据库暂时繁忙，本次比较未完成且未写入数据；请稍后重试。') from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
