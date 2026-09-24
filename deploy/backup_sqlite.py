"""Take a private, consistent SQLite backup with its matching runtime environment.

Each run creates a new directory and never replaces or removes an older backup.
The manifest is written last, so an interrupted backup cannot look complete.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DATABASE = Path('/var/lib/duxing/managedb.sqlite3')
ENVIRONMENT = Path('/etc/duxing/ams.env')
ROOT = Path('/var/backups/duxing')


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def write_private(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Backup must run as root to pair the database with its private environment.')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.stat().st_mode & 0o077:
        raise RuntimeError('Backup root is accessible to non-root users.')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S%z')
    target_dir = ROOT / f'{stamp}-{secrets.token_hex(4)}'
    target_dir.mkdir(mode=0o700)
    database = target_dir / 'managedb.sqlite3'
    environment = ENVIRONMENT.read_bytes()
    source = sqlite3.connect(DATABASE.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    target = sqlite3.connect(database)
    try:
        source.backup(target, pages=256, sleep=0.1)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backed-up SQLite integrity check failed.')
        if target.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Backed-up SQLite foreign key check failed.')
    finally:
        target.close()
        source.close()
    os.chmod(database, 0o600)
    with database.open('rb') as complete:
        os.fsync(complete.fileno())
    if environment != ENVIRONMENT.read_bytes():
        raise RuntimeError('Runtime environment changed during backup; refusing a mismatched pair.')
    write_private(target_dir / 'ams.env', environment)
    manifest = {'created_at_beijing': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                'database_sha256': digest(database), 'environment_sha256': digest(target_dir / 'ams.env')}
    write_private(target_dir / 'manifest.json', (json.dumps(manifest, sort_keys=True) + '\n').encode())
    descriptor = os.open(target_dir, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(f'Private Duxing backup complete: {target_dir.name}')


if __name__ == '__main__':
    main()
