"""Excel upload/confirmation HTTP acceptance using only isolated test fixtures."""
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from manage.models import (
    Activity, AuditEvent, Class, Report, RosterVersion, ScoringPolicyVersion,
    Student, Submission, SummaryCount,
)
from manage.test_domain import business_day
from manage.test_named_committee import AccountFixtures
from manage.test_student_import import record, workbook


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentImportHTTPTests(AccountFixtures, TestCase):
    term_key = "2026-autumn"

    def import_url(self, classroom=None):
        return f"/classes/{(classroom or self.c).code}/roster/import/"

    def roster_url(self, classroom=None):
        return f"/classes/{(classroom or self.c).code}/roster/"

    def legacy_template(self):
        # This tracked template contains six fictional examples, never live data.
        path = Path(settings.BASE_DIR) / "manage/static/downloads/ams-template-add-students.xlsx"
        return SimpleUploadedFile(path.name, path.read_bytes())

    def upload_fields(self, upload=None, **values):
        self.c.refresh_from_db()
        return {"file": upload or workbook(), "term_key": self.term_key,
                "revision": str(self.c.revision), **values}

    def roster_payload(self, text="001|合成姓名|男", **values):
        self.c.refresh_from_db()
        return {"action": "bulk_add", "students_text": text, "term_key": self.term_key,
                "revision": self.c.revision, "submission_id": str(uuid4()), **values}

    def snapshot(self):
        return {
            "class_revisions": list(Class.objects.order_by("id").values_list("id", "revision")),
            **{model.__name__: model.objects.count() for model in (
                Student, RosterVersion, Activity, Report, SummaryCount, AuditEvent,
                Submission, ScoringPolicyVersion,
            )},
        }

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["code"], code)

    def assert_no_writes(self, queries):
        writes = [query["sql"] for query in queries
                  if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))]
        self.assertEqual(writes, [])

    def test_original_template_returns_six_person_preview_without_any_database_writes(self):
        before = self.snapshot()
        fields = self.upload_fields(self.legacy_template(), action="bulk_add", submission_id=str(uuid4()))
        with CaptureQueriesContext(connection) as queries:
            response = self.owner_client.post(self.import_url(), fields)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual((data["count"], len(data["students"])), (6, 6))
        self.assertEqual([student["number"] for student in data["students"]],
                         [f"20060410{number}" for number in range(1, 7)])
        self.assertEqual(data["students_text"].splitlines()[0], "200604101|张三|男")
        self.assertNotIn("#导入系统", data["students_text"])
        self.assertEqual(self.snapshot(), before)
        self.assert_no_writes(queries)

    def test_confirming_uploaded_preview_creates_one_batch_and_retry_is_idempotent(self):
        preview = self.owner_client.post(self.import_url(), self.upload_fields(self.legacy_template()))
        self.assertEqual(preview.status_code, 200)
        payload = self.roster_payload(preview.json()["data"]["students_text"])
        response = self.owner_client.post(self.roster_url(), payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual((data["count"], data["revision"], data["replayed"]), (6, 2, False))
        self.assertEqual(Student.objects.filter(inclass=self.c).count(), 6)
        self.assertEqual(Student.objects.exclude(inclass=self.c).count(), 0)
        versions = RosterVersion.objects.filter(inclass=self.c)
        self.assertEqual(versions.count(), 6)
        self.assertEqual(set(versions.values_list("effective_term_start", flat=True)), {date(2026, 9, 1)})
        audit = AuditEvent.objects.get(kind="roster_add")
        self.assertEqual((audit.inclass_id, audit.actor_id, audit.affected_count, audit.revision),
                         (self.c.pk, self.owner.pk, 6, 2))
        self.assertEqual(Submission.objects.count(), 1)
        before_retry = self.snapshot()
        retry = self.owner_client.post(self.roster_url(), payload, content_type="application/json")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertTrue(retry.json()["data"]["replayed"])
        self.assertEqual(self.snapshot(), before_retry)

    def test_excel_pasted_tsv_has_same_preview_and_atomic_confirmation_contract(self):
        text = " 0001\t 合成甲 \t女\r\n\r\n0002\t合成乙\tmale\r\n"
        before = self.snapshot()
        preview = self.owner_client.post(self.roster_url(), self.roster_payload(text, action="preview_add"),
                                         content_type="application/json")
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["data"], {"count": 2, "reactivate_count": 0, "students": [
            {"number": "0001", "name": "合成甲", "sex": "female"},
            {"number": "0002", "name": "合成乙", "sex": "male"},
        ]})
        self.assertEqual(self.snapshot(), before)
        committed = self.owner_client.post(self.roster_url(), self.roster_payload(text), content_type="application/json")
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(list(Student.objects.filter(inclass=self.c).order_by("number").values_list("number", "sex")),
                         [("0001", "female"), ("0002", "male")])
        self.assertEqual(AuditEvent.objects.get().affected_count, 2)
        self.assertEqual(Submission.objects.count(), 1)

    def test_anonymous_foreign_owner_and_committee_cannot_upload_or_confirm(self):
        foreign = Client()
        foreign.force_login(self.other)
        committee, login = self.sign_in()
        self.assertEqual(login.status_code, 200)
        for role, client in (("anonymous", Client()), ("foreign owner", foreign), ("committee", committee)):
            with self.subTest(role=role):
                before = self.snapshot()
                with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("authorize first")):
                    upload = client.post(self.import_url(), self.upload_fields())
                self.assert_error(upload, 403, "permission_denied")
                committed = client.post(self.roster_url(), self.roster_payload(), content_type="application/json")
                self.assert_error(committed, 403, "permission_denied")
                self.assertEqual(self.snapshot(), before)
        # The requesting owner also cannot select another owner's class by URL.
        with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("authorize first")):
            response = self.owner_client.post(self.import_url(self.private), self.upload_fields())
        self.assert_error(response, 403, "permission_denied")

    def test_url_class_scope_wins_over_forged_form_fields_and_cross_class_duplicate_is_allowed(self):
        Student.objects.create(inclass=self.private, number="001", name="他班合成学生")
        fields = self.upload_fields(class_id=str(self.private.pk), inclass=str(self.private.pk))
        preview = self.owner_client.post(self.import_url(), fields)
        self.assertEqual(preview.status_code, 200, preview.content)
        payload = self.roster_payload(preview.json()["data"]["students_text"], class_id=self.private.pk, inclass=self.private.pk)
        response = self.owner_client.post(self.roster_url(), payload, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Student.objects.get(inclass=self.private).name, "他班合成学生")
        self.assertEqual(Student.objects.get(inclass=self.c).name, "合成姓名")
        self.assertEqual(AuditEvent.objects.get().inclass_id, self.c.pk)

    def test_past_future_terms_and_august_are_rejected_before_parser_and_on_confirmation(self):
        for day, term_key, status, code in (
            (date(2026, 10, 15), "2026-spring", 409, "term_read_only"),
            (date(2026, 10, 15), "2027-spring", 409, "term_read_only"),
            (date(2026, 8, 15), "2026-spring", 403, "august_read_only"),
        ):
            with self.subTest(day=day, term=term_key), business_day(day):
                before = self.snapshot()
                with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("check term first")):
                    response = self.owner_client.post(self.import_url(), self.upload_fields(term_key=term_key))
                self.assert_error(response, status, code)
                commit = self.owner_client.post(self.roster_url(), self.roster_payload(term_key=term_key), content_type="application/json")
                self.assert_error(commit, status, code)
                self.assertEqual(self.snapshot(), before)

    def test_archived_and_unverified_legacy_classes_cannot_import(self):
        for field, status, code in (("archived", 403, "class_archived"), ("legacy_pending", 409, "legacy_baseline_pending")):
            with self.subTest(field=field):
                Class.objects.filter(pk=self.c.pk).update(**{field: True})
                before = self.snapshot()
                with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("check readiness first")):
                    response = self.owner_client.post(self.import_url(), self.upload_fields())
                self.assert_error(response, status, code)
                self.assertEqual(self.snapshot(), before)
                Class.objects.filter(pk=self.c.pk).update(**{field: False})

    def test_invalid_and_stale_multipart_revision_are_rejected_before_parser(self):
        for revision, status, code in (("", 400, "invalid_revision"), ("1.5", 400, "invalid_revision"),
                                       ("false", 400, "invalid_revision"), ("0", 409, "revision_conflict")):
            with self.subTest(revision=revision):
                before = self.snapshot()
                with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("check revision first")):
                    response = self.owner_client.post(self.import_url(), self.upload_fields(revision=revision))
                self.assert_error(response, status, code)
                self.assertEqual(self.snapshot(), before)

    def test_roster_change_after_preview_invalidates_confirmation_and_reupload(self):
        fields = self.upload_fields()
        preview = self.owner_client.post(self.import_url(), fields)
        self.assertEqual(preview.status_code, 200)
        stale_payload = self.roster_payload(preview.json()["data"]["students_text"])
        changed = self.owner_client.post(self.roster_url(), self.roster_payload("002|另一名合成学生|女"), content_type="application/json")
        self.assertEqual(changed.status_code, 200)
        before = self.snapshot()
        rejected = self.owner_client.post(self.roster_url(), stale_payload, content_type="application/json")
        self.assert_error(rejected, 409, "revision_conflict")
        self.assertEqual(rejected.json()["error"]["details"]["current_revision"], 2)
        with patch("manage.services.student_import.parse_student_workbook", side_effect=AssertionError("check revision first")):
            rejected_upload = self.owner_client.post(self.import_url(), self.upload_fields(revision="1"))
        self.assert_error(rejected_upload, 409, "revision_conflict")
        self.assertEqual(self.snapshot(), before)
        fresh = self.owner_client.post(self.import_url(), self.upload_fields())
        self.assertEqual(fresh.status_code, 200, fresh.content)
        final = self.owner_client.post(self.roster_url(), self.roster_payload(fresh.json()["data"]["students_text"]),
                                       content_type="application/json")
        self.assertEqual(final.status_code, 200, final.content)
        self.assertEqual(Student.objects.filter(inclass=self.c).count(), 2)

    def test_upload_and_confirmation_both_require_csrf_and_accept_valid_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        before = self.snapshot()
        self.assertEqual(client.post(self.import_url(), self.upload_fields()).status_code, 403)
        self.assertEqual(client.post(self.roster_url(), self.roster_payload(), content_type="application/json").status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(client.get(self.roster_url()).status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        accepted = client.post(self.import_url(), self.upload_fields(), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(self.snapshot(), before)
        committed = client.post(self.roster_url(), self.roster_payload(accepted.json()["data"]["students_text"]),
                                content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(Student.objects.count(), 1)

    def test_get_upload_is_405_and_missing_file_or_bad_workbook_is_json_error(self):
        before = self.snapshot()
        response = self.owner_client.get(self.import_url())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")
        fields = self.upload_fields()
        del fields["file"]
        self.assert_error(self.owner_client.post(self.import_url(), fields), 400, "invalid_workbook")
        invalid = SimpleUploadedFile("synthetic.xlsx", b"invalid archive")
        self.assert_error(self.owner_client.post(self.import_url(), self.upload_fields(invalid)), 400, "invalid_workbook")
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_or_invalid_middle_student_rejects_entire_upload_and_tsv_batch(self):
        cases = (
            (record(2) + record(3), "001\t合成姓名\t男\n001\t另一名\t女", 409, "duplicate_student_number"),
            (record(2) + record(3, number="002", name="长" * 21), "001\t合成姓名\t男\n002\t" + "长" * 21 + "\t女", 400, "invalid_student"),
        )
        for rows, pasted, status, code in cases:
            with self.subTest(code=code):
                before = self.snapshot()
                uploaded = self.owner_client.post(self.import_url(), self.upload_fields(workbook(rows)))
                self.assert_error(uploaded, status, code)
                self.assertEqual(uploaded.json()["error"]["details"]["line"], 2)
                for action in ("preview_add", "bulk_add"):
                    rejected = self.owner_client.post(self.roster_url(), self.roster_payload(pasted, action=action),
                                                       content_type="application/json")
                    self.assert_error(rejected, status, code)
                self.assertEqual(self.snapshot(), before)

    def test_existing_student_blocks_new_upload_without_consuming_submission(self):
        created = self.owner_client.post(self.roster_url(), self.roster_payload(), content_type="application/json")
        self.assertEqual(created.status_code, 200)
        before = self.snapshot()
        rejected = self.owner_client.post(self.import_url(), self.upload_fields())
        self.assert_error(rejected, 409, "duplicate_student_number")
        self.assertEqual(self.snapshot(), before)

    def test_removed_student_is_reactivated_without_creating_duplicate_identity(self):
        created = self.owner_client.post(self.roster_url(), self.roster_payload(), content_type="application/json")
        self.assertEqual(created.status_code, 200, created.content)
        student = Student.objects.get(inclass=self.c, number="001")
        original_id = student.pk
        self.c.refresh_from_db()
        removed = self.owner_client.post(self.roster_url(), {
            "action": "remove", "student": {"id": student.pk}, "term_key": self.term_key,
            "revision": self.c.revision, "submission_id": str(uuid4()),
        }, content_type="application/json")
        self.assertEqual(removed.status_code, 200, removed.content)
        preview = self.owner_client.post(
            self.roster_url(), self.roster_payload("001|更新姓名|女", action="preview_add"),
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(preview.json()["data"]["reactivate_count"], 1)
        committed = self.owner_client.post(
            self.roster_url(), self.roster_payload("001|更新姓名|女"), content_type="application/json",
        )
        self.assertEqual(committed.status_code, 200, committed.content)
        self.assertEqual(committed.json()["data"]["reactivate_count"], 1)
        self.assertEqual(Student.objects.filter(inclass=self.c, number="001").count(), 1)
        student.refresh_from_db()
        self.assertEqual((student.pk, student.name, student.sex, student.active),
                         (original_id, "更新姓名", "female", True))
        version = RosterVersion.objects.get(student=student, effective_term_start=date(2026, 9, 1))
        self.assertEqual((version.name, version.sex, version.active), ("更新姓名", "female", True))
