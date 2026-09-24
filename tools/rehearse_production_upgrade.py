"""Offline 2026-lineage rehearsal on disposable SQLite backups only.

This program never opens the source for writing and refuses an existing output
directory. It is a release rehearsal, not a production deployment command.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MIGRATION_SHA256 = 'ce4016f57900cec7fa86196072830f86e9aac519683d0970064bac9da88d2dfd'
BUSINESS_TABLES = ('manage_class', 'manage_student', 'manage_activity',
                   'manage_report', 'manage_summarycount')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect_readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def state(path):
    with connect_readonly(path) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite integrity_check failed.')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('SQLite foreign_key_check failed.')
        ledger = [row[0] for row in db.execute(
            "SELECT name FROM django_migrations WHERE app='manage' ORDER BY name")]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(BUSINESS_TABLES).issubset(tables):
            raise RuntimeError('Required legacy business tables are missing.')
        facts = {}
        for table in BUSINESS_TABLES:
            columns = [row[1] for row in db.execute(f'PRAGMA table_info({table})')]
            if table == 'manage_class':
                columns.remove('managecode')  # Existing 0003 hashes this retired value.
            rows = db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY id').fetchall()
            encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()
            facts[table] = {'count': len(rows), 'sha256': hashlib.sha256(encoded).hexdigest(),
                            'columns': columns}
        report_columns = {row[1] for row in db.execute('PRAGMA table_info(manage_report)')}
        summary_columns = {row[1] for row in db.execute('PRAGMA table_info(manage_summarycount)')}
        return {'ledger': ledger, 'facts': facts, 'report_columns': report_columns,
                'summary_columns': summary_columns}


def assert_facts(before, after):
    with connect_readonly(after) as db:
        for table, item in before['facts'].items():
            rows = db.execute(f'SELECT {",".join(item["columns"])} FROM {table} ORDER BY id').fetchall()
            encoded = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()
            if len(rows) != item['count'] or hashlib.sha256(encoded).hexdigest() != item['sha256']:
                raise RuntimeError(f'{table} source facts changed during migration.')


def backup(source, target):
    with connect_readonly(source) as original, sqlite3.connect(target) as copied:
        original.backup(copied)
    os.chmod(target, 0o600)


def run_manage(database, *args):
    env = {**os.environ, 'DUXING_DATABASE': str(database.resolve()),
           'DUXING_MIGRATION_LINEAGE': 'production-2026', 'DUXING_DEBUG': '1'}
    result = subprocess.run([sys.executable, str(ROOT / 'manage.py'), *args],
        cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'{" ".join(args)} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Immutable raw VPS SQLite snapshot')
    parser.add_argument('--output-dir', required=True, type=Path, help='New, disposable local directory')
    parser.add_argument('--as-of', required=True, help='Cutover date YYYY-MM-DD, Beijing time')
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        parser.error('Source snapshot does not exist.')
    migration = ROOT / 'manage/production_migrations/0001_initial.py'
    if digest(migration) != SOURCE_MIGRATION_SHA256:
        raise RuntimeError('Tracked 2026 initial migration differs from the verified VPS migration.')
    source_sha = digest(source)
    original = state(source)
    expected = {'discipline', 'discipline_low_count', 'discipline_mid_count', 'discipline_high_count'}
    if original['ledger'] != ['0001_initial'] or 'discipline' not in original['report_columns'] or not expected.difference({'discipline'}).issubset(original['summary_columns']):
        raise RuntimeError('Source is not the verified 2026 initial lineage/schema; no migration performed.')
    target_dir = args.output_dir.resolve()
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    target = target_dir / 'upgraded.sqlite3'
    sentinel = target_dir / 'nonzero-sentinel.sqlite3'
    restored = target_dir / 'restored-legacy.sqlite3'
    backup(source, target)
    backup(source, sentinel)
    with sqlite3.connect(sentinel) as db:
        activity = db.execute('SELECT id FROM manage_activity ORDER BY id LIMIT 1').fetchone()
        report = db.execute('SELECT id,student_id FROM manage_report ORDER BY id LIMIT 1').fetchone()
        if not activity or not report:
            raise RuntimeError('Source has no facts for the nonzero discipline sentinel.')
        db.execute("UPDATE manage_activity SET activity_type='discipline' WHERE id=?", (activity[0],))
        db.execute("UPDATE manage_report SET discipline='high' WHERE id=?", (report[0],))
        db.execute("UPDATE manage_summarycount SET discipline_high_count=2 WHERE student_id=?", (report[1],))
    sentinel_before = state(sentinel)
    run_manage(sentinel, 'migrate', '--noinput')
    assert_facts(sentinel_before, sentinel)
    with connect_readonly(sentinel) as db:
        if db.execute("SELECT discipline FROM manage_report WHERE id=?", (report[0],)).fetchone()[0] != 'high':
            raise RuntimeError('Nonzero discipline report was lost.')
        if db.execute("SELECT MAX(discipline_high_count) FROM manage_summarycount WHERE student_id=?", (report[1],)).fetchone()[0] < 2:
            raise RuntimeError('Nonzero discipline summary was lost.')
    run_manage(target, 'migrate', '--noinput')
    assert_facts(original, target)
    run_manage(target, 'bootstrap_legacy_current_term', '--as-of', args.as_of,
               '--source-sha256', source_sha)
    with connect_readonly(target) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Upgraded copy failed integrity verification.')
        if db.execute('SELECT COUNT(*) FROM manage_class WHERE legacy_pending').fetchone()[0]:
            raise RuntimeError('A class remains pending after baseline import.')
        report_count = db.execute('SELECT COUNT(*) FROM manage_currentclassreport').fetchone()[0]
        if report_count != original['facts']['manage_class']['count']:
            raise RuntimeError('Current public report count does not match classes.')
    backup(source, restored)
    assert_facts(original, restored)
    if state(restored)['ledger'] != ['0001_initial'] or digest(source) != source_sha:
        raise RuntimeError('Legacy restore/source integrity check failed.')
    result = {'source_sha256': source_sha,
              'source_counts': {table: item['count'] for table,item in original['facts'].items()},
              'schema_migration': 'production-2026 0001 -> 0010',
              'source_facts_preserved': True, 'nonzero_discipline_preserved': True,
              'current_term_baseline': args.as_of, 'current_reports': report_count,
              'restore_verified': True}
    (target_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
