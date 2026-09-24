"""Acceptance examples for calendar scoring and historical identity isolation.

All business facts are synthetic and created through the same services as the
UI. Historical expectations are specified independently of cached summaries.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from manage.models import (
    Activity, Class, Report, ScoringPolicyVersion, Student, SummaryCount,
)
from manage.services import calendar, records, roster, rules, scoring
from manage.services.errors import BusinessError


@contextmanager
def business_day(value):
    current = date.fromisoformat(value) if isinstance(value, str) else value
    with patch("manage.services.calendar.business_today", return_value=current), \
            patch("django.utils.timezone.now", return_value=datetime(current.year, current.month, current.day, 12)):
        yield current


class CalendarContractTests(SimpleTestCase):
    def test_school_year_boundaries_and_august_gap(self):
        expected = {
            "2027-01-31": "2026-autumn",
            "2027-02-01": "2027-spring",
            "2027-07-31": "2027-spring",
            "2027-08-01": None,
            "2027-08-31": None,
            "2027-09-01": "2027-autumn",
            "2027-12-31": "2027-autumn",
        }
        for value, key in expected.items():
            with self.subTest(day=value):
                term = calendar.term_for_date(date.fromisoformat(value))
                self.assertEqual(term.key if term else None, key)

    def test_half_open_ranges_and_leap_day(self):
        spring = calendar.Term(2028, "spring")
        autumn = calendar.Term(2027, "autumn")
        self.assertEqual((spring.start, spring.end), (date(2028, 2, 1), date(2028, 8, 1)))
        self.assertEqual((autumn.start, autumn.end), (date(2027, 9, 1), date(2028, 2, 1)))
        self.assertTrue(spring.contains(date(2028, 2, 29)))
        self.assertTrue(autumn.contains(date(2028, 1, 31)))
        self.assertFalse(autumn.contains(date(2028, 2, 1)))
        self.assertFalse(spring.contains(date(2028, 8, 1)))

    def test_months_follow_calendar_including_january(self):
        autumn = calendar.Term(2026, "autumn")
        self.assertEqual(autumn.months(date(2026, 8, 31)), [])
        self.assertEqual(autumn.months(date(2026, 9, 30)), [(2026, 9)])
        self.assertEqual(autumn.months(date(2026, 10, 1)), [(2026, 9), (2026, 10)])
        self.assertEqual(autumn.months(date(2027, 1, 1)), [
            (2026, 9), (2026, 10), (2026, 11), (2026, 12), (2027, 1),
        ])
        self.assertEqual(calendar.Term(2026, "spring").months(date(2027, 1, 1)), [
            (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7),
        ])

    @override_settings(USE_TZ=True, TIME_ZONE="Asia/Shanghai")
    def test_business_date_changes_at_shanghai_midnight(self):
        for utc_time, expected in (
            (datetime(2026, 8, 31, 15, 59, 59, tzinfo=datetime_timezone.utc), date(2026, 8, 31)),
            (datetime(2026, 8, 31, 16, 0, 0, tzinfo=datetime_timezone.utc), date(2026, 9, 1)),
        ):
            with self.subTest(utc_time=utc_time), patch("manage.services.calendar.timezone.now", return_value=utc_time):
                self.assertEqual(calendar.business_today(), expected)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DomainContractTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("domain-owner", password="fixture-password")
        self.factory = RequestFactory()

    def request(self, owner=None):
        request = self.factory.post("/domain-test/", content_type="application/json")
        request.user = owner or self.owner
        SessionMiddleware(lambda value: None).process_request(request)
        return request

    def classroom(self, started_on, owner=None):
        return Class.objects.create(
            classname="合成班级 " + str(Class.objects.count() + 1),
            owner=owner or self.owner,
            managecode=make_password("fixture-class-password"),
            started_on=date.fromisoformat(started_on),
        )

    def current_key(self):
        return calendar.term_for_date(calendar.business_today()).key

    def roster_change(self, classroom, action, student, term_key=None):
        classroom.refresh_from_db()
        return roster.change_roster(self.request(classroom.owner), classroom.code, {
            "action": action, "student": student,
            "term_key": term_key or self.current_key(),
            "submission_id": str(uuid4()), "revision": classroom.revision,
        })

    def add_student(self, classroom, number="001", name="合成学生"):
        self.roster_change(classroom, "add", {"number": number, "name": name, "sex": "male"})
        return Student.objects.get(inclass=classroom, number=number)

    def record(self, classroom, occurred_on, values, kind="class"):
        classroom.refresh_from_db()
        result = records.save_record(self.request(classroom.owner), classroom.code, {
            "submission_id": str(uuid4()), "term_key": self.current_key(), "revision": 0,
            "roster_revision": classroom.revision,
            "record": {
                "kind": kind, "name": "合成记录", "date": occurred_on,
                "students": [{"id": student.pk, "value": values.get(student.pk, "normal")}
                             for student in Student.objects.filter(inclass=classroom, active=True)],
            },
        })
        return Activity.objects.get(pk=result["id"])

    def change_weights(self, changes, owner=None, action="save"):
        owner = owner or self.owner
        term = calendar.term_for_date(calendar.business_today())
        current = scoring.policy_for(owner.pk, term)
        return rules.change_rules(self.request(owner), {
            "weights": {**current["weights"], **changes}, "revision": current["revision"],
            "term_key": term.key, "submission_id": str(uuid4()), "action": action,
        })

    def report(self, classroom, key=None):
        term = calendar.parse_term(key or self.current_key())
        return scoring.class_report(classroom, term, today=calendar.business_today())

    def only_row(self, classroom, key=None):
        result = self.report(classroom, key)
        self.assertEqual(len(result["rows"]), 1)
        return result["rows"][0]

    def test_no_records_receive_base_for_only_entered_months(self):
        with business_day("2026-09-30"):
            classroom = self.classroom("2026-09-01")
            self.add_student(classroom)
            report = self.report(classroom)
            self.assertEqual(report["summary"], {"average": "60.00", "student_count": 1, "month_count": 1})
            self.assertEqual(report["rows"][0]["score"], "60.00")
        with business_day("2026-10-01"):
            report = self.report(classroom)
            self.assertEqual(report["summary"]["month_count"], 2)
            self.assertEqual([month["key"] for month in report["rows"][0]["months"]], ["2026-09", "2026-10"])
            self.assertEqual([month["score"] for month in report["rows"][0]["months"]], ["60.00", "60.00"])
            self.assertEqual(Report.objects.count(), 0)

    def test_month_rollover_changes_average_without_new_events(self):
        with business_day("2026-09-30"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            for _ in range(5):
                self.record(classroom, "2026-09-15", {student.pk: "absent"})
            self.assertEqual(self.only_row(classroom)["score"], "50.00")
        with business_day("2026-10-01"):
            self.assertEqual(self.only_row(classroom)["score"], "55.00")
            self.assertEqual(Activity.objects.count(), 5)

    def test_month_is_clamped_before_semester_average(self):
        with business_day("2026-09-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            for _ in range(6):
                self.record(classroom, "2026-09-15", {student.pk: "dhigh"}, kind="discipline")
        with business_day("2026-10-01"):
            row = self.only_row(classroom)
            self.assertEqual([month["score"] for month in row["months"]], ["0.00", "60.00"])
            self.assertEqual(row["score"], "30.00")
            self.assertEqual(row["counts"]["dhigh"], 6)

    def test_fractional_average_uses_decimal_half_up(self):
        with business_day("2026-12-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.change_weights({"late": "0.50"})
            self.record(classroom, "2026-09-30", {student.pk: "late"})
            self.record(classroom, "2026-10-30", {student.pk: "late"})
            self.record(classroom, "2026-11-30", {student.pk: "late"})
            row = self.only_row(classroom)
            self.assertEqual([month["score"] for month in row["months"]], ["59.50", "59.50", "59.50", "60.00"])
            self.assertEqual(row["score"], "59.63")  # 59.625, not banker's rounding.

    def test_all_nine_categories_and_zero_weight_are_counted(self):
        with business_day("2026-09-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.change_weights({"absent": "2.50", "late": "1.50", "leave": "0",
                                 "low": "0.50", "mid": "3.50", "high": "5.50",
                                 "dlow": "4.50", "dmid": "8.50", "dhigh": "12.50"})
            for kind, values in (("class", ("absent", "late", "leave")),
                                 ("activity", ("low", "mid", "high")),
                                 ("discipline", ("dlow", "dmid", "dhigh"))):
                for value in values:
                    self.record(classroom, "2026-09-15", {student.pk: value}, kind=kind)
            row = self.only_row(classroom)
            self.assertEqual(row["score"], "40.00")
            self.assertEqual(row["counts"], {key: 1 for key in (
                "absent", "late", "leave", "low", "mid", "high", "dlow", "dmid", "dhigh",
            )})

    def test_positive_score_has_no_upper_cap(self):
        with business_day("2026-09-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.change_weights({"high": "75.50"})
            self.record(classroom, "2026-09-15", {student.pk: "high"}, kind="activity")
            self.assertEqual(self.only_row(classroom)["score"], "135.50")

    def test_late_supplement_receives_earlier_months_of_current_term(self):
        with business_day("2026-11-20"):
            classroom = self.classroom("2026-09-01")
            self.add_student(classroom)
            row = self.only_row(classroom)
            self.assertEqual([month["key"] for month in row["months"]], ["2026-09", "2026-10", "2026-11"])
            self.assertEqual(row["score"], "60.00")
            self.assertEqual(self.report(classroom, "2026-spring")["rows"], [])

    def test_missing_monthly_cache_does_not_hide_backdated_report(self):
        with business_day("2026-10-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.record(classroom, "2026-09-20", {student.pk: "absent"})
            SummaryCount.objects.filter(student=student, year=2026, month=9).delete()
            SummaryCount.objects.update_or_create(student=student, year=2026, month=10)
            row = self.only_row(classroom)
            self.assertEqual(row["counts"]["absent"], 1)
            self.assertEqual(row["score"], "59.00")

    def test_weights_apply_to_all_owners_classes_only(self):
        with business_day("2026-09-15"):
            other_owner = get_user_model().objects.create_user("another-owner")
            classrooms = [self.classroom("2026-09-01"), self.classroom("2026-09-01"),
                          self.classroom("2026-09-01", owner=other_owner)]
            for classroom in classrooms:
                student = self.add_student(classroom)
                self.record(classroom, "2026-09-10", {student.pk: "late"})
            self.change_weights({"late": "2.00"})
            self.assertEqual([self.only_row(classroom)["score"] for classroom in classrooms], ["58.00", "58.00", "59.00"])

    def test_new_weights_recalculate_earlier_months_in_current_term(self):
        with business_day("2026-09-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.record(classroom, "2026-09-10", {student.pk: "late"})
        with business_day("2026-11-01"):
            self.change_weights({"late": "3.00"})
            row = self.only_row(classroom)
            self.assertEqual([month["score"] for month in row["months"]], ["57.00", "60.00", "60.00"])
            self.assertEqual(row["score"], "59.00")

    def test_weight_change_preserves_historical_scores_and_future_default(self):
        with business_day("2026-03-15"):
            classroom = self.classroom("2026-02-01")
            student = self.add_student(classroom)
            self.change_weights({"late": "2.00"})
            self.record(classroom, "2026-03-15", {student.pk: "late"})
        with business_day("2026-09-15"):
            self.record(classroom, "2026-09-10", {student.pk: "late"})
            self.change_weights({"late": "5.00"})
            self.assertEqual(self.only_row(classroom, "2026-spring")["score"], "59.67")
            self.assertEqual(self.only_row(classroom)["score"], "55.00")
            old_policy = scoring.policy_for(self.owner.pk, calendar.Term(2026, "spring"))
            future_policy = scoring.policy_for(self.owner.pk, calendar.Term(2027, "spring"))
            self.assertEqual(Decimal(old_policy["weights"]["late"]), Decimal("2"))
            self.assertEqual(Decimal(future_policy["weights"]["late"]), Decimal("5"))

    def test_first_read_of_unused_old_term_uses_then_effective_policy(self):
        with business_day("2025-03-01"):
            self.classroom("2025-02-01")
            self.change_weights({"late": "2.50"})
        # No report or policy lookup occurs in 2025 autumn or 2026 spring.
        with business_day("2026-09-15"):
            self.change_weights({"late": "8.00"})
            for term in (calendar.Term(2025, "autumn"), calendar.Term(2026, "spring")):
                with self.subTest(term=term.key):
                    self.assertEqual(Decimal(scoring.policy_for(self.owner.pk, term)["weights"]["late"]), Decimal("2.50"))

    def test_roster_changes_preserve_unread_historical_identity_and_score(self):
        with business_day("2026-03-15"):
            classroom = self.classroom("2026-02-01")
            renamed = self.add_student(classroom, "001", "历史姓名")
            removed = self.add_student(classroom, "002", "历史成员")
            self.record(classroom, "2026-03-10", {renamed.pk: "late"})
        # Deliberately do not read the spring report before changing the roster.
        with business_day("2026-10-15"):
            self.roster_change(classroom, "edit", {"id": renamed.pk, "number": "101", "name": "当前姓名", "sex": "male"})
            self.roster_change(classroom, "remove", {"id": removed.pk})
            added = self.add_student(classroom, "003", "当前新增")
            old = {row["id"]: row for row in self.report(classroom, "2026-spring")["rows"]}
            current = {row["id"]: row for row in self.report(classroom)["rows"]}
            self.assertEqual(set(old), {renamed.pk, removed.pk})
            self.assertEqual((old[renamed.pk]["number"], old[renamed.pk]["name"], old[renamed.pk]["score"]), ("001", "历史姓名", "59.83"))
            self.assertEqual(old[removed.pk]["score"], "60.00")
            self.assertEqual(set(current), {renamed.pk, added.pk})
            self.assertEqual((current[renamed.pk]["number"], current[renamed.pk]["name"]), ("101", "当前姓名"))
            self.assertEqual(len(current[added.pk]["months"]), 2)
            self.assertEqual(current[added.pk]["score"], "60.00")

    def test_long_unvisited_terms_inherit_old_roster_not_current_identity(self):
        with business_day("2025-03-01"):
            classroom = self.classroom("2025-02-01")
            student = self.add_student(classroom, "001", "早期姓名")
        with business_day("2026-09-15"):
            self.roster_change(classroom, "edit", {"id": student.pk, "number": "101", "name": "后期姓名", "sex": "male"})
            for key in ("2025-autumn", "2026-spring"):
                with self.subTest(term=key):
                    row = self.only_row(classroom, key)
                    self.assertEqual((row["number"], row["name"]), ("001", "早期姓名"))
                    self.assertEqual(row["score"], "60.00")

    def test_rules_preview_does_not_commit_policy_or_change_scores(self):
        with business_day("2026-09-15"):
            classroom = self.classroom("2026-09-01")
            student = self.add_student(classroom)
            self.record(classroom, "2026-09-10", {student.pk: "late"})
            before = scoring.policy_for(self.owner.pk, calendar.Term(2026, "autumn"))
            version_count = ScoringPolicyVersion.objects.count()
            self.change_weights({"late": "9.00"}, action="preview")
            after = scoring.policy_for(self.owner.pk, calendar.Term(2026, "autumn"))
            self.assertEqual((after["revision"], after["weights"]), (before["revision"], before["weights"]))
            self.assertEqual(ScoringPolicyVersion.objects.count(), version_count)
            self.assertEqual(self.only_row(classroom)["score"], "59.00")

    def test_invalid_weights_are_rejected_without_new_version(self):
        with business_day("2026-09-15"):
            self.classroom("2026-09-01")
            before = scoring.policy_for(self.owner.pk, calendar.Term(2026, "autumn"))
            version_count = ScoringPolicyVersion.objects.count()
            for value in ("-0.01", "0.001", "NaN", "Infinity"):
                with self.subTest(value=value), self.assertRaises(BusinessError):
                    self.change_weights({"late": value})
                self.assertEqual(ScoringPolicyVersion.objects.count(), version_count)
            after = scoring.policy_for(self.owner.pk, calendar.Term(2026, "autumn"))
            self.assertEqual(after["weights"], before["weights"])

    def test_policy_version_cannot_be_overwritten(self):
        with business_day("2026-09-15"):
            self.classroom("2026-09-01")
            self.change_weights({"late": "2.00"})
            policy = ScoringPolicyVersion.objects.filter(owner=self.owner).latest("pk")
            policy.late = Decimal("9.00")
            with self.assertRaises(ValidationError):
                policy.save()
            policy.refresh_from_db()
            self.assertEqual(policy.late, Decimal("2.00"))

    def test_student_with_historical_fact_cannot_be_physically_deleted(self):
        with business_day("2026-03-15"):
            classroom = self.classroom("2026-02-01")
            student = self.add_student(classroom)
            self.record(classroom, "2026-03-10", {student.pk: "late"})
        with business_day("2026-09-15"), self.assertRaises(ProtectedError):
            student.delete()
        self.assertEqual(Report.objects.count(), 1)

    def test_closed_term_roster_mutation_is_rejected(self):
        with business_day("2026-03-15"):
            classroom = self.classroom("2026-02-01")
            student = self.add_student(classroom)
        with business_day("2026-09-15"), self.assertRaises(BusinessError) as caught:
            self.roster_change(classroom, "edit", {"id": student.pk, "number": "001", "name": "不应保存", "sex": "male"}, term_key="2026-spring")
        self.assertEqual(caught.exception.code, "term_read_only")
        student.refresh_from_db()
        self.assertEqual(student.name, "合成学生")

    def test_august_rejects_business_roster_and_rule_writes(self):
        with business_day("2026-07-31"):
            classroom = self.classroom("2026-02-01")
            policy = scoring.policy_for(self.owner.pk, calendar.Term(2026, "spring"))
        with business_day("2026-08-01"):
            with self.assertRaises(BusinessError) as caught:
                self.roster_change(classroom, "add", {"number": "001", "name": "不应新增"}, term_key="2026-spring")
            self.assertEqual(caught.exception.code, "august_read_only")
            with self.assertRaises(BusinessError) as caught:
                rules.change_rules(self.request(), {"weights": policy["weights"], "revision": policy["revision"],
                    "term_key": "2026-spring", "submission_id": str(uuid4()), "action": "save"})
            self.assertEqual(caught.exception.code, "august_read_only")
            self.assertEqual(Student.objects.count(), 0)
