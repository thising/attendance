"""Credential redaction and atomic key publication, using synthetic secrets only."""
import ast
import base64
import os
from pathlib import Path
import tempfile
from threading import Event, Thread
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.db import IntegrityError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.views.debug import ExceptionReporter, SafeExceptionReporterFilter

from manage import views
from manage.models import AuditEvent, CommitteeAccount, Submission
from manage.services import committee, credential_store
from manage.test_named_committee import AccountFixtures


@override_settings(DEBUG=False, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CredentialExceptionSafetyTests(AccountFixtures, TestCase):
    def setUp(self):
        super().setUp()
        # No real runtime key is read by the test: each case owns an ephemeral key.
        synthetic_key = override_settings(DUXING_CREDENTIAL_KEY=Fernet.generate_key().decode())
        synthetic_key.enable()
        self.addCleanup(synthetic_key.disable)

    def request(self, path, body):
        request = RequestFactory().post(path, body, content_type='application/json')
        request.user = self.owner
        request.session = {}
        return request

    def assert_redacted_exception(self, request, callback, secret, expected=IntegrityError):
        try:
            callback()
        except expected as error:
            traceback = error.__traceback__
            application_root = Path(__file__).resolve().parent
            test_file = Path(__file__).resolve()
            # Exclude the test caller holding the synthetic request itself. The
            # captured traceback begins at the real view/service entry point.
            while traceback:
                filename = Path(traceback.tb_frame.f_code.co_filename).resolve()
                if application_root in filename.parents and filename != test_file:
                    break
                traceback = traceback.tb_next
            self.assertIsNotNone(traceback, 'The injected failure must traverse application code.')
            first = traceback
            filtered_frames = []
            raw_application_secret_seen = False
            report_filter = SafeExceptionReporterFilter()
            while traceback:
                frame = traceback.tb_frame
                filename = Path(frame.f_code.co_filename).resolve()
                if application_root in filename.parents and filename != test_file:
                    raw_application_secret_seen |= any(secret in repr(value) for value in frame.f_locals.values())
                    leaks = [name for name, value in report_filter.get_traceback_frame_variables(request, frame)
                             if secret in repr(value)]
                    if leaks:
                        filtered_frames.append((frame.f_code.co_name, leaks))
                traceback = traceback.tb_next
            self.assertTrue(raw_application_secret_seen, 'The regression must actually exercise a plaintext local.')
            self.assertEqual(filtered_frames, [], 'Filtered application frames retained a credential.')
            html = ExceptionReporter(request, type(error), error, first).get_traceback_html()
            self.assertFalse(secret in html, 'Production HTML exception report retained a synthetic credential.')
        else:
            self.fail('The injected unhandled failure did not occur.')

    def test_json_create_audit_failure_redacts_payload_and_rolls_back(self):
        body = self.action(username='redaction-create', password=self.password)
        request = self.request(self.account_url(), body)
        before = CommitteeAccount.objects.count()
        with patch('manage.services.writes.AuditEvent.objects.create', side_effect=IntegrityError('synthetic audit failure')):
            self.assert_redacted_exception(request, lambda: views.committee_accounts(request, self.c.code), self.password)
        self.assertEqual(CommitteeAccount.objects.count(), before)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_json_reset_audit_failure_redacts_password_and_restores_hash_ciphertext_and_revision(self):
        credential_store.store_password(self.a, self.password)
        before = (self.a.password, self.a.credential_ciphertext, self.a.auth_version, self.a.revision)
        replacement = 'synthetic-new-secret-for-review'
        body = self.action('reset_password', id=self.a.pk, revision=self.a.revision, password=replacement)
        request = self.request(self.account_url(), body)
        with patch('manage.services.writes.AuditEvent.objects.create', side_effect=IntegrityError('synthetic audit failure')):
            self.assert_redacted_exception(request, lambda: views.committee_accounts(request, self.c.code), replacement)
        self.a.refresh_from_db()
        self.assertEqual((self.a.password, self.a.credential_ciphertext, self.a.auth_version, self.a.revision), before)
        self.assertEqual(credential_store.read_password(self.a), self.password)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_service_entry_without_http_decorator_still_redacts_secret(self):
        body = self.action(username='direct-redaction', password=self.password)
        request = self.request(self.account_url(), body)
        with patch('manage.services.writes.AuditEvent.objects.create', side_effect=IntegrityError('synthetic audit failure')):
            self.assert_redacted_exception(request, lambda: committee.manage_accounts(request, self.c.code, body), self.password)

    def test_copy_audit_failure_cannot_leak_decrypted_password_in_exception_report(self):
        credential_store.store_password(self.a, self.password)
        request = self.request(f'/classes/{self.c.code}/committee/credentials/', {'id': self.a.pk})
        with patch('manage.services.writes.AuditEvent.objects.create', side_effect=IntegrityError('synthetic audit failure')):
            self.assert_redacted_exception(request, lambda: views.committee_credentials(request, self.c.code), self.password)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_json_teacher_and_committee_login_failures_redact_password(self):
        for role, username, target in (
            ('owner', self.owner.username, 'manage.views.authenticate'),
            ('committee', self.a.username, 'manage.models.CommitteeAccount.check_password'),
        ):
            with self.subTest(role=role):
                request = self.request('/login/', {'role': role, 'username': username, 'password': self.password})
                with patch(target, side_effect=RuntimeError('synthetic authentication failure')):
                    self.assert_redacted_exception(request, lambda: views.user_login(request), self.password, RuntimeError)

    def test_direct_decryption_failure_redacts_decoded_document(self):
        credential_store.store_password(self.a, self.password)
        request = self.request('/synthetic-service/', {})
        with patch.object(self.a, 'check_password', side_effect=RuntimeError('synthetic hash failure')):
            self.assert_redacted_exception(request, lambda: credential_store.read_password(self.a), self.password, RuntimeError)


class CredentialKeyPublicationTests(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='attendance-key-safety-', dir='/private/tmp')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.key_path = self.directory / '.local' / 'credential.key'
        runtime = Path(__file__).resolve().parents[1] / 'attendance' / 'runtime_settings.py'
        parsed = ast.parse(runtime.read_text())
        prefix = []
        for node in parsed.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ALLOWED_HOSTS' for target in node.targets):
                break
            prefix.append(node)
        else:
            self.fail('Runtime key initialization boundary moved; review the isolated harness.')
        # Only stdlib imports and key initialization execute. __file__ is below
        # the disposable directory, so BASE_DIR and every key file stay in /tmp.
        self.settings_prefix = compile(ast.Module(body=prefix, type_ignores=[]), str(runtime), 'exec')
        environment = patch.dict(os.environ, {'DUXING_DEBUG': '1', 'DUXING_CREDENTIAL_KEY': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def load_settings(self):
        namespace = {'__file__': str(self.directory / 'attendance' / 'runtime_settings.py')}
        exec(self.settings_prefix, namespace)
        self.assertEqual(namespace['BASE_DIR'], self.directory)
        return namespace['DUXING_CREDENTIAL_KEY']

    def test_parallel_initialization_never_publishes_empty_key_or_overwrites_winner(self):
        first_entered, release_first = Event(), Event()
        original_fdopen = os.fdopen
        results, failures = {}, []

        class PausedFirstWriter:
            def __init__(self, descriptor, mode):
                self.file = original_fdopen(descriptor, mode)

            def __enter__(self):
                self.file.__enter__()
                first_entered.set()
                if not release_first.wait(5):
                    raise RuntimeError('Synthetic worker synchronization timed out.')
                return self.file

            def __exit__(self, *arguments):
                return self.file.__exit__(*arguments)

        def first_worker():
            try:
                # Patch only this worker's first fdopen call; the second worker
                # must be able to write and publish while the first is paused.
                results['first'] = self.load_settings()
            except BaseException as error:
                failures.append(type(error).__name__)

        from threading import current_thread

        def fdopen(descriptor, mode):
            if current_thread().name == 'credential-key-first':
                return PausedFirstWriter(descriptor, mode)
            return original_fdopen(descriptor, mode)

        with patch('os.fdopen', fdopen):
            worker = Thread(target=first_worker, name='credential-key-first')
            worker.start()
            try:
                self.assertTrue(first_entered.wait(5))
                self.assertFalse(self.key_path.exists(), 'An incomplete key was made visible.')
                results['second'] = self.load_settings()
                self.assertEqual(len(base64.urlsafe_b64decode(results['second'])), 32)
            finally:
                release_first.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(results['first'], results['second'])
        self.assertEqual(self.key_path.read_text(), results['second'])
        self.assertEqual(self.key_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([path.name for path in self.key_path.parent.iterdir()], ['credential.key'])

    def test_existing_key_is_reused_without_regeneration(self):
        self.key_path.parent.mkdir()
        key = Fernet.generate_key().decode()
        self.key_path.write_text(key)
        self.key_path.chmod(0o600)
        with patch('os.urandom', side_effect=AssertionError('Existing key must not be regenerated.')):
            self.assertEqual(self.load_settings(), key)
        self.assertEqual(self.key_path.read_text(), key)

    def test_empty_corrupt_or_wrong_length_existing_key_fails_without_replacing_it(self):
        self.key_path.parent.mkdir()
        for invalid in ('', 'not-base64', base64.urlsafe_b64encode(b'short').decode()):
            with self.subTest(length=len(invalid)):
                self.key_path.write_text(invalid)
                self.key_path.chmod(0o600)
                with self.assertRaisesRegex(RuntimeError, 'restore the matching key backup'):
                    self.load_settings()
                self.assertEqual(self.key_path.read_text(), invalid)

    def test_explicit_ephemeral_environment_key_does_not_read_or_create_local_key(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'DUXING_CREDENTIAL_KEY': key}), \
                patch('pathlib.Path.read_text', side_effect=AssertionError('Environment configuration needs no key file.')):
            self.assertEqual(self.load_settings(), key)
        self.assertFalse(self.key_path.parent.exists())
