"""Duxing NAS backup: verified business archives and encrypted append-only recovery.

Uses the established after-school-diary receiver/finalizer/Restic pattern.
Only completed paired local backups are read; no live database is changed.
"""
import argparse
import csv
import fcntl
import io
import json
import os
import re
import shutil
import smtplib
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .offsite_backup import digest, verify_bundle, write_private, write_status
except ImportError:
    from offsite_backup import digest, verify_bundle, write_private, write_status


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def private_json(path, data):
    write_private(Path(path), (json.dumps(data, ensure_ascii=False, sort_keys=True) + '\n').encode())


def run(command, env=None, timeout=1800):
    result = subprocess.run([str(x) for x in command], env=env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        # Subprocess output can contain repository credentials or business paths.
        raise RuntimeError(Path(command[0]).name + ' operation failed')
    return result.stdout


def export_readable(bundle, destination):
    """Allowlisted business facts; never export auth tables, links or key material."""
    bundle, destination = Path(bundle), Path(destination)
    manifest = verify_bundle(bundle)
    destination.mkdir(mode=0o700)
    with sqlite3.connect((bundle / 'managedb.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        available = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        facts = {}
        for table in ['manage_student', 'manage_activity', 'manage_report', 'manage_summarycount',
                      'manage_rosterversion', 'manage_classterm', 'manage_scoringpolicyversion']:
            if table in available:
                facts[table] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
        columns = {'id', 'code', 'classname', 'archived', 'ended_at', 'ended_term_key', 'revision', 'report_revision'}
        existing = [r[1] for r in db.execute('PRAGMA table_info(manage_class)') if r[1] in columns]
        facts['manage_class'] = [dict(row) for row in db.execute('SELECT ' + ','.join(existing) + ' FROM manage_class ORDER BY id')]
        reports = [json.loads(row[0]) for row in db.execute('SELECT payload FROM manage_currentclassreport ORDER BY id')]
    private_json(destination / 'business.json', {'release': manifest['release'],
                 'source_backup': bundle.name, 'created_at_beijing': manifest['created_at_beijing'], 'facts': facts})
    private_json(destination / 'scores.json', reports)
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    keys = ['absent', 'late', 'leave', 'low', 'mid', 'high', 'dlow', 'dmid', 'dhigh']
    writer.writerow(['班级', '学期', '范围', '姓名', '学号', '性别', '分数',
                     '缺勤', '迟到', '请假', '班级活动', '院级活动', '校级活动', '轻度违纪', '中度违纪', '严重违纪'])
    safe = lambda value: ("'" + str(value)) if str(value).startswith(('=', '+', '-', '@', '\t', '\r')) else str(value)
    for report in reports:
        for table in report['tables']:
            for row in table['rows']:
                writer.writerow([safe(x) for x in [report['class_name'], (report.get('term') or {}).get('label', ''),
                    table['label'], row['name'], row['number'], row.get('sex') or '', row['score'],
                    *[row['counts'].get(key, 0) for key in keys]]])
    write_private(destination / 'scores.csv', output.getvalue().encode('utf-8-sig'))
    sums = ''.join(digest(p) + '  ' + p.name + '\n' for p in sorted(destination.iterdir()))
    write_private(destination / 'SHA256SUMS', sums.encode())
    return manifest


def verify_readable(directory):
    directory = Path(directory)
    entries = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ', 1)
        if not re.fullmatch('[a-zA-Z0-9_.-]+', name) or name in entries:
            raise RuntimeError('Invalid archive file list')
        entries[name] = checksum
    if set(entries) != {'business.json', 'scores.json', 'scores.csv'}:
        raise RuntimeError('Incomplete business archive')
    if {p.name for p in directory.iterdir()} != set(entries) | {'SHA256SUMS'}:
        raise RuntimeError('Unexpected business archive files')
    for name, expected in entries.items():
        if (directory / name).is_symlink() or digest(directory / name) != expected:
            raise RuntimeError('Business archive checksum mismatch')


def rsync(config, arguments):
    env = os.environ.copy()
    env['RSYNC_PASSWORD'] = Path(config['rsync_password_file']).read_text().strip()
    return run(['rsync', '--timeout=60', *arguments], env, timeout=1800)


def capacity(config, staging):
    marker = staging / '.capacity'
    rsync(config, [config['rsync_remote'] + '.capacity', marker])
    values = dict(line.split('=', 1) for line in marker.read_text().splitlines())
    if int(values['available_percent']) < 20:
        raise RuntimeError('NAS capacity is below 20 percent')
    stamp = datetime.fromisoformat(values['updated_at'])
    if stamp.tzinfo is None:
        raise RuntimeError('NAS capacity marker lacks a timezone')
    age = (now() - stamp).total_seconds()
    # Independent NAS/VPS clocks can straddle a second boundary. Reject large
    # drift while allowing a bounded future timestamp, not arbitrary freshness.
    if not -30 <= age <= 180:
        raise RuntimeError('NAS capacity marker is stale')
    marker.unlink()


def readable_backup(config, bundle, staging):
    target = staging / 'readable'
    export_readable(bundle, target)
    verify_readable(target)
    stamp = now().strftime('%Y%m%dT%H%M%S%z')
    remote = config['rsync_remote']
    rsync(config, ['--archive', '--delete', '--delay-updates', '--exclude=.ready',
                  '--exclude=.finalized', '--exclude=.capacity', str(target) + '/', remote])
    ready = staging / '.ready'
    checksum = digest(target / 'SHA256SUMS')
    write_private(ready, f'snapshot={stamp}\nsha256sums={checksum}\n'.encode())
    rsync(config, ['--archive', ready, remote + '.ready'])
    deadline = time.monotonic() + 300
    marker = staging / '.finalized'
    while time.monotonic() < deadline:
        try:
            rsync(config, [remote + '.finalized', marker])
            values = dict(line.split('=', 1) for line in marker.read_text().splitlines())
            if values.get('snapshot') == stamp and values.get('sha256sums') == checksum:
                restored = staging / 'readable-restored'
                restored.mkdir(mode=0o700)
                rsync(config, ['--archive', config['rsync_history'] + stamp + '/', str(restored) + '/'])
                verify_readable(restored)
                return stamp
        except RuntimeError:
            pass
        time.sleep(5)
    raise RuntimeError('NAS finalization or round-trip verification timed out')


def restic_env(config):
    env = os.environ.copy()
    env.update(RESTIC_REPOSITORY=config['restic_repository'],
               RESTIC_PASSWORD_FILE=config['restic_password_file'],
               RESTIC_REST_USERNAME='duxing',
               RESTIC_REST_PASSWORD=Path(config['rest_password_file']).read_text().strip(),
               RESTIC_CACHE_DIR=config['state_root'] + '/restic-cache')
    return env


def system_backup(config, bundle, staging):
    target = staging / 'system'
    target.mkdir(mode=0o700)
    manifest = verify_bundle(bundle)
    shutil.copytree(bundle, target / 'bundle')
    artifact = Path(config['artifact_root']) / (manifest['release'] + '.tar.gz')
    if not artifact.is_file():
        raise RuntimeError('Matching code artifact is missing')
    shutil.copyfile(artifact, target / 'code.tar.gz')
    shutil.copytree(config['environment_root'], target / 'configuration')
    # Restore the environment paired with the database, not a later edit.
    shutil.copyfile(bundle / 'ams.env', target / 'configuration/ams.env')
    units = target / 'units'; units.mkdir(mode=0o700)
    for source in Path('/etc/systemd/system').glob('duxing-*'):
        if source.is_file(): shutil.copyfile(source, units / source.name)
    nginx = Path('/etc/nginx/sites-available/ams.unzip.work')
    if nginx.is_file(): shutil.copyfile(nginx, target / 'nginx.conf')
    for path in target.rglob('*'):
        if path.is_file(): path.chmod(0o600)
    env = restic_env(config)
    events = run(['restic', 'backup', '--json', '--host', 'unzip-work-origin', '--tag', 'duxing-system', target], env)
    summaries = [json.loads(line) for line in events.splitlines() if line.startswith('{')]
    summary = next(item for item in summaries if item.get('message_type') == 'summary')
    snapshot = summary['snapshot_id']
    # A completed upload alone is insufficient: restore its exact snapshot.
    restored = staging / 'system-restored'
    run(['restic', 'restore', snapshot, '--target', restored], env)
    manifests = list(restored.rglob('bundle/manifest.json'))
    if len(manifests) != 1: raise RuntimeError('Restored system bundle is ambiguous')
    recovered = manifests[0].parent
    verify_bundle(recovered)
    for name in ['managedb.sqlite3', 'ams.env', 'manifest.json']:
        if digest(bundle / name) != digest(recovered / name):
            raise RuntimeError('Restic round-trip differs from the source bundle')
    if digest(artifact) != digest(recovered.parent / 'code.tar.gz'):
        raise RuntimeError('Restored code artifact differs')
    return snapshot


def notify(config, status):
    smtp = config.get('smtp')
    if not smtp: return
    message = EmailMessage()
    message['Subject'] = '[笃行备份' + status + '] NAS 异地备份'
    message['From'], message['To'] = smtp['sender'], smtp['recipient']
    message.set_content(f'项目：笃行\n状态：{status}\n时间：{now().isoformat()}\n邮件不包含学生数据、路径或凭据。')
    with smtplib.SMTP_SSL(smtp['host'], int(smtp['port']), timeout=20) as client:
        client.login(smtp['username'], Path(smtp['password_file']).read_text().strip())
        client.send_message(message)


def execute(config, mode):
    state = Path(config['state_root']); state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_mode & 0o077: raise RuntimeError('State directory must be private')
    path = state / 'offsite.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    with (state / 'nas.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            bundles = []
            legacy = 0
            for p in sorted(Path(config['backup_root']).iterdir()):
                if not p.is_dir() or not (p / 'manifest.json').is_file(): continue
                meta = json.loads((p / 'manifest.json').read_text())
                if meta.get('format_version') != 2: legacy += 1; continue
                bundles.append((p, verify_bundle(p)))
            if not bundles: raise RuntimeError('No verified local backup exists')
            bundle, meta = max(bundles, key=lambda item: item[1]['created_at_beijing'])
            age = (now() - datetime.fromisoformat(meta['created_at_beijing'])).total_seconds()
            if not 0 <= age <= 48 * 3600: raise RuntimeError('Local backup is overdue')
            with tempfile.TemporaryDirectory(prefix='nas-', dir=state) as temp:
                staging = Path(temp)
                capacity(config, staging)
                if mode == 'check':
                    if not previous.get('ok') or previous.get('latest_backup') != bundle.name:
                        raise RuntimeError('NAS recovery point is missing or overdue')
                    run(['restic', 'check', '--read-data-subset=1/20'], restic_env(config))
                    target = staging / 'history'; target.mkdir(mode=0o700)
                    rsync(config, ['--archive', config['rsync_history'] + previous['readable_snapshot'] + '/', str(target) + '/'])
                    verify_readable(target)
                    write_status(state / 'monthly-check.json', {'checked_at': now().isoformat(), 'ok': True, 'delete_performed': False})
                    return previous
                if previous.get('ok') and previous.get('latest_backup') == bundle.name:
                    return previous
                readable = readable_backup(config, bundle, staging)
                system = system_backup(config, bundle, staging)
            result = {'ok': True, 'last_attempt_at': now().isoformat(), 'last_success_at': now().isoformat(),
                      'latest_backup': bundle.name, 'latest_backup_created_at': meta['created_at_beijing'],
                      'readable_snapshot': readable, 'system_snapshot': system, 'legacy_pending': legacy}
            write_status(path, result)
            if previous.get('ok') is False:
                try: notify(config, '恢复')
                except Exception: write_status(state / 'notification-failure.json', {'status': 'recovery_mail_failed'})
            return result
        except Exception:
            write_status(path, {**previous, 'ok': False, 'last_attempt_at': now().isoformat(), 'error': 'nas_backup_failed'})
            try: notify(config, '失败或超期')
            except Exception: write_status(state / 'notification-failure.json', {'status': 'failure_mail_failed'})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--mode', choices=['primary', 'retry', 'check'], default='primary')
    args = parser.parse_args()
    if args.config.is_symlink() or args.config.stat().st_mode & 0o077:
        parser.error('Private configuration (0600) required')
    result = execute(json.loads(args.config.read_text()), args.mode)
    print(json.dumps({'ok': result['ok'], 'backup': result['latest_backup'], 'legacy_pending': result['legacy_pending']}))


if __name__ == '__main__':
    main()
