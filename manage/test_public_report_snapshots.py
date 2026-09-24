"""Synthetic acceptance tests for persisted current-term public reports."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import F
from django.test import Client, TestCase, TransactionTestCase

from manage.models import Class, CurrentClassReport, RosterVersion, Student
from manage.services.report_snapshots import refresh_report
from manage.services.scoring import policy_for
from manage.services.calendar import Term
from manage.test_domain import business_day


class SnapshotWorkflowTests(TestCase):
    def setUp(self):
        clock = business_day('2026-09-24')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('snapshot-owner', password='fixture-password')
        self.classroom = Class.objects.create(owner=self.owner, classname='合成一班', started_on=date(2026, 9, 1))
        self.other = Class.objects.create(owner=self.owner, classname='合成二班', started_on=date(2026, 9, 1))
        self.student = Student.objects.create(inclass=self.classroom, number='001', name='甲同学', sex='female')
        RosterVersion.objects.create(inclass=self.classroom, student=self.student,
            effective_term_start=date(2026, 9, 1), number='001', name='甲同学', sex='female')
        self.client.force_login(self.owner)

    def post(self, path, data):
        response = self.client.post(path, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def test_business_commits_refresh_snapshots_without_public_recalculation(self):
        self.post(f'/classes/{self.classroom.code}/public-report/', {'action': 'enable'})
        original = CurrentClassReport.objects.get(inclass=self.classroom)
        self.assertEqual(original.payload['tables'][0]['rows'][0]['score'], '60.00')
        with self.captureOnCommitCallbacks(execute=True):
            self.post(f'/classes/{self.classroom.code}/records/new/', {
                'term_key': '2026-autumn', 'submission_id': str(uuid4()), 'revision': 0,
                'roster_revision': self.classroom.revision,
                'record': {'kind': 'class', 'name': '点名', 'date': '2026-09-24',
                           'students': [{'id': self.student.pk, 'value': 'late'}]}})
        current = CurrentClassReport.objects.get(inclass=self.classroom)
        self.assertGreater(current.source_revision, original.source_revision)
        self.assertEqual(current.payload['tables'][0]['rows'][0]['counts']['late'], 1)
        self.assertNotEqual(current.payload['tables'][0]['rows'][0]['score'], '60.00')

        with self.captureOnCommitCallbacks(execute=True):
            self.post(f'/classes/{self.classroom.code}/roster/', {
                'term_key': '2026-autumn', 'submission_id': str(uuid4()),
                'revision': self.classroom.revision, 'action': 'add',
                'student': {'number': '002', 'name': '乙同学', 'sex': 'male'}})
        current.refresh_from_db()
        self.assertEqual(len(current.payload['tables'][0]['rows']), 2)

        # A rule change updates every class under the owner after commit.
        refresh_report(self.other.pk)
        weights = policy_for(self.owner.pk, Term(2026, 'autumn'))['weights']
        with self.captureOnCommitCallbacks(execute=True):
            self.post('/rules/', {'action': 'save', 'term_key': '2026-autumn',
                'submission_id': str(uuid4()), 'revision': 0, 'weights': weights,
                'monthly': {'base': '70', 'minimum': '0', 'maximum': None}})
        current.refresh_from_db()
        other = CurrentClassReport.objects.get(inclass=self.other)
        self.assertEqual(other.source_revision, 1)
        self.assertEqual(current.payload['tables'][0]['rows'][1]['score'], '70.00')
        with patch('manage.services.report_snapshots.build_report_payload', side_effect=AssertionError('recomputed')):
            url = self.client.get(f'/classes/{self.classroom.code}/public-report/').json()['data']['url']
            self.assertEqual(Client().get(url).status_code, 200)

    def test_month_and_term_boundaries_refresh_without_business_events(self):
        url = self.post(f'/classes/{self.classroom.code}/public-report/', {'action': 'enable'})['url']
        with business_day('2026-10-01'):
            with patch('manage.services.calendar.timezone.now', return_value=datetime(2026, 10, 1, 0, 1)):
                output = StringIO()
                call_command('refresh_due_public_reports', stdout=output)
            self.assertIn('重建 2', output.getvalue())
            snapshot = CurrentClassReport.objects.get(inclass=self.classroom)
            self.assertEqual(snapshot.month_key, '2026-10')
            self.assertEqual([table['key'] for table in snapshot.payload['tables']],
                             ['term', '2026-09', '2026-10'])
            self.assertEqual(snapshot.payload['tables'][2]['rows'][0]['score'], '60.00')
            with patch('manage.services.report_snapshots.build_report_payload', side_effect=AssertionError('recomputed')):
                self.assertEqual(Client().get(url).status_code, 200)
        with business_day('2027-01-01'):
            self.assertEqual(Client().get(url).status_code, 200)
            snapshot.refresh_from_db()
            self.assertEqual(snapshot.term_key, '2026-autumn')
            self.assertEqual([table['key'] for table in snapshot.payload['tables']],
                             ['term', '2026-09', '2026-10', '2026-11', '2026-12', '2027-01'])
        with business_day('2027-08-01'):
            # Access repairs a missed boundary timer before serving content.
            self.assertEqual(Client().get(url).status_code, 200)
            snapshot.refresh_from_db()
            self.assertEqual(snapshot.term_key, '')
            self.assertEqual(snapshot.payload['tables'], [])
        with business_day('2027-09-01'):
            self.assertEqual(Client().get(url).status_code, 200)
            snapshot.refresh_from_db()
            self.assertEqual(snapshot.term_key, '2027-autumn')
            self.assertEqual([table['key'] for table in snapshot.payload['tables']], ['term', '2027-09'])

    def test_four_am_consistency_check_repairs_missing_and_divergent_reports(self):
        refresh_report(self.classroom.pk)
        snapshot = CurrentClassReport.objects.get(inclass=self.classroom)
        snapshot.payload['tables'][0]['rows'][0]['score'] = '999.00'
        snapshot.save(update_fields=['payload'])
        CurrentClassReport.objects.filter(inclass=self.other).delete()
        with patch('manage.services.calendar.timezone.now', return_value=datetime(2026, 9, 24, 4)):
            output = StringIO()
            call_command('verify_public_reports', stdout=output)
        self.assertIn('重建 2', output.getvalue())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload['tables'][0]['rows'][0]['score'], '60.00')
        self.assertTrue(CurrentClassReport.objects.filter(inclass=self.other).exists())
        # Also detects fact drift even when the revision and saved digest agree.
        Student.objects.filter(pk=self.student.pk).update(name='改名同学')
        RosterVersion.objects.filter(student=self.student).update(name='改名同学')
        output = StringIO()
        call_command('verify_public_reports', stdout=output)
        self.assertIn('重建 1', output.getvalue())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.payload['tables'][0]['rows'][0]['name'], '改名同学')

    def test_revoked_token_is_rejected_before_missing_snapshot_is_generated(self):
        settings = f'/classes/{self.classroom.code}/public-report/'
        url = self.post(settings, {'action': 'enable'})['url']
        self.post(settings, {'action': 'disable'})
        CurrentClassReport.objects.filter(inclass=self.classroom).delete()
        with patch('manage.services.report_snapshots.build_report_payload', side_effect=AssertionError('recomputed')):
            self.assertEqual(Client().get(url).status_code, 404)
        self.assertFalse(CurrentClassReport.objects.filter(inclass=self.classroom).exists())


class SnapshotConcurrencyTests(TransactionTestCase):
    def test_concurrent_stale_reads_generate_once_and_replace_atomically(self):
        owner = get_user_model().objects.create_user('concurrent-owner', password='fixture-password')
        classroom = Class.objects.create(owner=owner, classname='并发班', started_on=date(2026, 9, 1))
        student = Student.objects.create(inclass=classroom, number='001', name='并发同学')
        RosterVersion.objects.create(inclass=classroom, student=student,
            effective_term_start=date(2026, 9, 1), number='001', name='并发同学')
        with business_day('2026-09-24'):
            owner_client = Client()
            owner_client.force_login(owner)
            issued = owner_client.post(f'/classes/{classroom.code}/public-report/',
                json.dumps({'action': 'enable'}), content_type='application/json')
            self.assertEqual(issued.status_code, 200, issued.content)
            url = issued.json()['data']['url']
            Class.objects.filter(pk=classroom.pk).update(report_revision=F('report_revision') + 1)
            from manage.services import report_snapshots
            original = report_snapshots.build_report_payload
            calls = []
            def counted(*args, **kwargs):
                calls.append(1)
                return original(*args, **kwargs)
            with patch.object(report_snapshots, 'build_report_payload', side_effect=counted):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(lambda _: Client().get(url), range(4)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(CurrentClassReport.objects.filter(inclass=classroom).count(), 1)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all('并发同学'.encode() in response.content for response in responses))
