"""Only synthetic facts and caches are inspected; the command never repairs them."""
import io
import json
import re
import sqlite3
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from manage.models import Activity, Class, ClassTerm, Report, RosterVersion, Student, SummaryCount


class PreviewFixture:
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user('preview-rebuild-owner')
        self.classroom = Class.objects.create(
            classname='合成缓存核对班', owner=self.owner, managecode='!', started_on=date(2026, 2, 1),
        )
        self.student = Student.objects.create(inclass=self.classroom, number='SYN001', name='合成学生')
        RosterVersion.objects.create(
            student=self.student, inclass=self.classroom, effective_term_start=date(2026, 2, 1),
            number=self.student.number, name=self.student.name, sex=self.student.sex,
        )
        clock = patch('manage.management.commands.preview_rebuild.business_today', return_value=date(2026, 10, 3))
        clock.start()
        self.addCleanup(clock.stop)

    def preview(self, term='2026-autumn'):
        output = io.StringIO()
        call_command('preview_rebuild', class_id=self.classroom.pk, term=term, stdout=output)
        return json.loads(output.getvalue())

    def fact(self, day, status='late'):
        activity = Activity.objects.create(inclass=self.classroom, name='合成点名', occurred_on=day)
        Report.objects.create(activity=activity, student=self.student, status=status)


class PreviewRebuildTests(PreviewFixture, TestCase):
    def test_nonzero_cache_without_any_fact_is_reported(self):
        SummaryCount.objects.create(student=self.student, year=2026, month=9, late_count=2)
        result = self.preview()
        self.assertEqual(result['fact_months'], 0)
        self.assertEqual(result['cached_months'], 1)
        self.assertEqual(result['compared_months'], 1)
        self.assertEqual(result['different_months'], 1)
        self.assertEqual(result['stale_cache_months'], 1)
        self.assertEqual(result['missing_cache_months'], 0)
        self.assertEqual(result['calendar_only_months'], 1)  # October has no fact or cache.

    def test_cache_and_facts_are_both_limited_to_the_requested_term(self):
        for year, month in ((2026, 1), (2026, 7), (2026, 8), (2027, 2), (2027, 9)):
            SummaryCount.objects.create(student=self.student, year=year, month=month, absent_count=9)
            self.fact(date(year, month, 10), status='absent')
        # January belongs to the previous year's autumn; it must be compared,
        # even if the command is investigating an invalid future cache entry.
        SummaryCount.objects.create(student=self.student, year=2027, month=1, late_count=1)
        result = self.preview()
        self.assertEqual(result['fact_months'], 0)
        self.assertEqual(result['cached_months'], 1)
        self.assertEqual(result['different_months'], 1)
        self.assertEqual(result['stale_cache_months'], 1)
        self.assertEqual(result['calendar_only_months'], 2)
        spring = self.preview('2026-spring')
        self.assertEqual(spring['fact_months'], 1)
        self.assertEqual(spring['cached_months'], 1)
        self.assertEqual(spring['different_months'], 1)  # Cached 9 absences vs one fact.

    def test_missing_and_changed_caches_are_distinct_from_calendar_only(self):
        self.fact(date(2026, 9, 10))
        result = self.preview()
        self.assertEqual(result['missing_cache_months'], 1)
        self.assertEqual(result['different_months'], 1)
        self.assertEqual(result['calendar_only_months'], 1)
        SummaryCount.objects.create(student=self.student, year=2026, month=9, late_count=2)
        result = self.preview()
        self.assertEqual(result['missing_cache_months'], 0)
        self.assertEqual(result['changed_cache_months'], 1)
        self.assertEqual(result['different_months'], 1)

    def test_zero_cache_without_facts_is_not_a_stale_difference(self):
        SummaryCount.objects.create(student=self.student, year=2026, month=9)
        result = self.preview()
        self.assertEqual(result['compared_months'], 1)
        self.assertEqual(result['different_months'], 0)
        self.assertEqual(result['stale_cache_months'], 0)
        self.assertEqual(result['calendar_only_months'], 1)

    def test_history_preview_does_not_write_or_overwrite_reviewed_archive(self):
        cache = SummaryCount.objects.create(student=self.student, year=2026, month=7, absent_count=4)
        baseline = {'rows': [{'id': self.student.pk, 'name': '历史合成姓名', 'score': '51.25'}],
                    'summary': {'average': '51.25'}, 'term_key': '2026-spring'}
        archive = ClassTerm.objects.create(
            inclass=self.classroom, owner=self.owner, term_key='2026-spring',
            archived_data=baseline, baseline_source='synthetic-reviewed-baseline',
        )
        before_cache = dict(SummaryCount.objects.values().get(pk=cache.pk))
        with CaptureQueriesContext(connection) as queries:
            result = self.preview('2026-spring')
        forbidden = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', re.I)
        self.assertFalse([query['sql'] for query in queries if forbidden.search(query['sql'])])
        self.assertTrue(result['read_only'])
        self.assertTrue(result['archived_baseline_present'])
        self.assertFalse(result['archived_baseline_compared'])
        self.assertEqual(result['stale_cache_months'], 1)
        self.assertEqual(dict(SummaryCount.objects.values().get(pk=cache.pk)), before_cache)
        archive.refresh_from_db()
        self.assertEqual(archive.archived_data, baseline)
        self.assertEqual(archive.baseline_source, 'synthetic-reviewed-baseline')
        self.assertEqual(Report.objects.count(), 0)

    def test_unverified_legacy_roster_is_unknown_not_zero_calendar_months(self):
        self.classroom.legacy_pending = True
        self.classroom.save(update_fields=['legacy_pending'])
        result = self.preview()
        self.assertIsNone(result['calendar_only_months'])
        self.assertEqual(result['calendar_basis'], 'unverified_legacy_roster')

    def test_invalid_term_is_a_command_error_with_no_success_output(self):
        output = io.StringIO()
        with self.assertRaises(CommandError):
            call_command('preview_rebuild', class_id=self.classroom.pk, term='bad-term', stdout=output)
        self.assertEqual(output.getvalue(), '')


class PreviewRebuildBusyTests(PreviewFixture, TransactionTestCase):
    def test_busy_snapshot_fails_without_success_output_or_writes(self):
        if connection.vendor != 'sqlite':
            self.skipTest('This regression targets the configured SQLite backend.')
        database = str(connection.settings_dict['NAME'])
        if database == ':memory:':
            self.skipTest('A separate connection requires a file or shared-memory URI.')
        blocker = sqlite3.connect(database, uri=database.startswith('file:'))
        with connection.cursor() as cursor:
            timeout = cursor.execute('PRAGMA busy_timeout').fetchone()[0]
            cursor.execute('PRAGMA busy_timeout = 25')
        output = io.StringIO()
        try:
            blocker.execute('BEGIN IMMEDIATE')
            with self.assertRaisesMessage(CommandError, '数据库暂时繁忙'):
                call_command('preview_rebuild', class_id=self.classroom.pk, term='2026-autumn', stdout=output)
            self.assertEqual(output.getvalue(), '')
        finally:
            blocker.rollback()
            blocker.close()
            with connection.cursor() as cursor:
                cursor.execute(f'PRAGMA busy_timeout = {int(timeout)}')
        self.assertEqual(SummaryCount.objects.count(), 0)
        self.assertEqual(ClassTerm.objects.count(), 0)
        self.assertEqual(Report.objects.count(), 0)
