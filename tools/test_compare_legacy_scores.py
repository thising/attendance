"""Stdlib-only tests; creates and removes synthetic SQLite files in /tmp."""
import ast
from contextlib import redirect_stderr
from datetime import date, datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from tools.compare_legacy_scores import (
    CATEGORIES, compare_database, legacy_current_start, main, score, term_months,
)
from tools.inspect_legacy import open_readonly


class LegacyScoreComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="attendance-score-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic #?.sqlite3"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        count_columns = ", ".join(values[3] + " INTEGER NOT NULL" for values in CATEGORIES.values())
        self.db.executescript("""
            CREATE TABLE manage_class (id INTEGER PRIMARY KEY, classname TEXT, managecode TEXT);
            CREATE TABLE manage_student (id INTEGER PRIMARY KEY, inclass_id INTEGER, number TEXT, name TEXT);
            CREATE TABLE manage_activity (id INTEGER PRIMARY KEY, inclass_id INTEGER, time TEXT, activity_type TEXT, name TEXT, status TEXT);
            CREATE TABLE manage_report (id INTEGER PRIMARY KEY, activity_id INTEGER, student_id INTEGER, status TEXT, level TEXT, discipline TEXT);
            CREATE TABLE manage_summarycount (id INTEGER PRIMARY KEY, student_id INTEGER, year INTEGER, month INTEGER,
        """ + count_columns + ");" + """
            CREATE TABLE django_content_type (id INTEGER PRIMARY KEY, app_label TEXT, model TEXT);
            CREATE TABLE django_admin_log (id INTEGER PRIMARY KEY, content_type_id INTEGER, action_flag INTEGER, action_time TEXT, object_repr TEXT);
            INSERT INTO manage_class VALUES (1, 'PRIVATE_CLASS_SENTINEL', 'PRIVATE_PASSWORD_SENTINEL');
            INSERT INTO manage_student VALUES (938473873777, 1, 'PRIVATE_NUMBER_SENTINEL', 'PRIVATE_NAME_SENTINEL');
            INSERT INTO manage_student VALUES (2, 1, 'PRIVATE_NUMBER_2_SENTINEL', 'PRIVATE_NAME_2_SENTINEL');
            INSERT INTO manage_student VALUES (3, 1, 'PRIVATE_NUMBER_3_SENTINEL', 'PRIVATE_NAME_3_SENTINEL');
            INSERT INTO django_content_type VALUES (1, 'manage', 'activity');
            INSERT INTO django_admin_log VALUES (1, 1, 3, '2023-10-10 12:00:00', 'PRIVATE_DELETED_EVENT_SENTINEL');
        """)
        self.student = 938473873777

    def cache(self, student, year, month, **counts):
        fields = ", ".join(values[3] for values in CATEGORIES.values())
        values = [student, year, month] + [counts.get(key, 0) for key in CATEGORIES]
        self.db.execute("INSERT INTO manage_summarycount(student_id,year,month," + fields + ") VALUES ("
                        + ",".join("?" for _ in values) + ")", values)

    def report(self, student, day, status="present", level="none", discipline="none", kind="class"):
        activity = self.db.execute("INSERT INTO manage_activity(inclass_id,time,activity_type,name,status) VALUES(1,?,?,?,?)",
            (day + " 12:00:00", kind, "PRIVATE_ACTIVITY_SENTINEL", "preview")).lastrowid
        self.db.execute("INSERT INTO manage_report(activity_id,student_id,status,level,discipline) VALUES(?,?,?,?,?)",
                        (activity, student, status, level, discipline))

    def result(self):
        self.db.commit()
        return compare_database(self.path)

    def test_cache_fact_and_calendar_differences_are_separated_without_inventing_roster(self):
        self.cache(self.student, 2023, 9, absent=1)
        self.cache(2, 2023, 10, late=1)
        self.report(self.student, "2023-09-10", status="absent")
        self.report(self.student, "2023-09-11", status="absent")
        self.report(2, "2023-10-01", status="late")
        result = self.result()
        self.assertEqual(result["snapshot_table_counts"]["manage_student"], 3)
        self.assertEqual(result["current_snapshot_students_without_score_evidence"], 1)
        self.assertEqual(result["overall"]["observed_evidence_members_across_all_months"], 2)
        self.assertEqual(result["months"][0]["cache_groups_with_count_difference"], 1)
        term = result["terms"][0]
        self.assertEqual(term["evidence_member_count"], 2)
        self.assertEqual(term["candidate_unobserved_member_month_slots_through_last_evidence"], 2)
        self.assertEqual(term["candidate_unobserved_member_month_slots_full_term"], 8)
        cache_fix = term["legacy_stage_cache_vs_same_rows_rebuilt_from_facts"]
        self.assertEqual((cache_fix["before_total"], cache_fix["after_total"], cache_fix["total_delta"]), ("117.00", "115.00", "-2.00"))
        elapsed = term["legacy_stage_cache_vs_natural_through_last_evidence"]
        self.assertEqual((elapsed["after_total"], elapsed["total_delta"]), ("117.50", "0.50"))
        complete = term["legacy_stage_cache_vs_candidate_full_natural_term"]
        self.assertEqual((complete["after_total"], complete["total_delta"]), ("119.00", "2.00"))
        self.assertEqual(term["months_without_any_retained_member_evidence"], ["2023-11", "2023-12", "2024-01"])

    def test_duplicate_cache_keeps_legacy_row_denominator_and_monthly_get_is_unavailable(self):
        self.cache(self.student, 2023, 9, absent=40)
        self.cache(self.student, 2023, 9)
        for _ in range(40):
            self.report(self.student, "2023-09-10", status="absent")
        result = self.result()
        self.assertEqual(result["overall"]["duplicate_cache_groups"], 1)
        self.assertEqual(result["months"][0]["unique_cache_vs_retained_legacy_facts"]["compared_evidence_members"], 0)
        term = result["terms"][0]["legacy_stage_cache_vs_candidate_full_natural_term"]
        self.assertEqual((term["before_total"], term["after_total"]), ("30.00", "48.00"))

    def test_missing_cache_legacy_zero_is_not_confused_with_natural_base(self):
        self.report(self.student, "2023-09-10", status="absent")
        result = self.result()
        self.assertEqual(result["months"][0]["report_members_without_cache"], 1)
        comparison = result["terms"][0]["legacy_stage_cache_vs_natural_through_last_evidence"]
        self.assertEqual((comparison["before_total"], comparison["after_total"]), ("0.00", "58.00"))

    def test_legacy_untyped_fields_and_new_typed_candidate_are_distinguished(self):
        self.cache(self.student, 2023, 9, absent=1, high=1)
        self.report(self.student, "2023-09-10", status="absent", level="high", kind="activity")
        month = self.result()["months"][0]
        self.assertEqual(month["legacy_field_fact_count_totals"]["absent"], 1)
        self.assertEqual(month["typed_fact_count_totals"]["absent"], 0)
        self.assertEqual(month["unique_cache_vs_retained_legacy_facts"]["total_delta"], "0.00")
        self.assertEqual(month["unique_cache_vs_typed_facts"]["total_delta"], "2.00")
        result = self.result()
        self.assertEqual(result["quality"]["reports_with_off_type_scoring_fields"], 1)
        self.assertEqual(result["off_type_report_counts_by_activity_kind"], {"activity": 1})

    def test_legacy_spring_includes_august_while_candidate_excludes_it(self):
        self.cache(self.student, 2023, 7, late=1)
        self.cache(self.student, 2023, 8, absent=2)
        self.report(self.student, "2023-07-10", status="late")
        for _ in range(2):
            self.report(self.student, "2023-08-10", status="absent")
        result = self.result()
        term = result["terms"][0]
        self.assertEqual(term["legacy_stage_months"][-1], "2023-08")
        self.assertEqual(term["natural_term_months"][-1], "2023-07")
        comparison = term["legacy_stage_cache_vs_candidate_full_natural_term"]
        self.assertEqual((comparison["before_total"], comparison["after_total"]), ("57.50", "59.83"))
        self.assertEqual(result["overall"]["august_report_rows"], 2)

    def test_january_bug_and_unbounded_future_rows_are_reproduced(self):
        for year, month in ((2025, 2), (2025, 9), (2026, 1), (2026, 9)):
            self.cache(self.student, year, month, late=1)
        result = self.result()
        term = next(row for row in result["terms"] if row["term"] == "2025-autumn")
        old = term["legacy_current_formula_at_last_observed_month"]
        self.assertEqual(old["lower_month_inclusive"], "2025-01")
        self.assertIsNone(old["upper_bound"])
        self.assertEqual(old["selected_cache_rows_outside_this_natural_term"], 2)

    def test_output_has_no_id_identity_secret_or_individual_score(self):
        self.cache(self.student, 2023, 9, late=1)
        self.report(self.student, "2023-09-10", status="late")
        result = self.result()
        output = json.dumps(result)
        self.assertNotIn("PRIVATE_", output)
        self.assertNotIn(str(self.student), output)
        self.assertEqual(result["retained_admin_deletion_events"]["activity"]["events"], 1)
        forbidden_keys = {"id", "student_id", "name", "number", "password", "managecode", "score", "students"}

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden_keys)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(result)

    def test_source_is_readonly_and_nonexistent_path_is_not_created(self):
        self.cache(self.student, 2023, 9)
        self.db.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        compare_database(self.path)
        connection = open_readonly(self.path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE manage_summarycount SET absent_count=99")
        finally:
            connection.close()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        missing = Path(self.temp.name) / "PRIVATE_MISSING_PATH.sqlite3"
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main([str(missing)]), 2)
        self.assertNotIn("PRIVATE_", error.getvalue())
        self.assertFalse(missing.exists())


class OriginalAlgorithmReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        source = subprocess.run(["git", "show", "00e487b:manage/models.py"], cwd=root,
                                check=True, capture_output=True, text=True).stdout
        cls.tree = ast.parse(source)

    def method(self, owner, name, namespace):
        cls = next(node for node in self.tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[method], type_ignores=[]), "<git-00e487b-reference>", "exec"), namespace)
        return namespace[name]

    def test_formula_matches_actual_git_version_including_floor_and_no_ceiling(self):
        old_score = self.method("SummaryCount", "score", {})
        cases = [{}, {"absent": 100}, {"high": 100}, {"leave": 999}, {key: 1 for key in CATEGORIES}]
        for counts in cases:
            with self.subTest(counts=counts):
                instance = SimpleNamespace(**{values[3]: counts.get(key, 0) for key, values in CATEGORIES.items()})
                self.assertEqual(score(counts), old_score(instance))

    def test_january_start_matches_actual_git_version(self):
        namespace = {"datetime": SimpleNamespace(now=lambda: datetime(2026, 1, 15))}
        method = self.method("Student", "summary_term", namespace)
        actual = method(SimpleNamespace(summary_total_avg=lambda year, month: (year, month)))
        self.assertEqual(legacy_current_start(date(2026, 1, 15)), actual)
        self.assertEqual(actual, (2025, 1))
        self.assertEqual(len(term_months((2025, "autumn"))), 5)


if __name__ == "__main__":
    unittest.main()
