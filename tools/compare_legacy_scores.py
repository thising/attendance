#!/usr/bin/env python3
"""Read-only, aggregate-only comparison of legacy caches and candidate scores.

This produces review evidence, never an import file or an approved historical
roster. Internal student IDs are used to align evidence but are not emitted.
"""
import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import sys

try:
    from .inspect_legacy import open_readonly, scalar
except ImportError:
    from inspect_legacy import open_readonly, scalar


LEGACY_REF = "00e487b:manage/models.py"
CATEGORIES = {
    "absent": ("status", "absent", "class", "absent_count", -2),
    "late": ("status", "late", "class", "late_count", -1),
    "leave": ("status", "leave", "class", "leave_count", 0),
    "low": ("level", "low", "activity", "low_count", 1),
    "mid": ("level", "mid", "activity", "mid_count", 3),
    "high": ("level", "high", "activity", "high_count", 5),
    "dlow": ("discipline", "low", "discipline", "discipline_low_count", -5),
    "dmid": ("discipline", "mid", "discipline", "discipline_mid_count", -8),
    "dhigh": ("discipline", "high", "discipline", "discipline_high_count", -12),
}


def score(counts):
    return max(0, 60 + sum(counts.get(key, 0) * values[4] for key, values in CATEGORIES.items()))


def money(value):
    value = Fraction(value)
    return str((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def mean(values):
    return sum(values, Fraction(0)) / len(values) if values else Fraction(0)


def comparison(before, after):
    assert len(before) == len(after)
    return {
        "compared_evidence_members": len(before),
        "changed_exact": sum(left != right for left, right in zip(before, after)),
        "changed_at_two_decimals": sum(money(left) != money(right) for left, right in zip(before, after)),
        "increased": sum(right > left for left, right in zip(before, after)),
        "decreased": sum(right < left for left, right in zip(before, after)),
        "before_total": money(sum(before)), "after_total": money(sum(after)),
        "total_delta": money(sum(after) - sum(before)),
        "before_mean": money(mean(before)), "after_mean": money(mean(after)),
    }


def legacy_current_start(day):
    # The January bug is intentional here: reproduce, do not repair the baseline.
    if day.month >= 9:
        return day.year, 9
    if day.month >= 2:
        return day.year, 2
    return day.year - 1, 1


def natural_term(month):
    year, number = month
    if number == 8:
        return None
    if number == 1:
        return year - 1, "autumn"
    return year, "spring" if number < 8 else "autumn"


def term_months(term, legacy=False):
    year, season = term
    if season == "spring":
        return [(year, number) for number in range(2, 9 if legacy else 8)]
    return [(year, number) for number in range(9, 13)] + [(year + 1, 1)]


def month_label(month):
    return f"{month[0]:04d}-{month[1]:02d}"


def add_counts(rows):
    return {key: sum(row.get(key, 0) for row in rows) for key in CATEGORIES}


def read_evidence(connection):
    caches = defaultdict(list)
    invalid_cache_rows = 0
    negative_cache_cells = 0
    fields = ", ".join(values[3] for values in CATEGORIES.values())
    for row in connection.execute("SELECT student_id, year, month, " + fields + " FROM manage_summarycount"):
        valid = (type(row["student_id"]) is int and type(row["year"]) is int
                 and type(row["month"]) is int and 1 <= row["year"] <= 9998
                 and 1 <= row["month"] <= 12
                 and all(type(row[values[3]]) is int for values in CATEGORIES.values()))
        if not valid:
            invalid_cache_rows += 1
            continue
        counts = {key: row[values[3]] for key, values in CATEGORIES.items()}
        negative_cache_cells += sum(value < 0 for value in counts.values())
        caches[(row["student_id"], row["year"], row["month"])].append(counts)

    expressions = []
    for key, (field, value, kind, _, _) in CATEGORIES.items():
        condition = f"r.{field} = '{value}'"
        expressions.append(f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END) AS old_{key}")
        expressions.append(f"SUM(CASE WHEN {condition} AND a.activity_type = '{kind}' THEN 1 ELSE 0 END) AS typed_{key}")
    facts, typed, report_counts = {}, {}, {}
    query = """
        SELECT r.student_id, CAST(strftime('%Y', a.time) AS INTEGER) AS year,
            CAST(strftime('%m', a.time) AS INTEGER) AS month, COUNT(*) AS report_count,
    """ + ", ".join(expressions) + """
        FROM manage_report r JOIN manage_activity a ON a.id = r.activity_id
        WHERE datetime(a.time) IS NOT NULL
        GROUP BY r.student_id, year, month
    """
    invalid_fact_groups = 0
    for row in connection.execute(query):
        if (type(row["student_id"]) is not int or not 1 <= row["year"] <= 9998
                or not 1 <= row["month"] <= 12):
            invalid_fact_groups += 1
            continue
        key = row["student_id"], row["year"], row["month"]
        facts[key] = {category: row["old_" + category] for category in CATEGORIES}
        typed[key] = {category: row["typed_" + category] for category in CATEGORIES}
        report_counts[key] = row["report_count"]

    activity_dates = dict(connection.execute("""
        SELECT MIN(date(time)) first_date, MAX(date(time)) last_date,
            COUNT(CASE WHEN datetime(time) IS NULL THEN 1 END) invalid_dates
        FROM manage_activity
    """).fetchone())
    invalid_date_reports = scalar(connection, """
        SELECT COUNT(*) FROM manage_report r JOIN manage_activity a ON a.id = r.activity_id
        WHERE datetime(a.time) IS NULL
    """)
    orphan_reports = scalar(connection, """
        SELECT COUNT(*) FROM manage_report r LEFT JOIN manage_activity a ON a.id = r.activity_id
        LEFT JOIN manage_student s ON s.id = r.student_id WHERE a.id IS NULL OR s.id IS NULL
    """)
    cross_class = scalar(connection, """
        SELECT COUNT(*) FROM manage_report r JOIN manage_activity a ON a.id = r.activity_id
        JOIN manage_student s ON s.id = r.student_id WHERE a.inclass_id != s.inclass_id
    """)
    duplicate_reports = scalar(connection, """
        SELECT COUNT(*) FROM (SELECT 1 FROM manage_report GROUP BY activity_id, student_id HAVING COUNT(*) > 1)
    """)
    off_type_condition = """
        (COALESCE(a.activity_type,'')!='class' AND r.status IN ('absent','late','leave')) OR
        (COALESCE(a.activity_type,'')!='activity' AND r.level IN ('low','mid','high')) OR
        (COALESCE(a.activity_type,'')!='discipline' AND r.discipline IN ('low','mid','high'))
    """
    off_type_by_kind = {row[0]: row[1] for row in connection.execute("""
        SELECT CASE WHEN a.activity_type IN ('class','activity','discipline')
            THEN a.activity_type ELSE 'unrecognized' END AS kind, COUNT(*)
        FROM manage_report r JOIN manage_activity a ON a.id=r.activity_id WHERE
    """ + off_type_condition + " GROUP BY kind")}
    existing_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    deletions = None
    if {"django_admin_log", "django_content_type"} <= existing_tables:
        deletions = {}
        for model in ("class", "student", "activity", "report", "summarycount"):
            row = connection.execute("""
                SELECT COUNT(*) events, MIN(date(l.action_time)) first_date, MAX(date(l.action_time)) last_date
                FROM django_admin_log l JOIN django_content_type t ON t.id=l.content_type_id
                WHERE l.action_flag=3 AND t.app_label='manage' AND t.model=?
            """, (model,)).fetchone()
            deletions[model] = dict(row)
    context = {
        "snapshot_table_counts": {table: scalar(connection, "SELECT COUNT(*) FROM " + table)
                                  for table in ("manage_class", "manage_student", "manage_activity", "manage_report", "manage_summarycount")},
        "activity_date_span": activity_dates,
        "current_snapshot_students_without_score_evidence": scalar(connection, """
            SELECT COUNT(*) FROM manage_student s
            WHERE NOT EXISTS (SELECT 1 FROM manage_summarycount c WHERE c.student_id=s.id)
              AND NOT EXISTS (SELECT 1 FROM manage_report r WHERE r.student_id=s.id)
        """),
        "quality": {"invalid_cache_rows_excluded": invalid_cache_rows,
                    "negative_cache_count_cells": negative_cache_cells,
                    "invalid_fact_groups_excluded": invalid_fact_groups,
                    "reports_with_invalid_dates_excluded": invalid_date_reports,
                    "orphan_report_rows": orphan_reports, "cross_class_report_rows": cross_class,
                    "duplicate_report_groups": duplicate_reports,
                    "reports_with_off_type_scoring_fields": sum(off_type_by_kind.values())},
        "off_type_report_counts_by_activity_kind": off_type_by_kind,
        "retained_admin_deletion_events": deletions,
    }
    return caches, facts, typed, report_counts, context


def build_report(caches, facts, typed, report_counts, context):
    keys = set(caches) | set(facts)
    observed_months = sorted({key[1:] for key in keys})
    month_results = []
    for month in observed_months:
        selected = sorted(key for key in keys if key[1:] == month)
        cache_rows = [row for key in selected for row in caches.get(key, [])]
        cached_total = add_counts(cache_rows)
        fact_total = add_counts([facts.get(key, {}) for key in selected])
        typed_total = add_counts([typed.get(key, {}) for key in selected])
        comparable = [key for key in selected if len(caches.get(key, [])) == 1]
        old_scores = [Fraction(score(caches[key][0])) for key in comparable]
        rebuilt_scores = [Fraction(score(facts.get(key, {}))) for key in comparable]
        typed_scores = [Fraction(score(typed.get(key, {}))) for key in comparable]
        month_results.append({
            "month": month_label(month), "evidence_member_count": len(selected),
            "cache_rows": len(cache_rows), "report_rows": sum(report_counts.get(key, 0) for key in selected),
            "members_with_reports": sum(key in facts for key in selected),
            "members_with_cache": sum(key in caches for key in selected),
            "report_members_without_cache": sum(key in facts and key not in caches for key in selected),
            "duplicate_cache_groups": sum(len(caches.get(key, [])) > 1 for key in selected),
            "cache_groups_with_count_difference": sum(
                add_counts(caches[key]) != add_counts([facts.get(key, {})]) for key in selected if key in caches),
            "cached_count_totals": cached_total, "legacy_field_fact_count_totals": fact_total,
            "typed_fact_count_totals": typed_total,
            "cache_minus_legacy_fact_counts": {category: cached_total[category] - fact_total[category] for category in CATEGORIES},
            "unique_cache_vs_retained_legacy_facts": comparison(old_scores, rebuilt_scores),
            "unique_cache_vs_typed_facts": comparison(old_scores, typed_scores),
        })

    term_results = []
    terms = sorted({natural_term(month) for month in observed_months} - {None}, key=lambda term: term_months(term)[0])
    for term in terms:
        natural = term_months(term)
        old_scope = term_months(term, legacy=True)
        cohort = sorted({key[0] for key in keys if key[1:] in natural})
        observed = [month for month in natural if month in observed_months]
        cutoff = max(observed)
        elapsed_months = [month for month in natural if month <= cutoff]
        old_stage, rebuilt_same_rows, old_current, candidate_elapsed, candidate_complete = [], [], [], [], []
        untyped_elapsed, untyped_complete = [], []
        members_without_cache, extra_current_rows = 0, 0
        current_start = legacy_current_start(date(cutoff[0], cutoff[1], 1))
        for student in cohort:
            cache_entries = [(key, row) for key, rows in caches.items()
                             if key[0] == student and key[1:] in old_scope for row in rows]
            members_without_cache += not cache_entries
            old_stage.append(mean([Fraction(score(row)) for _, row in cache_entries]))
            rebuilt_same_rows.append(mean([Fraction(score(facts.get(key, {}))) for key, _ in cache_entries]))
            current_entries = [(key, row) for key, rows in caches.items()
                               if key[0] == student and key[1:] >= current_start for row in rows]
            old_current.append(mean([Fraction(score(row)) for _, row in current_entries]))
            extra_current_rows += sum(key[1:] not in natural for key, _ in current_entries)
            candidate_elapsed.append(mean([Fraction(score(typed.get((student, *month), {}))) for month in elapsed_months]))
            candidate_complete.append(mean([Fraction(score(typed.get((student, *month), {}))) for month in natural]))
            untyped_elapsed.append(mean([Fraction(score(facts.get((student, *month), {}))) for month in elapsed_months]))
            untyped_complete.append(mean([Fraction(score(facts.get((student, *month), {}))) for month in natural]))
        term_results.append({
            "term": f"{term[0]}-{term[1]}",
            "cohort_basis": "Only IDs with retained cache or report evidence inside this natural term; not an approved historical roster.",
            "evidence_member_count": len(cohort), "members_without_legacy_stage_cache": members_without_cache,
            "legacy_stage_months": [month_label(month) for month in old_scope],
            "natural_term_months": [month_label(month) for month in natural],
            "last_observed_month": month_label(cutoff),
            "natural_months_through_last_evidence": [month_label(month) for month in elapsed_months],
            "months_without_any_retained_member_evidence": [month_label(month) for month in natural if month not in observed_months],
            "candidate_unobserved_member_month_slots_through_last_evidence": len(cohort) * len(elapsed_months) - sum(key[0] in cohort and key[1:] in elapsed_months for key in keys),
            "candidate_unobserved_member_month_slots_full_term": len(cohort) * len(natural) - sum(key[0] in cohort and key[1:] in natural for key in keys),
            "legacy_stage_cache_vs_same_rows_rebuilt_from_facts": comparison(old_stage, rebuilt_same_rows),
            "legacy_stage_cache_vs_natural_months_legacy_fields": comparison(old_stage, untyped_elapsed),
            "natural_months_legacy_fields_vs_typed_fields": comparison(untyped_elapsed, candidate_elapsed),
            "legacy_stage_cache_vs_natural_through_last_evidence": comparison(old_stage, candidate_elapsed),
            "legacy_stage_cache_vs_full_natural_term_legacy_fields": comparison(old_stage, untyped_complete),
            "full_natural_term_legacy_fields_vs_typed_fields": comparison(untyped_complete, candidate_complete),
            "legacy_stage_cache_vs_candidate_full_natural_term": comparison(old_stage, candidate_complete),
            "legacy_current_formula_at_last_observed_month": {
                "lower_month_inclusive": month_label(current_start), "upper_bound": None,
                "selected_cache_rows_outside_this_natural_term": extra_current_rows,
                "vs_natural_through_last_evidence": comparison(old_current, candidate_elapsed),
            },
        })

    return {
        "status": "candidate_review_only_no_historical_correction_authorized",
        "legacy_source": LEGACY_REF,
        "read_only": {"uri_mode": "ro", "query_only": True},
        "assumptions": [
            "Use the legacy fixed weights, monthly floor zero, no ceiling; exact rational averages and two-decimal HALF_UP aggregate presentation.",
            "Legacy signals count status/level/discipline without checking activity type; typed candidates check the corresponding activity type. Preview records are included in both.",
            "Legacy semester scores average stored cache rows, including duplicate rows; no stored rows means zero. Legacy monthly get is unavailable for duplicated groups.",
            "The legacy current-term query has no upper bound and January starts at January of the previous year. Legacy previous spring includes August.",
            "Candidate denominators assume the observed evidence cohort was present for every included natural month; this is not proven historical enrollment.",
            "No retained event is treated as zero only for the candidate calculation. Missing retained facts do not prove no real-world activity occurred.",
            "Dates use SQLite parsing of legacy stored timestamps with no additional application timezone conversion; historical timezone provenance is unverified.",
        ],
        "evidence_gaps": [
            "The snapshot has no reliable semester roster, enrollment history, or certified final score archive; current students are not substituted for historical membership.",
            "Deleted students, activities and reports cannot be reconstructed from surviving caches. Admin deletion counts are partial evidence only; cascades and web/script deletions may leave no equivalent ledger.",
            "Surviving cache rows may reflect later corrections; reproducing this snapshot is not proof of what users saw at the time.",
            "Missing migration 0002_auto_20200927_0037 source remains unresolved; physical schema similarity does not establish historical data transformations.",
        ],
        **context,
        "overall": {
            "observed_evidence_members_across_all_months": len({key[0] for key in keys}),
            "observed_student_month_groups": len(keys),
            "months": len(month_results), "natural_terms_with_evidence": len(term_results),
            "duplicate_cache_groups": sum(len(rows) > 1 for rows in caches.values()),
            "cache_groups_with_count_difference": sum(row["cache_groups_with_count_difference"] for row in month_results),
            "august_cache_rows": sum(row["cache_rows"] for row in month_results if row["month"].endswith("-08")),
            "august_report_rows": sum(row["report_rows"] for row in month_results if row["month"].endswith("-08")),
        },
        "months": month_results, "terms": term_results,
    }


def compare_database(path):
    connection = open_readonly(path)
    try:
        connection.execute("BEGIN")
        return build_report(*read_evidence(connection))
    finally:
        connection.rollback()
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing legacy SQLite backup, opened mode=ro")
    args = parser.parse_args(argv)
    try:
        result = compare_database(args.database)
    except (OSError, sqlite3.Error) as error:
        print(json.dumps({"error": "Read-only comparison failed", "error_type": type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
