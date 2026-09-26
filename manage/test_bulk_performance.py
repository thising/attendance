"""Batch-roster atomicity and query-count acceptance on synthetic data only."""
from datetime import date
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from manage.models import (
    Activity, AuditEvent, Class, ClassDailyUsage, CommitteeAccount, Report, RosterVersion,
    Student, Submission, SummaryCount,
)
from manage.services import committee, records, roster, scoring
from manage.services.calendar import Term
from manage.services.errors import BusinessError
from manage.test_domain import business_day


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BulkRosterAndPerformanceTests(TestCase):
    today = date(2026, 10, 15)
    term_key = "2026-autumn"
    password = "synthetic-bulk-class-password"

    def setUp(self):
        clock = business_day(self.today)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user("bulk-owner")
        self.classroom = Class.objects.create(classname="批量验收合成班", owner=self.owner,
            managecode=make_password(self.password), started_on=date(2026, 9, 1))

    def request(self, user=None):
        request = RequestFactory().post("/synthetic-bulk/", content_type="application/json")
        request.user = user if user is not None else self.owner
        SessionMiddleware(lambda value: None).process_request(request)
        return request

    def payload(self, text, action="bulk_add", term_key=None):
        self.classroom.refresh_from_db()
        return {"action": action, "students_text": text, "revision": self.classroom.revision,
                "submission_id": str(uuid4()), "term_key": term_key or self.term_key}

    def submit(self, payload, request=None):
        return roster.change_roster(request or self.request(), self.classroom.code, payload)

    def snapshot(self):
        self.classroom.refresh_from_db()
        return {
            "revision": self.classroom.revision,
            **{model.__name__: model.objects.count() for model in (
                Student, RosterVersion, AuditEvent, Submission, Activity, Report, SummaryCount,
            )},
        }

    def test_preview_normalizes_rows_without_writing_business_or_submission_state(self):
        payload = self.payload(" 001 | 合成甲 | 男 \n\n002|合成乙|female\n", action="preview_add")
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            result = self.submit(payload)
        self.assertEqual(result, {"count": 2, "reactivate_count": 0, "students": [
            {"number": "001", "name": "合成甲", "sex": "male"},
            {"number": "002", "name": "合成乙", "sex": "female"},
        ]})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for query in queries))
        self.assertEqual(self.submit(payload), result)
        self.assertEqual(self.snapshot(), before)

    def test_bulk_add_commits_one_roster_revision_and_one_event(self):
        before = self.snapshot()
        result = self.submit(self.payload("001|合成甲|男\n002|合成乙|女"))
        self.assertEqual(result["count"], 2)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["revision"], before["revision"] + 1)
        self.classroom.refresh_from_db()
        self.assertEqual(self.classroom.revision, before["revision"] + 1)
        self.assertEqual(Student.objects.filter(inclass=self.classroom).count(), 2)
        versions = RosterVersion.objects.filter(inclass=self.classroom)
        self.assertEqual(versions.count(), 2)
        self.assertEqual(set(versions.values_list("effective_term_start", flat=True)), {date(2026, 9, 1)})
        event = AuditEvent.objects.get(inclass=self.classroom, kind="roster_add")
        self.assertEqual((event.affected_count, event.revision), (2, before["revision"] + 1))
        self.assertEqual(Submission.objects.count(), before["Submission"] + 1)

    def test_bulk_retry_does_not_repeat_students_versions_event_or_revision(self):
        payload = self.payload("001|合成甲|男\n002|合成乙|女")
        first = self.submit(payload)
        after_first = self.snapshot()
        retry = self.submit(payload)
        self.assertTrue(retry["replayed"])
        self.assertEqual((retry["count"], retry["revision"]), (first["count"], first["revision"]))
        self.assertEqual(self.snapshot(), after_first)

    def test_invalid_middle_row_rejects_entire_batch(self):
        before = self.snapshot()
        for action in ("preview_add", "bulk_add"):
            with self.subTest(action=action), self.assertRaises(BusinessError) as caught:
                self.submit(self.payload("001|合成甲|男\n002|合成乙|未知\n003|合成丙|女", action=action))
            self.assertEqual(caught.exception.code, "invalid_student")
            self.assertEqual(caught.exception.details["line"], 2)
            self.assertEqual(self.snapshot(), before)

    def test_duplicate_inside_batch_rejects_every_row(self):
        before = self.snapshot()
        with self.assertRaises(BusinessError) as caught:
            self.submit(self.payload("001|合成甲|男\n002|合成乙|女\n001|合成丙|男"))
        self.assertEqual(caught.exception.code, "duplicate_student_number")
        self.assertEqual(caught.exception.details["line"], 3)
        self.assertEqual(self.snapshot(), before)

    def test_existing_number_rejects_whole_batch_and_preserves_existing_student(self):
        self.submit(self.payload("existing|既有合成学生|女"))
        before = self.snapshot()
        for action in ("preview_add", "bulk_add"):
            with self.subTest(action=action), self.assertRaises(BusinessError) as caught:
                self.submit(self.payload("new-001|合成甲|男\nexisting|不应覆盖|男\nnew-002|合成乙|女", action=action))
            self.assertEqual(caught.exception.code, "duplicate_student_number")
            self.assertEqual(self.snapshot(), before)
        student = Student.objects.get(inclass=self.classroom, number="existing")
        self.assertEqual((student.name, student.sex), ("既有合成学生", "female"))

    def test_august_closed_term_and_committee_reject_preview_and_commit(self):
        account = CommitteeAccount.objects.create(inclass=self.classroom,
            username="bulk-committee", password=make_password(self.password))
        committee_request = self.request(AnonymousUser())
        grant = committee.sign_in(committee_request, {"username": account.username, "password": self.password})
        self.assertEqual(grant["role"], "committee")
        before = self.snapshot()
        cases = (
            ("august", "2026-08-15", "2026-spring", self.request(), "august_read_only"),
            ("closed_term", "2026-10-15", "2026-spring", self.request(), "term_read_only"),
            ("committee", "2026-10-15", "2026-autumn", committee_request, "permission_denied"),
        )
        for label, day, term_key, request, code in cases:
            for action in ("preview_add", "bulk_add"):
                with self.subTest(case=label, action=action), business_day(day), self.assertRaises(BusinessError) as caught:
                    self.submit(self.payload("001|合成甲|男", action=action, term_key=term_key), request=request)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.snapshot(), before)

    def test_fifty_exceptions_use_bounded_queries_independent_of_old_summary_months(self):
        text = "\n".join(f"{number:03d}|合成学生{number:02d}|男" for number in range(1, 51))
        self.submit(self.payload(text))
        students = list(Student.objects.filter(inclass=self.classroom).order_by("number"))
        self.assertEqual(len(students), 50)
        # Warm only the fixed per-day counter, outside the measured operation.
        # Both measurements then compare the same 50-row service input shape.
        ClassDailyUsage.objects.create(inclass=self.classroom, day=self.today, created_count=0)
        self.classroom.refresh_from_db()

        def measured_record(name):
            payload = {"revision": 0, "roster_revision": self.classroom.revision,
                "term_key": self.term_key, "submission_id": str(uuid4()),
                "record": {"kind": "class", "name": name, "date": self.today.isoformat(),
                           "students": [{"id": student.pk, "value": "absent"} for student in students]}}
            request = self.request()
            with CaptureQueriesContext(connection) as queries:
                result = records.save_record(request, self.classroom.code, payload)
            self.assertEqual(Report.objects.filter(activity_id=result["id"]).count(), 50)
            return len(queries)

        without_history = measured_record("无历史缓存的完整点名")
        self.assertEqual(Report.objects.count(), 50)
        SummaryCount.objects.bulk_create([
            SummaryCount(student=student, year=2025, month=month, absent_count=7)
            for student in students for month in range(1, 13)
        ])
        with_history = measured_record("附加历史缓存后的完整点名")
        self.assertLess(without_history, 60)
        self.assertLess(with_history, 60)
        self.assertLessEqual(abs(with_history - without_history), 3)
        self.assertEqual(Report.objects.count(), 100)
        self.assertEqual(AuditEvent.objects.filter(kind="record_created").count(), 2)
        self.assertEqual(SummaryCount.objects.filter(year=2025, absent_count=7).count(), 600)
        self.assertEqual(SummaryCount.objects.filter(year=2026, month=10, absent_count=2).count(), 50)
        report = scoring.class_report(self.classroom, Term(2026, "autumn"), today=self.today)
        self.assertEqual(report["summary"], {"student_count": 50, "month_count": 2, "average": "58.00"})
        print("BULK_PERFORMANCE " + json.dumps({
            "students": 50, "exceptions_per_submission": 50, "unrelated_history_rows": 600,
            "queries_without_history": without_history, "queries_with_history": with_history,
            "includes_transaction_queries": True,
        }, sort_keys=True))
