"""Establish only the verified current-term baseline after the 2026 migration bridge."""
import re
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from manage.models import (Activity, Class, ClassTerm, OwnerScoringSettings,
    OwnerTermPolicy, RosterVersion, ScoringPolicyVersion, Student, SummaryCount,
    COUNT_FIELDS, WEIGHT_DEFAULTS)
from manage.services import calendar
from manage.services.report_snapshots import refresh_report
from manage.services.scoring import class_report


class Command(BaseCommand):
    help = 'Build a checked current-term baseline after the production-2026 schema bridge.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', required=True, help='Cutover date YYYY-MM-DD in Beijing time')
        parser.add_argument('--source-sha256', required=True, help='Digest of the immutable pre-migration backup')

    def handle(self, *args, **options):
        from django.conf import settings
        if settings.DUXING_MIGRATION_LINEAGE != 'production-2026':
            raise CommandError('Only the verified production-2026 lineage may be bootstrapped.')
        try:
            today = date.fromisoformat(options['as_of'])
        except ValueError as exc:
            raise CommandError('Invalid --as-of date.') from exc
        term = calendar.term_for_date(today)
        if term is None:
            raise CommandError('8 月没有当前学期，不能建立计分基线。')
        source_hash = options['source_sha256'].lower()
        if not re.fullmatch(r'[0-9a-f]{64}', source_hash):
            raise CommandError('Invalid source SHA-256.')
        with transaction.atomic():
            classrooms = list(Class.objects.select_for_update().order_by('id'))
            if not classrooms or not all(c.legacy_pending for c in classrooms):
                raise CommandError('Expected unmodified legacy classes; refusing partial/repeated baseline.')
            if (RosterVersion.objects.exists() or ScoringPolicyVersion.objects.exists()
                    or ClassTerm.objects.exists() or OwnerScoringSettings.objects.exists()
                    or OwnerTermPolicy.objects.exists()):
                raise CommandError('Current-term baseline tables are not empty.')
            if Activity.objects.exclude(occurred_on__gte=term.start, occurred_on__lte=today).exists():
                raise CommandError('Activity outside the current term or after cutover; historical facts require manual review.')
            cache = {}
            for item in SummaryCount.objects.select_related('student').all():
                month = date(item.year, item.month, 1)
                if not term.contains(month) or month > today:
                    raise CommandError('Summary cache outside the current entered term; manual review required.')
                cache[(item.student_id, item.year, item.month)] = {
                    key: getattr(item, column) for key, column in COUNT_FIELDS.items()}
            policies = {}
            for owner_id in sorted({c.owner_id for c in classrooms}):
                policy = ScoringPolicyVersion.objects.create(
                    owner_id=owner_id, effective_term_start=term.start, **WEIGHT_DEFAULTS)
                OwnerScoringSettings.objects.create(owner_id=owner_id, default_policy=policy)
                OwnerTermPolicy.objects.create(owner_id=owner_id, term_key=term.key, policy=policy)
                policies[owner_id] = policy
            verified = 0
            for classroom in classrooms:
                students = list(Student.objects.filter(inclass=classroom).order_by('id'))
                RosterVersion.objects.bulk_create([RosterVersion(student=s, inclass=classroom,
                    effective_term_start=term.start, number=s.number, name=s.name, sex=s.sex,
                    active=True) for s in students])
                classroom.started_on = term.start
                classroom.legacy_pending = False
                classroom.save(update_fields=['started_on', 'legacy_pending'])
                ClassTerm.objects.create(inclass=classroom, owner_id=classroom.owner_id,
                    term_key=term.key, policy=policies[classroom.owner_id],
                    baseline_source=f'vps207:{source_hash}')
                report = class_report(classroom, term, today=today)
                if len(report['rows']) != len(students):
                    raise CommandError('Roster/score count mismatch; transaction rolled back.')
                for row in report['rows']:
                    for month in row['months']:
                        year, mon = map(int, month['key'].split('-'))
                        expected = cache.get((row['id'], year, mon),
                            {key: 0 for key in COUNT_FIELDS})
                        if month['counts'] != expected:
                            raise CommandError('Source summary counts differ from raw reports; transaction rolled back.')
                    verified += 1
                refresh_report(classroom.pk, today=today, verify_content=True)
            if verified != Student.objects.count():
                raise CommandError('Some students were not verified; transaction rolled back.')
        self.stdout.write(self.style.SUCCESS(
            f'Current-term baseline verified: {len(classrooms)} classes, {verified} students, {term.key}.'))
