"""Adversarial R15 authorization tests on disposable synthetic data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import StringIO
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher, make_password
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings

from manage.models import (AuditEvent, Class, CommitteeAccount, CommitteeLoginGuard,
                           OwnerLoginGuard, PublicClassReportLink, RosterVersion, Student, Submission)
from manage.services.access import digest
from manage.services.errors import BusinessError
from manage.services.login_guard import authenticate_owner, check_login
from manage.services.public_reports import change_link
from manage.test_domain import business_day


FAST_HASH = ['django.contrib.auth.hashers.MD5PasswordHasher']


class SecurityFixtures:
    def setUp(self):
        super().setUp()
        clock = business_day('2026-10-05')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('r15-owner', password='synthetic-safe-password')
        self.classroom = Class.objects.create(owner=self.owner, classname='安全回归班', started_on=date(2026, 9, 1))
        self.student = Student.objects.create(inclass=self.classroom, number='001', name='合成学生')
        RosterVersion.objects.create(inclass=self.classroom, student=self.student,
            effective_term_start=date(2026, 9, 1), number='001', name='合成学生')
        self.client = Client()
        self.client.force_login(self.owner)
        self.url = f'/classes/{self.classroom.code}/public-report/'

    def action(self, action='enable', **overrides):
        return {'action': action, 'submission_id': str(uuid4()),
                'revision': self.client.get(self.url).json()['data']['revision'], **overrides}

    def change(self, action='enable', **overrides):
        response = self.client.post(self.url, self.action(action, **overrides), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['data']

    def expect_failure(self, role='owner', username='r15-owner', verify=lambda: None, status=401):
        with self.assertRaises(BusinessError) as caught:
            check_login(role, username, verify)
        self.assertEqual(caught.exception.status, status)
        return caught.exception


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class ReportAuthorityTests(SecurityFixtures, TestCase):
    def test_teacher_disabled_link_cannot_be_restored_by_committee_even_by_replay(self):
        account = CommitteeAccount.objects.create(inclass=self.classroom, username='classrep',
                                                  password=make_password('committee-secret'))
        committee = Client()
        self.assertEqual(committee.post('/login/', {'role': 'committee', 'username': account.username,
            'password': 'committee-secret'}, content_type='application/json').status_code, 200)
        first_payload = self.action()
        first = committee.post(self.url, first_payload, content_type='application/json').json()['data']
        self.change('disable')
        state = committee.get(self.url).json()['data']
        self.assertEqual(state['status'], 'owner_disabled')
        self.assertFalse(state['can_enable'])
        for attempt in (first_payload, self.action()):
            self.assertEqual(committee.post(self.url, attempt, content_type='application/json').status_code, 403)
        restored = self.change()
        self.assertNotEqual(restored['url'], first['url'])
        self.assertEqual(Client().get(first['url']).status_code, 404)
        self.assertEqual(Client().get(restored['url']).status_code, 200)

    def test_replay_returns_current_authority_without_retaining_old_tokens(self):
        first = self.change()
        payload = self.action('rotate')
        rotated = self.client.post(self.url, payload, content_type='application/json').json()['data']
        latest = self.change('rotate')
        replay = self.client.post(self.url, payload, content_type='application/json').json()['data']
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['url'], latest['url'])
        self.assertEqual(replay['receipt']['revision'], rotated['revision'])
        self.assertEqual(replay['revision'], latest['revision'])
        self.assertEqual(AuditEvent.objects.filter(kind='public_report_rotated').count(), 2)
        receipts = str(list(Submission.objects.values('result')))
        for old in (first, rotated, latest):
            self.assertNotIn(old['url'].split('/report/')[1].strip('/'), receipts)
        self.change('disable')
        replay = self.client.post(self.url, payload, content_type='application/json').json()['data']
        self.assertEqual(replay['status'], 'owner_disabled')
        self.assertIsNone(replay['url'])

    def test_stale_distinct_request_conflicts_and_same_id_changed_action_conflicts(self):
        self.change()
        first = self.action('rotate')
        stale = self.action('rotate')
        self.assertEqual(self.client.post(self.url, first, content_type='application/json').status_code, 200)
        response = self.client.post(self.url, stale, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'revision_conflict')
        changed = self.client.post(self.url, {**first, 'action': 'disable'}, content_type='application/json')
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()['error']['code'], 'idempotency_conflict')

    def test_disable_never_enabled_has_no_fake_link_or_receipt(self):
        response = self.client.post(self.url, self.action('disable'), content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PublicClassReportLink.objects.exists())
        self.assertFalse(Submission.objects.exists())

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_invalid_ascii_unicode_and_length_tokens_are_404(self):
        for token in ('中' * 43, 'é' * 43, 'a' * 42, 'a' * 44, '.' * 43, 'a' * 42 + ' ', 'Ａ' * 43):
            with self.subTest(token=token):
                self.assertEqual(Client().get(f'/report/{token}/').status_code, 404)

    def test_revocation_during_snapshot_read_is_rechecked(self):
        issued = self.change()
        from manage.services.report_snapshots import get_current_report
        def read_then_revoke(classroom):
            result = get_current_report(classroom)
            PublicClassReportLink.objects.filter(inclass=classroom).update(active=False)
            return result
        with patch('manage.views.get_current_report', side_effect=read_then_revoke):
            self.assertEqual(Client().get(issued['url']).status_code, 404)


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class RollingLoginTests(SecurityFixtures, TestCase):
    def test_expiring_oldest_failure_keeps_newer_failures(self):
        with patch('manage.services.login_guard.time.time', return_value=1000):
            self.expect_failure()
        with patch('manage.services.login_guard.time.time', return_value=1299):
            for _ in range(6):
                self.expect_failure()
        with patch('manage.services.login_guard.time.time', return_value=1300):
            self.expect_failure()
        with patch('manage.services.login_guard.time.time', return_value=1301):
            self.expect_failure(status=429)
        guard = OwnerLoginGuard.objects.get(username_digest=digest(self.owner.username))
        self.assertEqual(guard.failure_times, [1299] * 6 + [1300, 1301])
        self.assertEqual(guard.blocked_until, 3101)

    def test_exact_window_boundary_and_success_clear(self):
        with patch('manage.services.login_guard.time.time', return_value=1000):
            for _ in range(7):
                self.expect_failure()
        with patch('manage.services.login_guard.time.time', return_value=1300):
            self.expect_failure()
            self.assertEqual(check_login('owner', self.owner.username, lambda: self.owner), self.owner)
            self.expect_failure()
        guard = OwnerLoginGuard.objects.get(username_digest=digest(self.owner.username))
        self.assertEqual(guard.failure_times, [1300])

    def test_account_reset_or_deactivation_during_hash_rejects_candidate(self):
        def reset_then_return():
            get_user_model().objects.filter(pk=self.owner.pk).update(password=make_password('new-password'))
            return self.owner
        self.expect_failure(verify=reset_then_return)
        account = CommitteeAccount.objects.create(inclass=self.classroom, username='classrep',
                                                  password=make_password('committee-secret'))
        def disable_then_return():
            CommitteeAccount.objects.filter(pk=account.pk).update(active=False, auth_version=2)
            return account
        self.expect_failure(role='committee', username=account.username, verify=disable_then_return)

    @override_settings(PASSWORD_HASHERS=FAST_HASH + ['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_hash_upgrade_is_computed_before_atomic_and_published_only_if_current(self):
        encoded = PBKDF2PasswordHasher().encode('synthetic-safe-password', 'syntheticsalt', iterations=1)
        get_user_model().objects.filter(pk=self.owner.pk).update(password=encoded)
        candidate = authenticate_owner(None, self.owner.username, 'synthetic-safe-password')
        self.assertEqual(get_user_model().objects.get(pk=self.owner.pk).password, encoded)
        self.assertTrue(candidate._duxing_password_upgrade.startswith('md5$'))
        accepted = check_login('owner', self.owner.username, lambda: candidate)
        self.assertEqual(get_user_model().objects.get(pk=self.owner.pk).password, accepted.password)
        self.assertNotEqual(accepted.password, encoded)

    @override_settings(PASSWORD_HASHERS=FAST_HASH + ['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_hash_upgrade_retry_uses_original_hash_after_transaction_rollback(self):
        encoded = PBKDF2PasswordHasher().encode('synthetic-safe-password', 'syntheticsalt', iterations=1)
        get_user_model().objects.filter(pk=self.owner.pk).update(password=encoded)
        candidate = authenticate_owner(None, self.owner.username, 'synthetic-safe-password')
        OwnerLoginGuard.objects.create(username_digest=digest(self.owner.username))
        original = OwnerLoginGuard.save
        calls = []
        def fail_first_save(guard, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError('database is locked')
            return original(guard, *args, **kwargs)
        with patch.object(OwnerLoginGuard, 'save', new=fail_first_save):
            accepted = check_login('owner', self.owner.username, lambda: candidate)
        self.assertEqual(len(calls), 2)
        self.assertEqual(get_user_model().objects.get(pk=self.owner.pk).password, accepted.password)
        self.assertNotEqual(accepted.password, encoded)

    @override_settings(PASSWORD_HASHERS=FAST_HASH + ['django.contrib.auth.hashers.PBKDF2PasswordHasher'])
    def test_password_reset_wins_over_pending_hash_upgrade(self):
        encoded = PBKDF2PasswordHasher().encode('synthetic-safe-password', 'syntheticsalt', iterations=1)
        get_user_model().objects.filter(pk=self.owner.pk).update(password=encoded)
        candidate = authenticate_owner(None, self.owner.username, 'synthetic-safe-password')
        reset = make_password('new-reset-password')
        get_user_model().objects.filter(pk=self.owner.pk).update(password=reset)
        self.expect_failure(verify=lambda: candidate)
        self.assertEqual(get_user_model().objects.get(pk=self.owner.pk).password, reset)

    def test_unknown_names_are_normalized_and_roles_are_separate(self):
        with patch('manage.services.login_guard.time.time', return_value=1000):
            for _ in range(7):
                self.expect_failure(username=' Unknown ')
            self.expect_failure(username='UNKNOWN', status=429)
            self.expect_failure(role='committee', username='unknown')
        self.assertEqual(OwnerLoginGuard.objects.count(), 1)
        self.assertEqual(CommitteeLoginGuard.objects.get().failures, 1)

    def test_cleanup_keeps_recent_failures_and_active_blocks(self):
        with patch('manage.services.login_guard.time.time', return_value=1000):
            self.expect_failure(username='expired')
            for _ in range(7):
                self.expect_failure(username='blocked')
            self.expect_failure(username='blocked', status=429)
        with patch('manage.services.login_guard.time.time', return_value=1299):
            self.expect_failure(username='recent')
        with patch('manage.management.commands.cleanup_login_guards.time.time', return_value=1300):
            call_command('cleanup_login_guards', stdout=StringIO())
        self.assertEqual(set(OwnerLoginGuard.objects.values_list('username_digest', flat=True)),
                         {digest('blocked'), digest('recent')})


@override_settings(PASSWORD_HASHERS=FAST_HASH)
class SecurityConcurrencyTests(SecurityFixtures, TransactionTestCase):
    def thread_attempt(self, verify):
        close_old_connections()
        try:
            check_login('owner', self.owner.username, verify)
            return 200
        except BusinessError as error:
            return error.status
        finally:
            close_old_connections()

    def test_eight_simultaneous_failures_count_once_each(self):
        barrier = Barrier(8)
        def verify():
            self.assertFalse(connection.in_atomic_block)
            barrier.wait(timeout=10)
            return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda _: self.thread_attempt(verify), range(8)))
        self.assertEqual(sorted(statuses), [401] * 7 + [429])
        self.assertEqual(OwnerLoginGuard.objects.get().failures, 8)

    def test_inflight_correct_password_loses_to_eighth_failure(self):
        for _ in range(7):
            self.expect_failure()
        entered, finish = Event(), Event()
        def verify():
            entered.set()
            if not finish.wait(10):
                raise AssertionError('Timed out awaiting synthetic hash completion.')
            return self.owner
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.thread_attempt, verify)
            self.assertTrue(entered.wait(5))
            try:
                self.expect_failure(status=429)
            finally:
                finish.set()
            self.assertEqual(pending.result(timeout=10), 429)

    def test_real_owner_verification_does_not_lock_unrelated_business_write(self):
        entered, finish = Event(), Event()
        def delayed_hash(*args):
            self.assertFalse(connection.in_atomic_block)
            entered.set()
            if not finish.wait(10):
                raise AssertionError('Timed out awaiting synthetic hash completion.')
            return True, False
        with patch('manage.services.login_guard.verify_password', side_effect=delayed_hash):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.thread_attempt, lambda: authenticate_owner(
                    None, self.owner.username, 'synthetic-safe-password'))
                self.assertTrue(entered.wait(5))
                try:
                    self.assertEqual(Class.objects.filter(pk=self.classroom.pk).update(classname='并发写入成功'), 1)
                finally:
                    finish.set()
                self.assertEqual(pending.result(timeout=10), 200)

    def test_same_link_version_two_rotations_only_one_wins(self):
        self.change()
        payloads = [self.action('rotate'), self.action('rotate')]
        barrier = Barrier(2)
        def rotate(payload):
            close_old_connections()
            request = RequestFactory().post(self.url)
            request.user = self.owner
            barrier.wait(timeout=5)
            try:
                return 200, change_link(request, self.classroom, payload)
            except BusinessError as error:
                return error.status, None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(rotate, payloads))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(AuditEvent.objects.filter(kind='public_report_rotated').count(), 1)
        current = self.client.get(self.url).json()['data']
        self.assertEqual(next(data['url'] for status, data in results if status == 200), current['url'])
