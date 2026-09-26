"""Destructive class-management actions preserve historical term boundaries."""
from datetime import date
from uuid import uuid4

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase, override_settings

from manage.models import (
    Activity, AuditEvent, Class, ClassTerm, CommitteeAccount, Report,
    RosterVersion, Student, Submission, SummaryCount,
)
from manage.test_domain import business_day
from manage.test_named_committee import AccountFixtures


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ClassManagementTests(AccountFixtures, TestCase):
    term_key = "2026-autumn"

    def url(self, classroom=None):
        return f"/classes/{(classroom or self.c).code}/management/"

    def payload(self, action, classroom=None, **values):
        classroom = classroom or self.c
        classroom.refresh_from_db()
        return {
            "action": action, "confirmation": classroom.classname,
            "term_key": self.term_key, "revision": classroom.revision,
            "submission_id": str(uuid4()), **values,
        }

    def add_student(self, classroom=None, number="001"):
        classroom = classroom or self.c
        classroom.refresh_from_db()
        response = self.owner_client.post(f"/classes/{classroom.code}/roster/", {
            "action": "add", "student": {"number": number, "name": "合成学生", "sex": "male"},
            "term_key": self.term_key, "revision": classroom.revision,
            "submission_id": str(uuid4()),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        return Student.objects.get(inclass=classroom, number=number)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_clear_data_deletes_only_current_term_records_and_derived_counts(self):
        student = self.add_student()
        current = Activity.objects.create(inclass=self.c, occurred_on=date(2026, 10, 1),
                                          activity_type="class", name="当前点名")
        Report.objects.create(activity=current, student=student, status="late")
        old = Activity.objects.create(inclass=self.c, occurred_on=date(2026, 6, 1),
                                      activity_type="class", name="历史点名")
        Report.objects.create(activity=old, student=student, status="absent")
        SummaryCount.objects.create(student=student, year=2026, month=10, late_count=1)
        SummaryCount.objects.create(student=student, year=2026, month=6, absent_count=1)
        response = self.owner_client.post(self.url(), self.payload("clear_data"), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Activity.objects.filter(pk=current.pk).exists())
        self.assertTrue(Activity.objects.filter(pk=old.pk).exists())
        self.assertFalse(SummaryCount.objects.filter(student=student, year=2026, month=10).exists())
        self.assertTrue(SummaryCount.objects.filter(student=student, year=2026, month=6).exists())
        self.assertTrue(Student.objects.filter(pk=student.pk, active=True).exists())
        log = AuditEvent.objects.get(kind="class_data_cleared")
        self.assertEqual((log.affected_count, log.actor_id), (1, self.owner.pk))

    def test_clear_students_requires_empty_current_data_and_preserves_history(self):
        student = self.add_student()
        record = Activity.objects.create(inclass=self.c, occurred_on=date(2026, 10, 1), name="当前记录")
        Report.objects.create(activity=record, student=student)
        blocked = self.owner_client.post(self.url(), self.payload("clear_students"), content_type="application/json")
        self.assert_error(blocked, 409, "class_has_records")
        self.owner_client.post(self.url(), self.payload("clear_data"), content_type="application/json")
        RosterVersion.objects.create(student=student, inclass=self.c, effective_term_start=date(2026, 2, 1),
                                     number=student.number, name="历史姓名", sex=student.sex, active=True)
        cleared = self.owner_client.post(self.url(), self.payload("clear_students"), content_type="application/json")
        self.assertEqual(cleared.status_code, 200, cleared.content)
        student.refresh_from_db()
        self.assertFalse(student.active)
        self.assertTrue(RosterVersion.objects.get(student=student, effective_term_start=date(2026, 2, 1)).active)
        self.assertFalse(RosterVersion.objects.get(student=student, effective_term_start=date(2026, 9, 1)).active)
        self.assertEqual(AuditEvent.objects.get(kind="class_students_cleared").affected_count, 1)

    def test_clear_students_removes_history_free_student_identity(self):
        student = self.add_student(self.c2)
        response = self.owner_client.post(self.url(self.c2), self.payload("clear_students", self.c2),
                                          content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Student.objects.filter(pk=student.pk).exists())
        self.assertFalse(RosterVersion.objects.filter(student_id=student.pk).exists())

    def test_delete_only_empty_history_free_class_and_revokes_supporting_access(self):
        empty = Class.objects.create(owner=self.owner, classname="待删空班", started_on=date(2026, 9, 1))
        inactive = Student.objects.create(inclass=empty, number="retired", name="已移出学生", active=False)
        RosterVersion.objects.create(student=inactive, inclass=empty, effective_term_start=date(2026, 9, 1),
                                     number=inactive.number, name=inactive.name, sex=inactive.sex, active=False)
        CommitteeAccount.objects.create(inclass=empty, username="delete.synthetic",
                                        password=make_password("synthetic-password"))
        AuditEvent.objects.create(inclass=empty, actor_role="owner", actor_id=self.owner.pk,
                                  kind="class_created", summary="创建班级")
        delete_payload = self.payload("delete_class", empty)
        response = self.owner_client.post(self.url(empty), delete_payload,
                                          content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Class.objects.filter(pk=empty.pk).exists())
        self.assertFalse(CommitteeAccount.objects.filter(inclass_id=empty.pk).exists())
        self.assertFalse(AuditEvent.objects.filter(inclass_id=empty.pk).exists())
        self.assertEqual(self.owner_client.get(f"/classes/{empty.pk}/?format=json").status_code, 404)
        retry = self.owner_client.post(self.url(empty), delete_payload, content_type="application/json")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertTrue(retry.json()["data"]["replayed"])

        ClassTerm.objects.create(inclass=self.c2, owner=self.owner, term_key="2026-spring",
                                 archived_data={"rows": [], "summary": {}})
        blocked = self.owner_client.post(self.url(self.c2), self.payload("delete_class", self.c2),
                                         content_type="application/json")
        self.assert_error(blocked, 409, "class_has_history")
        self.assertTrue(Class.objects.filter(pk=self.c2.pk).exists())

    def test_confirmation_revision_term_and_permissions_are_enforced(self):
        wrong = self.payload("clear_data", confirmation="不是班级名")
        self.assert_error(self.owner_client.post(self.url(), wrong, content_type="application/json"),
                          409, "confirmation_mismatch")
        stale = self.payload("clear_students", revision=0)
        self.assert_error(self.owner_client.post(self.url(), stale, content_type="application/json"),
                          409, "revision_conflict")
        committee, login = self.sign_in()
        self.assertEqual(login.status_code, 200)
        forbidden = committee.post(self.url(), self.payload("clear_students"), content_type="application/json")
        self.assert_error(forbidden, 403, "permission_denied")
        foreign = Client(); foreign.force_login(self.other)
        self.assert_error(foreign.post(self.url(), self.payload("clear_students"), content_type="application/json"),
                          403, "permission_denied")
        self.assertEqual(Submission.objects.count(), 0)

        csrf_client = Client(enforce_csrf_checks=True); csrf_client.force_login(self.owner)
        denied = csrf_client.post(self.url(), self.payload("clear_students"), content_type="application/json")
        self.assertEqual(denied.status_code, 403)
