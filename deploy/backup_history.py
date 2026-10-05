"""Preserve every completed paired backup on NAS before seven-day local cleanup.

NAS history is append-only. Cleanup only removes verified local bundle files;
it never runs restic forget/prune or deletes a NAS archive.
"""
import argparse
import fcntl
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .offsite_backup import FILES, digest, verify_bundle, write_status
except ImportError:
    from offsite_backup import FILES, digest, verify_bundle, write_status

NAME = re.compile(r'^[0-9]{8}T[0-9]{6}\+0800-[0-9a-f]{8}$')
RELEASE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$')
KEEP_DAYS = 7


def now():
    return datetime.now(ZoneInfo('Asia/Shanghai'))


def private_directory(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
        raise RuntimeError('Backup and state directories must be private real directories.')
    return path


def inspect_bundle(path):
    path = private_directory(path)
    if not NAME.fullmatch(path.name) or {x.name for x in path.iterdir()} != set(FILES):
        raise RuntimeError('Unexpected backup directory name or contents.')
    for name in FILES:
        p = path / name
        if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077:
            raise RuntimeError('Backup files must be private regular files.')
    meta = json.loads((path / 'manifest.json').read_text())
    stamp = datetime.fromisoformat(meta['created_at_beijing'])
    if stamp.tzinfo is None:
        raise RuntimeError('Backup creation time requires a timezone.')
    hashes = {name: digest(path / name) for name in FILES}
    if hashes['managedb.sqlite3'] != meta.get('database_sha256') or hashes['ams.env'] != meta.get('environment_sha256'):
        raise RuntimeError('Backup checksums differ from manifest.')
    version = meta.get('format_version', 1)
    if version == 2:
        verify_bundle(path)
    elif version == 1:
        with closing(sqlite3.connect((path / 'managedb.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Legacy backup SQLite validation failed.')
    else:
        raise RuntimeError('Unsupported backup format.')
    return {'hashes': hashes, 'created_at': stamp.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(), 'format_version': version,
            'release': meta.get('release') if version == 2 else None}


def scan(config):
    root = private_directory(config['backup_root'])
    bundles, skipped = {}, []
    for path in sorted(root.iterdir()):
        if not NAME.fullmatch(path.name):
            continue
        if path.is_symlink() or not path.is_dir() or not (path / 'manifest.json').is_file():
            skipped.append(path.name)
            continue
        try:
            bundles[path.name] = inspect_bundle(path)
        except (ValueError, KeyError, RuntimeError, OSError):
            skipped.append(path.name)
    return bundles, skipped


def ledger(config):
    state = private_directory(config['state_root'])
    path = state / 'backup-history.json'
    if not path.exists():
        return {'version': 1, 'bundles': {}}
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError('NAS history ledger must be a private regular file.')
    values = json.loads(path.read_text())
    if values.get('version') != 1 or not isinstance(values.get('bundles'), dict):
        raise RuntimeError('Unsupported NAS history ledger.')
    return values


def restic(config, arguments):
    try:
        from . import nas_backup
    except ImportError:
        import nas_backup
    return nas_backup.run(['restic', *arguments], nas_backup.restic_env(config))


def recovered_bundle(restored, name):
    matches = list(Path(restored).rglob('stored-bundles/' + name))
    if len(matches) != 1:
        raise RuntimeError('Restored history bundle is missing or ambiguous.')
    return matches[0]


def verify_receipts(config, receipts, destination):
    grouped = {}
    for name, receipt in receipts.items():
        if not NAME.fullmatch(name) or not re.fullmatch(r'[0-9a-f]{64}', receipt.get('snapshot', '')):
            raise RuntimeError('Invalid NAS history receipt.')
        grouped.setdefault(receipt['snapshot'], {})[name] = receipt
    for snapshot, entries in grouped.items():
        restored = Path(destination) / snapshot
        restic(config, ['restore', snapshot, '--target', str(restored)])
        for name, receipt in entries.items():
            if inspect_bundle(recovered_bundle(restored, name)) != receipt['identity']:
                raise RuntimeError('NAS restored history does not match its receipt.')
            release = receipt['identity']['release']
            if release:
                if not RELEASE.fullmatch(release):
                    raise RuntimeError('Invalid archived code identity.')
                codes = list(restored.rglob('codes/' + release + '.tar.gz'))
                if len(codes) != 1 or codes[0].is_symlink() or digest(codes[0]) != receipt.get('code_sha256'):
                    raise RuntimeError('Archived matching code is missing or corrupt.')


def preserve(config, staging):
    bundles, skipped = scan(config)
    if skipped:
        raise RuntimeError('Unfinished or invalid backups remain; preserve them locally and inspect.')
    values = ledger(config)
    pending = {}
    for name, identity in bundles.items():
        existing = values['bundles'].get(name)
        if existing and existing.get('identity') != identity:
            raise RuntimeError('Existing backup identity changed; refusing to overwrite NAS history.')
        if not existing:
            pending[name] = identity
    if not pending:
        return values
    target = Path(staging) / 'local-history'
    store = target / 'stored-bundles'; store.mkdir(mode=0o700, parents=True)
    codes = target / 'codes'; codes.mkdir(mode=0o700)
    code_hashes = {}
    root = Path(config['backup_root'])
    for name, identity in pending.items():
        copied = store / name; copied.mkdir(mode=0o700)
        for filename in FILES:
            shutil.copyfile(root / name / filename, copied / filename)
            (copied / filename).chmod(0o600)
        if inspect_bundle(copied) != identity:
            raise RuntimeError('Local history changed while staging.')
        release = identity['release']
        if release and release not in code_hashes:
            if not RELEASE.fullmatch(release):
                raise RuntimeError('Invalid code identity.')
            artifact = Path(config['artifact_root']) / (release + '.tar.gz')
            if artifact.is_symlink() or not artifact.is_file():
                raise RuntimeError('Matching history code artifact is missing.')
            shutil.copyfile(artifact, codes / artifact.name)
            (codes / artifact.name).chmod(0o600)
            code_hashes[release] = digest(artifact)
    write_status(target / 'inventory.json', {'bundles': pending, 'codes': code_hashes})
    events = restic(config, ['backup', '--json', '--host', 'unzip-work-origin', '--tag', 'duxing-local-history', str(target)])
    result = next(json.loads(line) for line in events.splitlines()
                  if line.startswith('{') and json.loads(line).get('message_type') == 'summary')
    snapshot = result['snapshot_id']
    receipts = {name: {'identity': identity, 'snapshot': snapshot, 'verified_at': now().isoformat(),
                       'code_sha256': code_hashes.get(identity['release']),
                       'legacy_code_unknown': identity['format_version'] == 1}
                for name, identity in pending.items()}
    verify_receipts(config, receipts, Path(staging) / 'history-restored')
    values['bundles'].update(receipts)
    values['last_verified_at'] = now().isoformat()
    write_status(Path(config['state_root']) / 'backup-history.json', values)
    return values


def cleanup(config, staging=None, apply=False, as_of=None):
    if config.get('local_retention_days') != KEEP_DAYS:
        raise RuntimeError('Only the approved seven-day local retention policy is enabled.')
    instant = as_of or now()
    if instant.tzinfo is None:
        raise RuntimeError('Retention clock requires a timezone.')
    cutoff = instant - timedelta(days=KEEP_DAYS)
    bundles, skipped = scan(config)
    values = ledger(config)['bundles']
    latest = max(bundles, key=lambda n: bundles[n]['created_at']) if bundles else None
    candidates, retained = {}, []
    for name, identity in bundles.items():
        if name == latest or datetime.fromisoformat(identity['created_at']) >= cutoff:
            retained.append(name)
        elif values.get(name, {}).get('identity') == identity:
            candidates[name] = values[name]
        else:
            skipped.append(name)
    result = {'checked_at': instant.isoformat(), 'keep_days': KEEP_DAYS, 'cutoff': cutoff.isoformat(),
              'candidates': sorted(candidates), 'retained': sorted(retained),
              'blocked': sorted(skipped), 'deleted': [], 'preview': not apply}
    if not apply:
        return result
    if candidates:
        if staging is None:
            raise RuntimeError('An isolated NAS restore destination is required before cleanup.')
        verify_receipts(config, candidates, Path(staging) / 'cleanup-restored')
        # Validate the entire batch again before deleting any local file.
        root = private_directory(config['backup_root'])
        for name, receipt in candidates.items():
            if inspect_bundle(root / name) != receipt['identity']:
                raise RuntimeError('Cleanup candidate changed after NAS verification.')
        for name in sorted(candidates):
            source = root / name
            if inspect_bundle(source) != candidates[name]['identity']:
                raise RuntimeError('Cleanup candidate changed.')
            # Shallow deletion only; never recursively follow an unexpected tree.
            tomb = root / ('.cleanup-' + name + '-' + secrets.token_hex(4))
            source.rename(tomb)
            for filename in FILES:
                (tomb / filename).unlink()
            tomb.rmdir()
            result['deleted'].append(name)
            write_status(Path(config['state_root']) / 'local-cleanup.json', result)
    write_status(Path(config['state_root']) / 'local-cleanup.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Default is a read-only preview.')
    args = parser.parse_args()
    if args.config.is_symlink() or not args.config.is_file() or args.config.stat().st_mode & 0o077:
        parser.error('Private configuration (0600) required.')
    config = json.loads(args.config.read_text())
    state = private_directory(config['state_root'])
    with (state / 'nas.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='cleanup-', dir=state) as temp:
            result = cleanup(config, Path(temp), apply=args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
