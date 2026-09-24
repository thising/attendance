"""Synthetic acceptance tests for authorization and reliable record writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from threading import Barrier
import time
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import IntegrityError, close_old_connections, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings

from manage.models import Activity, AuditEvent, Class, ClassDailyUsage, CommitteeAccount, Report, Student, Submission, SummaryCount
from manage.services import records, roster
from manage.services.calendar import Term
from manage.services.errors import BusinessError
from manage.services.scoring import policy_for
from manage.test_domain import business_day


class AccessFixtures:
    password = "fixture-class-password"
    today = date(2026, 10, 15)
    term_key = "2026-autumn"

    def setUp(self):
        super().setUp()
        clock = business_day(self.today)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        users = get_user_model()
        self.owner = users.objects.create_user("access-owner", password="fixture-owner-password")
        self.other_owner = users.objects.create_user("other-owner", password="fixture-other-password")
        self.classroom = self.make_class(self.owner, "合成一班")
        self.other_class = self.make_class(self.other_owner, "合成二班")
        self.committee_account = CommitteeAccount.objects.create(
            inclass=self.classroom, username="access-committee", password=make_password(self.password))
        self.student = self.add_student(self.classroom, self.owner)
        self.other_student = self.add_student(self.other_class, self.other_owner)
        self.classroom.refresh_from_db()
        self.other_class.refresh_from_db()

    def make_class(self, owner, name):
        return Class.objects.create(classname=name, owner=owner,
            managecode=make_password(self.password), started_on=date(2026, 9, 1))

    def request(self, user=None, session=None):
        request = RequestFactory().post("/synthetic-record/", content_type="application/json")
        request.user = user or self.owner
        SessionMiddleware(lambda value: None).process_request(request)
        if session is not None:
            request.session = session
        return request

    def add_student(self, classroom, owner):
        result = roster.change_roster(self.request(owner), classroom.code, {
            "action": "add", "student": {"number": "001", "name": "合成成员", "sex": "male"},
            "term_key": self.term_key, "submission_id": str(uuid4()), "revision": classroom.revision,
        })
        return Student.objects.get(pk=result["id"])

    def record_url(self, classroom=None, record_id=None):
        code = (classroom or self.classroom).code
        return f"/classes/{code}/records/{record_id}/" if record_id else f"/classes/{code}/records/new/"

    def record_payload(self, classroom=None, student=None, name="合成点名", occurred_on="2026-10-15"):
        classroom = classroom or self.classroom
        student = student or self.student
        classroom.refresh_from_db()
        return {
            "term_key": self.term_key, "submission_id": str(uuid4()),
            "revision": 0, "roster_revision": classroom.revision,
            "record": {"kind": "class", "name": name, "date": occurred_on,
                       "students": [{"id": student.pk, "value": "late"}]},
        }

    def create_record(self, classroom=None, student=None, owner=None):
        classroom = classroom or self.classroom
        return records.save_record(self.request(owner), classroom.code,
            self.record_payload(classroom, student))

    def owner_client(self, owner=None, enforce_csrf=False):
        client = Client(enforce_csrf_checks=enforce_csrf)
        client.force_login(owner or self.owner)
        return client

    def committee_client(self):
        client = Client()
        response = client.post("/login/", self.committee_credentials(), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return client

    def committee_credentials(self, password=None):
        return {"role": "committee", "username": self.committee_account.username,
                "password": self.password if password is None else password}

    def delete_payload(self, record):
        return {"action": "delete", "term_key": self.term_key,
                "submission_id": str(uuid4()), "revision": record["revision"]}

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertFalse(response.json()["ok"])
        self.assertEqual(response.json()["error"]["code"], code)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AccessAndRecordTests(AccessFixtures, TestCase):
    def test_optional_details_preserve_legacy_name_and_old_edit_clients(self):
        client = self.owner_client()
        payload = self.record_payload(name='旧记录名称保留完整，超过原来的三十字符限制，可以直接修正学生状态与详情')
        payload['record']['details'] = '地点：设计楼；说明：补充课程信息。'
        response = client.post(self.record_url(), payload, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        activity = Activity.objects.get(pk=response.json()['data']['id'])
        self.assertEqual(activity.name, payload['record']['name'])
        self.assertEqual(activity.details, payload['record']['details'])
        page = client.get(self.record_url(record_id=activity.pk) + '?format=json').json()['data']
        self.assertEqual(page['record']['details'], activity.details)
        edit = self.record_payload(name='修正后的名称')
        edit['revision'] = activity.revision
        response = client.post(self.record_url(record_id=activity.pk), edit, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        activity.refresh_from_db()
        self.assertEqual(activity.details, payload['record']['details'])
        invalid = self.record_payload()
        invalid['record']['details'] = '字' * 501
        self.assert_error(client.post(self.record_url(), invalid, content_type='application/json'), 400,
                          'invalid_record_details')
        invalid_name = self.record_payload(name='字' * 65)
        self.assert_error(client.post(self.record_url(), invalid_name, content_type='application/json'), 400,
                          'invalid_record')

    def test_create_and_edit_role_matrix(self):
        clients = {
            "anonymous": (Client(), 401),
            "other_owner": (self.owner_client(self.other_owner), 403),
            "committee": (self.committee_client(), 200),
            "owner": (self.owner_client(), 200),
        }
        for role, (client, expected) in clients.items():
            with self.subTest(role=role, action="create"):
                before = Activity.objects.count()
                response = client.post(self.record_url(), self.record_payload(), content_type="application/json")
                self.assertEqual(response.status_code, expected, response.content)
                self.assertEqual(Activity.objects.count(), before + (expected == 200))
            original = self.create_record()
            payload = self.record_payload(name="合成修改")
            payload["revision"] = original["revision"]
            with self.subTest(role=role, action="edit"):
                response = client.post(self.record_url(record_id=original["id"]), payload, content_type="application/json")
                self.assertEqual(response.status_code, expected, response.content)
                activity = Activity.objects.get(pk=original["id"])
                self.assertEqual(activity.name, "合成修改" if expected == 200 else "合成点名")

    def test_delete_and_roster_are_owner_only(self):
        clients = {
            "anonymous": (Client(), 403),
            "other_owner": (self.owner_client(self.other_owner), 403),
            "committee": (self.committee_client(), 403),
            "owner": (self.owner_client(), 200),
        }
        for role, (client, expected) in clients.items():
            original = self.create_record()
            with self.subTest(role=role, action="delete"):
                response = client.post(self.record_url(record_id=original["id"]),
                    self.delete_payload(original), content_type="application/json")
                self.assertEqual(response.status_code, expected, response.content)
                self.assertEqual(Activity.objects.filter(pk=original["id"]).exists(), expected != 200)
            self.classroom.refresh_from_db()
            with self.subTest(role=role, action="roster"):
                response = client.post(f"/classes/{self.classroom.code}/roster/", {
                    "action": "add", "student": {"number": "new-" + role, "name": "合成新增"},
                    "revision": self.classroom.revision, "term_key": self.term_key, "submission_id": str(uuid4()),
                }, content_type="application/json")
                self.assertEqual(response.status_code, expected, response.content)
                self.assertEqual(Student.objects.filter(inclass=self.classroom, number="new-" + role).exists(), expected == 200)

    def test_class_log_is_owner_only(self):
        for role, client, expected in (
            ("anonymous", Client(), 403), ("other_owner", self.owner_client(self.other_owner), 403),
            ("committee", self.committee_client(), 403), ("owner", self.owner_client(), 200),
        ):
            with self.subTest(role=role):
                response = client.get(f"/classes/{self.classroom.code}/events/?format=json")
                self.assertEqual(response.status_code, expected, response.content)

    def test_rules_require_owner_identity_and_use_requesting_owners_scope(self):
        original = policy_for(self.owner.pk, Term(2026, "autumn"))
        payload = {"weights": {**original["weights"], "late": "3.00"}, "revision": 0,
            "term_key": self.term_key, "submission_id": str(uuid4()), "action": "save"}
        for role, client in (("anonymous", Client()), ("committee", self.committee_client())):
            with self.subTest(role=role):
                self.assert_error(client.post("/rules/", payload, content_type="application/json"), 401, "authentication_required")
        # An authenticated second owner may update their own defaults, even if
        # an untrusted request includes another owner's ID.
        payload["owner_id"] = self.owner.pk
        response = self.owner_client(self.other_owner).post("/rules/", payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(policy_for(self.owner.pk, Term(2026, "autumn"))["weights"], original["weights"])
        self.assertEqual(policy_for(self.other_owner.pk, Term(2026, "autumn"))["weights"]["late"], "3.00")

    def test_cross_class_activity_cannot_be_modified_or_deleted(self):
        original = self.create_record(self.other_class, self.other_student, self.other_owner)
        client = self.owner_client()
        edit = self.record_payload(name="越界内容")
        edit["revision"] = original["revision"]
        for action, payload in (("edit", edit), ("delete", self.delete_payload(original))):
            with self.subTest(action=action):
                response = client.post(self.record_url(record_id=original["id"]), payload, content_type="application/json")
                self.assert_error(response, 404, "not_found")
        self.assertEqual(Activity.objects.get(pk=original["id"]).name, "合成点名")

    def test_cross_class_student_is_rejected_before_any_record_write(self):
        before = (Activity.objects.count(), Report.objects.count(), AuditEvent.objects.count())
        payload = self.record_payload(student=self.other_student)
        response = self.owner_client().post(self.record_url(), payload, content_type="application/json")
        self.assert_error(response, 409, "roster_changed")
        self.assertEqual((Activity.objects.count(), Report.objects.count(), AuditEvent.objects.count()), before)

    def test_cross_class_student_cannot_be_renamed_by_roster_request(self):
        response = self.owner_client().post(f"/classes/{self.classroom.code}/roster/", {
            "action": "edit", "student": {"id": self.other_student.pk, "number": "001", "name": "越界姓名"},
            "revision": self.classroom.revision, "term_key": self.term_key, "submission_id": str(uuid4()),
        }, content_type="application/json")
        self.assert_error(response, 404, "not_found")
        self.other_student.refresh_from_db()
        self.assertEqual(self.other_student.name, "合成成员")

    def test_committee_login_is_fixed_three_hours_and_expires_at_boundary(self):
        started = time.time()
        client = Client()
        session = client.session
        session["unrelated"] = "synthetic"
        session.save()
        previous_key = client.session.session_key
        with patch("manage.services.committee.time.time", return_value=started):
            response = client.post("/login/", self.committee_credentials(), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(client.session.session_key, previous_key)
        expires = client.session["committee_auth"]["expires_at"]
        self.assertEqual(expires, started + 3 * 60 * 60)
        for elapsed in (60, 7200, 10799):
            with self.subTest(elapsed=elapsed), patch("manage.services.access.time.time", return_value=started + elapsed):
                response = client.post(self.record_url(), self.record_payload(), content_type="application/json")
                self.assertEqual(response.status_code, 200, response.content)
                client.get(f"/classes/{self.classroom.code}/?format=json")
                self.assertEqual(client.session["committee_auth"]["expires_at"], expires)
        with patch("manage.services.access.time.time", return_value=expires):
            response = client.post(self.record_url(), self.record_payload(), content_type="application/json")
        self.assert_error(response, 401, "class_authorization_required")
        self.assertEqual(Activity.objects.count(), 3)

    def test_logout_invalidates_committee_login_even_for_copied_cookie(self):
        committee = self.committee_client()
        replay = Client()
        replay.cookies[settings.SESSION_COOKIE_NAME] = committee.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertEqual(committee.post("/logout/").status_code, 200)
        for client in (committee, replay):
            response = client.post(self.record_url(), self.record_payload(), content_type="application/json")
            self.assert_error(response, 401, "class_authorization_required")

    def test_owner_password_reset_revokes_independent_committee_session(self):
        committee = self.committee_client()
        owner = self.owner_client()
        replacement = "another-synthetic-account-password"
        response = owner.post(f"/classes/{self.classroom.code}/committee/", {
            "action": "reset_password", "id": self.committee_account.pk,
            "password": replacement, "revision": self.committee_account.revision,
            "term_key": self.term_key, "submission_id": str(uuid4()),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        response = committee.post(self.record_url(), self.record_payload(), content_type="application/json")
        self.assert_error(response, 401, "class_authorization_required")
        replacement_client = Client()
        url = "/login/"
        self.assert_error(replacement_client.post(url, self.committee_credentials(), content_type="application/json"), 401, "invalid_credentials")
        self.assertEqual(replacement_client.post(url, self.committee_credentials(replacement), content_type="application/json").status_code, 200)
        self.assertEqual(replacement_client.post(self.record_url(), self.record_payload(), content_type="application/json").status_code, 200)

    def test_csrf_is_required_even_for_authenticated_owner(self):
        client = self.owner_client(enforce_csrf=True)
        self.assertEqual(client.get("/?format=json").status_code, 200)
        payload = self.record_payload()
        response = client.post(self.record_url(), payload, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Activity.objects.count(), 0)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(self.record_url(), payload, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Activity.objects.count(), 1)

    def test_get_cannot_authorize_or_logout_and_legacy_posts_are_retired(self):
        client = self.owner_client()
        for path in ("/logout/", f"/classes/{self.classroom.code}/authorize/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 405)
        for path in ("/save_activity.html", "/release_activity.html", "/remove_activity.html", "/students-clear-all.html"):
            with self.subTest(path=path):
                self.assert_error(client.post(path), 410, "legacy_endpoint_retired")

    def test_replayed_submission_has_one_fact_cache_and_audit(self):
        client = self.owner_client()
        payload = self.record_payload()
        before = Submission.objects.count()
        first = client.post(self.record_url(), payload, content_type="application/json")
        second = client.post(self.record_url(), payload, content_type="application/json")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertFalse(first.json()["data"]["replayed"])
        self.assertTrue(second.json()["data"]["replayed"])
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), before + 1)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 1)
        self.assertEqual(SummaryCount.objects.get(student=self.student, year=2026, month=10).late_count, 1)

    def test_same_key_different_payload_conflicts_but_independent_duplicate_content_is_allowed(self):
        client = self.owner_client()
        original = self.record_payload()
        self.assertEqual(client.post(self.record_url(), original, content_type="application/json").status_code, 200)
        conflict = deepcopy(original)
        conflict["record"]["name"] = "改过的请求内容"
        self.assert_error(client.post(self.record_url(), conflict, content_type="application/json"), 409, "idempotency_conflict")
        independent = deepcopy(original)
        independent["submission_id"] = str(uuid4())
        self.assertEqual(client.post(self.record_url(), independent, content_type="application/json").status_code, 200)
        self.assertEqual(Activity.objects.count(), 2)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 2)

    def test_original_create_retry_does_not_resurrect_deleted_result(self):
        client = self.owner_client()
        payload = self.record_payload()
        original = client.post(self.record_url(), payload, content_type="application/json").json()["data"]
        response = client.post(self.record_url(record_id=original["id"]), self.delete_payload(original), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        retry = client.post(self.record_url(), payload, content_type="application/json")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertTrue(retry.json()["data"]["replayed"])
        self.assertTrue(retry.json()["data"]["deleted"])
        self.assertEqual(Activity.objects.count(), 0)
        self.assertEqual(Report.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="record_deleted").count(), 1)
        removed = AuditEvent.objects.get(kind="record_deleted")
        self.assertIn(payload['record']['name'], removed.summary)
        self.assertIn(payload['record']['date'], removed.summary)
        self.assertEqual(removed.object_id, original['id'])

    def test_stale_record_revision_is_rejected_without_overwriting_newer_edit(self):
        client = self.owner_client()
        original = self.create_record()
        edit = self.record_payload(name="先到的修改")
        edit["revision"] = original["revision"]
        self.assertEqual(client.post(self.record_url(record_id=original["id"]), edit, content_type="application/json").status_code, 200)
        stale = self.record_payload(name="不应覆盖的新内容")
        stale["revision"] = original["revision"]
        response = client.post(self.record_url(record_id=original["id"]), stale, content_type="application/json")
        self.assert_error(response, 409, "revision_conflict")
        self.assertEqual(Activity.objects.get(pk=original["id"]).name, "先到的修改")
        self.assertEqual(AuditEvent.objects.filter(kind="record_updated").count(), 1)

    def test_stale_roster_revision_is_rejected_even_when_student_ids_are_unchanged(self):
        payload = self.record_payload()
        roster.change_roster(self.request(), self.classroom.code, {
            "action": "edit", "student": {"id": self.student.pk, "number": "001", "name": "已改名"},
            "term_key": self.term_key, "submission_id": str(uuid4()), "revision": self.classroom.revision,
        })
        response = self.owner_client().post(self.record_url(), payload, content_type="application/json")
        self.assert_error(response, 409, "roster_changed")
        self.assertEqual(Activity.objects.count(), 0)

    def test_omitting_roster_revision_cannot_bypass_snapshot_validation(self):
        client = self.owner_client()
        original = self.create_record()
        for record_id in (None, original["id"]):
            with self.subTest(record_id=record_id):
                payload = self.record_payload(name="缺失名单版本")
                payload.pop("roster_revision")
                payload["revision"] = original["revision"] if record_id else 0
                response = client.post(self.record_url(record_id=record_id), payload, content_type="application/json")
                self.assert_error(response, 409, "roster_changed")
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(Activity.objects.get(pk=original["id"]).name, "合成点名")

    def test_audit_failure_rolls_back_facts_submission_usage_and_cache(self):
        models = (Activity, Report, SummaryCount, Submission, ClassDailyUsage, AuditEvent)
        before = [model.objects.count() for model in models]
        with patch("manage.services.writes.AuditEvent.objects.create", side_effect=IntegrityError("synthetic audit failure")):
            with self.assertRaises(IntegrityError):
                records.save_record(self.request(), self.classroom.code, self.record_payload())
        self.assertEqual([model.objects.count() for model in models], before)

    def test_quota_uses_server_creation_day_and_owner_is_exempt(self):
        ClassDailyUsage.objects.create(inclass=self.classroom, day=self.today, created_count=29)
        committee = self.committee_client()
        backdated = self.record_payload(occurred_on="2026-09-01")
        self.assertEqual(committee.post(self.record_url(), backdated, content_type="application/json").status_code, 200)
        another_date = self.record_payload(occurred_on="2026-10-01")
        response = committee.post(self.record_url(), another_date, content_type="application/json")
        self.assert_error(response, 429, "daily_quota_exceeded")
        usage = ClassDailyUsage.objects.get(inclass=self.classroom, day=self.today)
        self.assertEqual(usage.created_count, 30)
        self.assertEqual(ClassDailyUsage.objects.filter(inclass=self.classroom).count(), 1)
        response = self.owner_client().post(self.record_url(), another_date, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Activity.objects.count(), 2)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConcurrentRecordTests(AccessFixtures, TransactionTestCase):
    def run_concurrently(self, payloads, committee=False):
        barrier = Barrier(len(payloads))
        owner, code = self.owner, self.classroom.code
        account_id, auth_version = self.committee_account.pk, self.committee_account.auth_version

        def submit(payload):
            close_old_connections()
            try:
                session = {}
                user = owner
                if committee:
                    user = AnonymousUser()
                    session = {"committee_auth": {"account_id": account_id,
                        "version": auth_version, "expires_at": time.time() + 3600}}
                request = self.request(user, session=session)
                barrier.wait(timeout=5)
                try:
                    return {"result": records.save_record(request, code, payload)}
                except BusinessError as error:
                    return {"error": error.code, "status": error.status}
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            futures = [pool.submit(submit, payload) for payload in payloads]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_same_key_commits_exactly_once(self):
        self.assertEqual(connections["default"].settings_dict["OPTIONS"]["transaction_mode"], "IMMEDIATE")
        payload = self.record_payload()
        before = Submission.objects.count()
        outcomes = self.run_concurrently([payload, deepcopy(payload)])
        self.assertTrue(all("result" in outcome for outcome in outcomes), outcomes)
        self.assertEqual(sorted(outcome["result"]["replayed"] for outcome in outcomes), [False, True])
        self.assertEqual(len({outcome["result"]["id"] for outcome in outcomes}), 1)
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), before + 1)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 1)
        self.assertEqual(SummaryCount.objects.get(student=self.student, year=2026, month=10).late_count, 1)

    def test_concurrent_quota_twenty_nine_allows_only_one_independent_create(self):
        ClassDailyUsage.objects.create(inclass=self.classroom, day=self.today, created_count=29)
        outcomes = self.run_concurrently([
            self.record_payload(name="并发请求一", occurred_on="2026-09-01"),
            self.record_payload(name="并发请求二", occurred_on="2026-10-01"),
        ], committee=True)
        self.assertEqual(sum("result" in outcome for outcome in outcomes), 1, outcomes)
        self.assertEqual([outcome.get("error") for outcome in outcomes if "error" in outcome], ["daily_quota_exceeded"])
        self.assertEqual(ClassDailyUsage.objects.get(inclass=self.classroom, day=self.today).created_count, 30)
        self.assertEqual(Activity.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 1)
