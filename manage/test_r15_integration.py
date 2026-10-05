"""Cross-layer lifecycle/read-only/health checks on synthetic data only."""
from datetime import date
from io import StringIO
from uuid import uuid4
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, Client, override_settings

from manage.models import ClassTerm, RosterVersion, Student, SummaryCount, Activity, Report
from manage.services import calendar
from manage.services.report_snapshots import refresh_report
from manage.test_domain import business_day
from manage.test_named_committee import AccountFixtures


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class R15IntegrationTests(AccountFixtures, TestCase):
    def student(self):
        student = Student.objects.create(inclass=self.c, name='虚构学生', number='01')
        RosterVersion.objects.create(student=student, inclass=self.c,
            effective_term_start=date(2026, 9, 1), name=student.name, number=student.number, sex=student.sex)
        return student

    def end_class(self):
        url = f'/classes/{self.c.pk}/management/'
        preview = self.owner_client.get(url).json()['data']
        result = self.owner_client.post(url, {
            'action': 'archive', 'term_key': preview['term_key'], 'confirmation': self.c.classname,
            'revision': preview['class_revision'], 'report_revision': preview['report_revision'],
            'freeze_date': preview['freeze_date'], 'submission_id': str(uuid4()),
        }, content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        return result.json()['data']

    def test_management_preview_is_owner_scoped_current_and_read_only(self):
        url = f'/classes/{self.c.pk}/management/'
        committee, _ = self.sign_in()
        for client in (Client(), committee):
            self.assertEqual(client.get(url).status_code, 403)
        self.assertEqual(self.owner_client.get(url+'?term=2026-spring').status_code, 409)
        payload = self.owner_client.get(url).json()['data']
        self.assertEqual(payload['class_id'], self.c.pk)
        self.assertEqual(payload['freeze_months'], ['2026-09', '2026-10'])
        self.assertEqual(payload['term_key'], '2026-autumn')

    def test_ended_class_history_pages_use_frozen_facts_and_default_term(self):
        student = self.student()
        activity = Activity.objects.create(inclass=self.c, occurred_on=date(2026, 10, 1), name='合成考勤')
        Report.objects.create(activity=activity, student=student, status='late')
        self.end_class()
        frozen = ClassTerm.objects.get(inclass=self.c, term_key='2026-autumn').archived_data
        self.assertTrue(frozen['frozen'])
        with business_day('2027-02-03'):
            self.owner_client.force_login(self.owner)
            active = self.owner_client.get('/?format=json').json()['data']
            self.assertNotIn(self.c.pk, [c['id'] for c in active['classes']])
            history = self.owner_client.get('/?scope=archived&format=json').json()['data']
            self.assertEqual(history['scope'], 'archived')
            self.assertEqual([r['term_key'] for r in history['dashboard_students']], ['2026-autumn'])
            result = self.owner_client.get(f'/classes/{self.c.pk}/?format=json').json()['data']
            self.assertEqual(result['term']['key'], '2026-autumn')
            self.assertTrue(result['readonly'])
            self.assertTrue(result['historical'])
            self.assertEqual(result['students'], frozen['rows'])
            self.assertEqual([m['key'] for m in result['monthly_overview']['months']], ['2026-09', '2026-10'])
            record = self.owner_client.get(f'/classes/{self.c.pk}/records/{activity.pk}/?format=json').json()['data']
            self.assertTrue(record['readonly'])
            self.assertEqual(record['record']['students'][0]['value'], 'late')
            self.assertTrue(record['record']['time'].endswith('+08:00'))
            detail = self.owner_client.get(f'/classes/{self.c.pk}/students/{student.pk}/?format=json').json()['data']
            self.assertEqual(detail['student']['score'], frozen['rows'][0]['score'])
            roster = self.owner_client.get(f'/classes/{self.c.pk}/roster/?format=json').json()['data']
            self.assertIsNone(roster['class_management'])
            self.assertEqual(roster['students'], frozen['rows'])
        self.assertEqual(ClassTerm.objects.get(inclass=self.c, term_key='2026-autumn').archived_data, frozen)

    def test_uncertain_old_archive_is_visible_as_pending_without_fake_scores(self):
        self.c.archived = True
        self.c.save(update_fields=['archived'])
        history = self.owner_client.get('/?scope=archived&format=json').json()['data']
        self.assertTrue(history['class_summaries'][0]['pending'])
        self.assertEqual(history['dashboard_students'], [])
        self.assertEqual(self.owner_client.get(f'/classes/{self.c.pk}/?format=json').status_code, 409)
        with self.assertRaises(CommandError):
            call_command('check_release_readiness', stdout=StringIO())
        self.assertFalse(ClassTerm.objects.filter(inclass=self.c).exists())

    def test_health_read_only_detects_missing_then_current_reports(self):
        with self.assertRaises(CommandError): call_command('check_runtime_health', stdout=StringIO())
        for classroom in (self.c, self.c2, self.private): refresh_report(classroom.pk)
        output = StringIO()
        call_command('check_runtime_health', stdout=output)
        self.assertIn('"reports_current": true', output.getvalue())

    def test_committee_home_for_ended_class_is_historical_and_read_only(self):
        self.student()
        self.end_class()
        client, response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        result = client.get('/?format=json').json()['data']
        self.assertEqual(result['scope'], 'archived')
        self.assertTrue(result['readonly'])
        self.assertTrue(result['historical'])

    def test_recent_copy_does_not_hide_an_old_backup(self):
        import json, tempfile
        from pathlib import Path
        for classroom in (self.c, self.c2, self.private): refresh_report(classroom.pk)
        with tempfile.TemporaryDirectory() as temporary:
            status = Path(temporary) / 'offsite.json'
            status.write_text(json.dumps({'ok': True, 'last_success_at': '2026-10-15T12:00:00+08:00',
                'latest_backup_created_at': '2025-01-01T12:00:00+08:00'}))
            with self.assertRaises(CommandError):
                call_command('check_runtime_health', offsite_status=status, max_backup_age_hours=48, stdout=StringIO())

    def test_damaged_frozen_archive_blocks_preflight_and_health(self):
        self.student()
        self.end_class()
        for classroom in (self.c, self.c2, self.private): refresh_report(classroom.pk)
        ClassTerm.objects.filter(inclass=self.c).update(archived_data={'frozen_at': 'invalid'})
        for command in ('check_release_readiness', 'check_runtime_health'):
            with self.assertRaises(CommandError): call_command(command, stdout=StringIO())

    def test_public_ended_class_does_not_promise_future_scores(self):
        from urllib.parse import urlparse
        self.student()
        response = self.owner_client.post(f'/classes/{self.c.pk}/public-report/', {
            'action': 'enable', 'revision': 0, 'submission_id': str(uuid4()),
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        path = urlparse(response.json()['data']['url']).path
        self.end_class()
        current = Client().get(path)
        self.assertContains(current, '以下为冻结成绩')
        self.assertContains(current, 'historical-view')
        with business_day('2027-02-03'):
            later = Client().get(path)
            self.assertContains(later, '不再生成新学期成绩')
            self.assertNotContains(later, '合成学生')
            self.assertNotContains(later, '进入新学期后，此链接将自动显示')

    def test_old_score_cache_cannot_be_used_as_scoring_authority(self):
        with self.assertRaises(NotImplementedError): SummaryCount().score()
