#!/usr/bin/env python3
"""Inspect a legacy SQLite database without exposing individual business records.

Only schema metadata, the Django migration ledger, and aggregate measurements
are emitted. No Django settings or models are imported and no migrations run.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys


BUSINESS_TABLES = (
    "manage_class", "manage_student", "manage_activity", "manage_report",
    "manage_summarycount",
)
EXPECTED_COLUMNS = {
    "manage_class": {"id", "classname", "sharecode", "managecode", "owner_id"},
    "manage_student": {"id", "number", "name", "sex", "inclass_id"},
    "manage_activity": {"id", "time", "activity_type", "name", "status", "inclass_id"},
    "manage_report": {"id", "activity_id", "student_id", "status", "level", "discipline"},
    "manage_summarycount": {
        "id", "student_id", "year", "month", "absent_count", "late_count",
        "leave_count", "low_count", "mid_count", "high_count",
        "discipline_low_count", "discipline_mid_count", "discipline_high_count",
    },
}


def identifier(value):
    """Quote schema identifiers; never interpolate data into SQL."""
    return '"' + value.replace('"', '""') + '"'


def readonly_authorizer(action, arg1, arg2, database, source):
    allowed = {
        sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE,
        sqlite3.SQLITE_TRANSACTION,
    }
    if action in allowed:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return (sqlite3.SQLITE_DENY if (arg2 or "").lower() == "load_extension"
                else sqlite3.SQLITE_OK)
    if action == sqlite3.SQLITE_PRAGMA:
        schema_queries = {"table_info", "foreign_key_list", "index_list", "index_info"}
        if arg1 in schema_queries or (arg1 == "query_only" and arg2 is None):
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def open_readonly(path):
    """Return a URI mode=ro connection that also rejects write SQL and ATTACH."""
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise OSError("A regular database file is required")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(readonly_authorizer)
    return connection


def scalar(connection, sql):
    return connection.execute(sql).fetchone()[0]


def inspect_connection(connection):
    """Read a consistent snapshot; returned values contain no individual records."""
    connection.execute("BEGIN")
    try:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        schema = {}
        columns = {}
        counts = {}
        for name in names:
            table = identifier(name)
            info = list(connection.execute("PRAGMA table_info(" + table + ")"))
            columns[name] = {row["name"] for row in info}
            indexes = []
            for index in connection.execute("PRAGMA index_list(" + table + ")"):
                index_columns = [row["name"] for row in connection.execute(
                    "PRAGMA index_info(" + identifier(index["name"]) + ")"
                )]
                indexes.append({
                    "name": index["name"], "unique": bool(index["unique"]),
                    "columns": index_columns,
                })
            schema[name] = {
                "columns": [{
                    "name": row["name"], "type": row["type"],
                    "not_null": bool(row["notnull"]), "primary_key": row["pk"],
                    # Do not emit arbitrary default expressions or SQL literals.
                    "has_default": row["dflt_value"] is not None,
                } for row in info],
                "foreign_keys": [{
                    "column": row["from"], "table": row["table"],
                    "target_column": row["to"], "on_delete": row["on_delete"],
                } for row in connection.execute("PRAGMA foreign_key_list(" + table + ")")],
                "indexes": indexes,
            }
            counts[name] = scalar(connection, "SELECT COUNT(*) FROM " + table)

        ledger = None
        if {"app", "name", "applied"} <= columns.get("django_migrations", set()):
            ledger = [dict(row) for row in connection.execute(
                "SELECT app, name, datetime(applied) AS applied "
                "FROM django_migrations ORDER BY app, name"
            )]

        compatibility = {}
        for table, expected in EXPECTED_COLUMNS.items():
            actual = columns.get(table, set())
            compatibility[table] = {
                "exists": table in schema,
                "missing_columns": sorted(expected - actual),
                "extra_columns": sorted(actual - expected),
            }

        def available(table, required):
            return set(required) <= columns.get(table, set())

        duplicates = {}
        for key, table, keys in (
            ("class_owner_name", "manage_class", ("owner_id", "classname")),
            ("class_sharecode", "manage_class", ("sharecode",)),
            ("student_class_number", "manage_student", ("inclass_id", "number")),
            ("report_activity_student", "manage_report", ("activity_id", "student_id")),
            ("summary_student_year_month", "manage_summarycount", ("student_id", "year", "month")),
        ):
            duplicates[key] = None
            if available(table, keys):
                duplicates[key] = scalar(connection,
                    "SELECT COUNT(*) FROM (SELECT 1 FROM " + identifier(table)
                    + " GROUP BY " + ", ".join(map(identifier, keys))
                    + " HAVING COUNT(*) > 1)"
                )

        orphan_counts = {}
        for table, column, parent in (
            ("manage_class", "owner_id", "auth_user"),
            ("manage_student", "inclass_id", "manage_class"),
            ("manage_activity", "inclass_id", "manage_class"),
            ("manage_report", "activity_id", "manage_activity"),
            ("manage_report", "student_id", "manage_student"),
            ("manage_summarycount", "student_id", "manage_student"),
        ):
            key = table + "." + column
            orphan_counts[key] = None
            if available(table, (column,)) and available(parent, ("id",)):
                orphan_counts[key] = scalar(connection,
                    "SELECT COUNT(*) FROM " + identifier(table) + " child LEFT JOIN "
                    + identifier(parent) + " parent ON child." + identifier(column)
                    + " = parent.id WHERE child." + identifier(column)
                    + " IS NOT NULL AND parent.id IS NULL"
                )

        cross_class = None
        if (available("manage_report", ("activity_id", "student_id"))
                and available("manage_activity", ("id", "inclass_id"))
                and available("manage_student", ("id", "inclass_id"))):
            cross_class = scalar(connection, """
                SELECT COUNT(*) FROM manage_report r
                JOIN manage_activity a ON a.id = r.activity_id
                JOIN manage_student s ON s.id = r.student_id
                WHERE a.inclass_id != s.inclass_id
            """)

        dates = {}
        if available("manage_activity", ("time",)):
            dates["activity"] = dict(connection.execute("""
                SELECT MIN(date(time)) AS first_date, MAX(date(time)) AS last_date,
                    COUNT(CASE WHEN datetime(time) IS NULL THEN 1 END) AS invalid_dates,
                    COUNT(CASE WHEN strftime('%m', time) = '08' THEN 1 END) AS august_records
                FROM manage_activity
            """).fetchone())
        if available("manage_summarycount", ("year", "month")):
            valid = "typeof(year) = 'integer' AND typeof(month) = 'integer' AND year BETWEEN 1 AND 9999 AND month BETWEEN 1 AND 12"
            dates["summary"] = dict(connection.execute(
                "SELECT MIN(CASE WHEN " + valid + " THEN printf('%04d-%02d', year, month) END) AS first_month, "
                "MAX(CASE WHEN " + valid + " THEN printf('%04d-%02d', year, month) END) AS last_month, "
                "COUNT(CASE WHEN NOT (" + valid + ") THEN 1 END) AS invalid_months "
                "FROM manage_summarycount"
            ).fetchone())

        missing_summary_months = None
        if (available("manage_report", ("student_id", "activity_id"))
                and available("manage_activity", ("id", "time"))
                and available("manage_summarycount", ("student_id", "year", "month"))):
            missing_summary_months = scalar(connection, """
                SELECT COUNT(*) FROM (
                    SELECT r.student_id, CAST(strftime('%Y', a.time) AS INTEGER) AS year,
                        CAST(strftime('%m', a.time) AS INTEGER) AS month
                    FROM manage_report r JOIN manage_activity a ON a.id = r.activity_id
                    WHERE datetime(a.time) IS NOT NULL
                    GROUP BY r.student_id, year, month
                ) represented WHERE NOT EXISTS (
                    SELECT 1 FROM manage_summarycount sc
                    WHERE sc.student_id = represented.student_id
                        AND sc.year = represented.year AND sc.month = represented.month
                )
            """)

        return {
            "read_only": {"uri_mode": "ro", "query_only": bool(scalar(connection, "PRAGMA query_only"))},
            "schema": schema, "migration_ledger": ledger, "row_counts": counts,
            "legacy_column_compatibility": compatibility,
            "duplicate_group_counts": duplicates, "orphan_reference_counts": orphan_counts,
            "cross_class_report_count": cross_class, "date_spans": dates,
            "report_months_without_summary_count": missing_summary_months,
        }
    finally:
        connection.rollback()


def inspect_database(path):
    connection = open_readonly(path)
    try:
        return inspect_connection(connection)
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing legacy SQLite file (opened read-only)")
    args = parser.parse_args(argv)
    try:
        result = inspect_database(args.database)
    except (OSError, sqlite3.Error) as error:
        # Database errors can contain input data; report only their class.
        print(json.dumps({"error": "Read-only inspection failed", "error_type": type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
