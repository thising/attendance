"""Synthetic regressions for review findings; SQLite connections are isolated.

TransactionTestCase deliberately avoids the outer transaction of TestCase so
the concurrent writer can actually commit (or wait for a reader's snapshot).
"""
import queue
import sqlite3
import threading
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import Client, TransactionTestCase

from manage import views
from manage.models import (
    Activity, AuditEvent, Class, OwnerScoringSettings, Report, RosterVersion,
    ScoringPolicyVersion, Student, WEIGHT_DEFAULTS,
)
from manage.services import calendar, roster, rules, scoring


class ReviewEdgeTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('review-edge-owner')
        self.day = date(2026, 10, 3)
        clock = patch('manage.services.calendar.business_today', return_value=self.day)
        clock.start()
        self.addCleanup(clock.stop)
        self.term = calendar.Term(2026, 'autumn')
        self.request = SimpleNamespace(user=self.owner, session={})

    def classroom(self, name):
        classroom = Class.objects.create(
            classname=name, owner=self.owner, managecode='!', started_on=date(2026, 9, 1),
        )
        student = Student.objects.create(inclass=classroom, number='SYN001', name='合成学生')
        RosterVersion.objects.create(
            student=student, inclass=classroom, effective_term_start=self.term.start,
            number=student.number, name=student.name, sex=student.sex,
        )
        activity = Activity.objects.create(
            inclass=classroom, name='合成点名', occurred_on=date(2026, 9, 15),
        )
        Report.objects.create(activity=activity, student=student, status='late')
        return classroom

    def rule_payload(self, revision=0, action='save'):
        return {
            'submission_id': str(uuid4()), 'term_key': self.term.key,
            'revision': revision, 'action': action,
            'weights': {**WEIGHT_DEFAULTS, 'late': '2.00'},
        }

    def test_archived_current_class_is_in_owner_rule_preview_and_audit(self):
        active = self.classroom('A合成班')
        archived = self.classroom('B合成班')
        roster.change_roster(self.request, archived.code, {
            'action': 'archive', 'revision': archived.revision,
            'term_key': self.term.key, 'submission_id': str(uuid4()),
        })
        archived.refresh_from_db()
        self.assertTrue(archived.archived)
        preview = rules.change_rules(self.request, self.rule_payload(action='preview'))['preview']
        self.assertEqual({row['id'] for row in preview['classes']}, {active.pk, archived.pk})
        self.assertEqual(preview['student_count'], 2)
        self.assertEqual(
            {(row['student_count'], row['changed_count'], row['max_absolute_change'])
             for row in preview['classes']},
            {(1, 1, '0.50')},
        )
        rules.change_rules(self.request, self.rule_payload())
        for classroom in (active, archived):
            with self.subTest(classroom=classroom.classname):
                report = scoring.class_report(classroom, self.term)
                self.assertEqual(report['summary']['average'], '59.00')
                self.assertEqual(
                    AuditEvent.objects.filter(inclass=classroom, kind='rules_updated').count(), 1,
                )

    def test_busy_json_and_html_reads_return_retryable_503(self):
        if connection.vendor != 'sqlite':
            self.skipTest('This regression exercises the configured SQLite backend.')
        classroom = self.classroom('锁冲突合成班')
        client = Client(raise_request_exception=False)
        client.force_login(self.owner)
        database = str(connection.settings_dict['NAME'])
        if database == ':memory:':
            self.skipTest('A separate connection requires a file or shared-memory URI.')
        blocker = sqlite3.connect(database, uri=database.startswith('file:'))
        with connection.cursor() as cursor:
            old_timeout = cursor.execute('PRAGMA busy_timeout').fetchone()[0]
            cursor.execute('PRAGMA busy_timeout = 25')
        try:
            blocker.execute('BEGIN IMMEDIATE')
            # A writer reservation must not block a consistent read.
            readable = client.get(f'/classes/{classroom.code}/?format=json')
            self.assertEqual(readable.status_code, 200)
            blocker.rollback()
            blocker.execute('BEGIN EXCLUSIVE')
            json_response = client.get(f'/classes/{classroom.code}/?format=json')
            self.assertEqual(json_response.status_code, 503)
            self.assertEqual(json_response.json()['error']['code'], 'database_busy')
            self.assertFalse(json_response.json()['ok'])
            self.assertGreaterEqual(int(json_response['Retry-After']), 1)
            html_response = client.get(f'/classes/{classroom.code}/')
            self.assertEqual(html_response.status_code, 503)
            self.assertGreaterEqual(int(html_response['Retry-After']), 1)
            error = html_response.context['data']['error']
            self.assertEqual(error['code'], 'database_busy')
            self.assertIn('稍后重试', error['message'])
        finally:
            blocker.rollback()
            blocker.close()
            with connection.cursor() as cursor:
                cursor.execute(f'PRAGMA busy_timeout = {int(old_timeout)}')

    def interleave_rule_write(self, reader, revision):
        """Let another real connection try to commit at a chosen reader gap.

        A correct snapshot may make the writer wait. Without a snapshot the
        writer commits before the reader continues, exposing mixed results.
        """
        gap = threading.Event()
        attempted = threading.Event()
        done = threading.Event()
        errors = queue.Queue()

        def writer():
            close_old_connections()
            try:
                if not gap.wait(5):
                    raise AssertionError('Reader did not reach the synchronization point.')
                attempted.set()
                rules.change_rules(self.request, self.rule_payload(revision=revision))
            except Exception as exc:
                errors.put(exc)
            finally:
                connections['default'].close()
                done.set()

        worker = threading.Thread(target=writer, name='duxing-review-rule-writer')
        worker.start()
        triggered = False

        def pause_for_writer():
            nonlocal triggered
            if triggered:
                return
            triggered = True
            gap.set()
            self.assertTrue(attempted.wait(5), 'Concurrent writer was not scheduled.')
            # Do not require a commit while the reader legitimately holds a snapshot.
            done.wait(1)

        try:
            result = reader(pause_for_writer)
        finally:
            # A future single-query reader may not need the old gap at all.
            gap.set()
            worker.join(8)
        self.assertFalse(worker.is_alive(), 'Concurrent writer did not finish after the read.')
        if not errors.empty():
            raise errors.get()
        return result

    def test_policy_weights_and_revision_are_one_snapshot(self):
        initial = ScoringPolicyVersion.objects.create(
            owner=self.owner, effective_term_start=self.term.start, late='1.00',
        )
        OwnerScoringSettings.objects.create(owner=self.owner, default_policy=initial, revision=1)

        def read(pause_for_writer):
            def between_queries(execute, sql, params, many, context):
                if 'manage_ownerscoringsettings' in sql.lower():
                    pause_for_writer()
                return execute(sql, params, many, context)

            with connection.execute_wrapper(between_queries):
                return scoring.policy_for(self.owner.pk, self.term)

        policy = self.interleave_rule_write(read, revision=1)
        self.assertIn(
            (policy['revision'], policy['weights']['late']),
            {(1, '1.00'), (2, '2.00')},
            'Old weights must never carry a newer revision that allows a silent overwrite.',
        )
        current = scoring.policy_for(self.owner.pk, self.term)
        self.assertEqual((current['revision'], current['weights']['late']), (2, '2.00'))

    def test_dashboard_uses_one_snapshot_across_owned_classes(self):
        self.classroom('A合成班')
        self.classroom('B合成班')
        client = Client()
        client.force_login(self.owner)
        original_report = views.class_report

        def read(pause_for_writer):
            def after_first_class(*args, **kwargs):
                report = original_report(*args, **kwargs)
                pause_for_writer()
                return report

            with patch.object(views, 'class_report', side_effect=after_first_class):
                response = client.get('/?format=json')
            self.assertEqual(response.status_code, 200)
            return response.json()['data']['dashboard_students']

        students = self.interleave_rule_write(read, revision=0)
        self.assertEqual(len(students), 2)
        self.assertEqual({item['class_id'] for item in students}, set(Class.objects.filter(owner=self.owner).values_list('pk', flat=True)))
        scores = {item['score'] for item in students}
        self.assertIn(scores, [{'59.50'}, {'59.00'}])
        for classroom in Class.objects.filter(owner=self.owner):
            self.assertEqual(scoring.class_report(classroom, self.term)['summary']['average'], '59.00')
