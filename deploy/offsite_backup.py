"""Copy completed paired backups to a private, configurable offsite destination.

No prune/delete operation is provided. Publication uses a same-directory rename
only after a downloaded copy passes hashes and SQLite integrity checks.
"""
import argparse
import fcntl
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .backup_sqlite import digest, write_private
except ImportError:
    from backup_sqlite import digest, write_private

FILES = ('managedb.sqlite3', 'ams.env', 'manifest.json')
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,100}$')


def verify_bundle(directory):
    directory = Path(directory)
    for name in FILES:
        file = directory / name
        if not file.is_file() or file.is_symlink():
            raise RuntimeError('Backup bundle is incomplete or contains symbolic links.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not manifest.get('release') or manifest.get('format_version') != 2:
        raise RuntimeError('Backup must identify its matching application release (format 2).')
    stamp = datetime.fromisoformat(manifest['created_at_beijing'])
    if stamp.tzinfo is None:
        raise RuntimeError('Backup creation time must include a timezone.')
    for name, key in [('managedb.sqlite3', 'database_sha256'), ('ams.env', 'environment_sha256')]:
        if digest(directory / name) != manifest.get(key):
            raise RuntimeError('Backup bundle checksum mismatch.')
    with sqlite3.connect((directory / FILES[0]).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Backup SQLite validation failed.')
        ledger = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='django_migrations'").fetchone()
        migrations = [list(row) for row in connection.execute('SELECT app,name FROM django_migrations ORDER BY app,name')] if ledger else []
        if migrations != manifest.get('migration_ledger'):
            raise RuntimeError('Backup migration ledger differs from its manifest.')
    return manifest


class DirectoryStore:
    """Mounted private NAS destination; also usable for isolated rehearsal."""
    def __init__(self, config):
        self.root = Path(config['destination'])
        if not self.root.is_dir() or self.root.is_symlink() or self.root.stat().st_mode & 0o077:
            raise RuntimeError('Destination must be an existing private directory (0700).')
        mount = Path(config['mount_point'])
        if mount.resolve() == Path('/') or not os.path.ismount(mount):
            raise RuntimeError('Configured NAS mount is not mounted; refusing a local fallback copy.')
        self.root.resolve().relative_to(mount.resolve())

    def fetch(self, name, target):
        origin = self.root / name
        if not origin.exists():
            return False
        if origin.is_symlink() or not origin.is_dir():
            raise RuntimeError('Destination bundle is not a directory.')
        for file in FILES:
            source = origin / file
            if source.is_symlink():
                raise RuntimeError('Destination contains symbolic links.')
            shutil.copyfile(source, target / file)
        return True

    def stage(self, name, source):
        target = self.root / name
        target.mkdir(mode=0o700)
        for file in FILES:
            write_private(target / file, (source / file).read_bytes())
        descriptor = os.open(target, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

    def publish(self, stage, name):
        if (self.root / name).exists():
            raise RuntimeError('Destination already exists; existing backups are never overwritten.')
        (self.root / stage).rename(self.root / name)
        descriptor = os.open(self.root, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


def sftp_quote(value):
    value = str(value)
    if any(ord(c) < 32 for c in value) or value.startswith('-'):
        raise ValueError('Invalid SFTP path.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class SFTPStore:
    """OpenSSH batch transport: strict host keys, no shell or remote script."""
    def __init__(self, config):
        host, username = config['host'], config['username']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', username):
            raise ValueError('Invalid SSH host or username.')
        self.destination = config['destination'].rstrip('/')
        if not self.destination.startswith('/') or '..' in self.destination.split('/'):
            raise ValueError('SFTP destination must be an absolute path without parent traversal.')
        sftp_quote(self.destination)
        self.timeout = int(config.get('timeout_seconds', 180))
        self.command = ['sftp', '-q', '-f', '-b', '-', '-P', str(int(config.get('port', 22))),
                        '-i', str(config['identity_file']), '-oBatchMode=yes', '-oIdentitiesOnly=yes',
                        '-oStrictHostKeyChecking=yes', '-oConnectTimeout=15',
                        '-oUserKnownHostsFile=' + str(config['known_hosts']), username + '@' + host]

    def run(self, commands):
        result = subprocess.run(self.command, input='\n'.join(commands) + '\n', text=True,
                                capture_output=True, timeout=self.timeout)
        if result.returncode:
            # Do not leak private server paths, usernames or environment contents.
            raise RuntimeError('SFTP operation failed; check private transport configuration and reachability.')

    def path(self, name, file=None):
        return sftp_quote(self.destination + '/' + name + (('/' + file) if file else ''))

    def fetch(self, name, target):
        # A suppressed missing manifest is distinguishable from a failed session.
        self.run(['-get ' + self.path(name, 'manifest.json') + ' ' + sftp_quote(target / 'manifest.json')])
        if not (target / 'manifest.json').exists():
            return False
        self.run(['get ' + self.path(name, file) + ' ' + sftp_quote(target / file) for file in FILES[:-1]])
        return True

    def stage(self, name, source):
        commands = ['mkdir ' + self.path(name), 'chmod 700 ' + self.path(name)]
        for file in FILES:
            commands += ['put ' + sftp_quote(source / file) + ' ' + self.path(name, file),
                         'chmod 600 ' + self.path(name, file)]
        self.run(commands)

    def publish(self, stage, name):
        self.run(['rename ' + self.path(stage) + ' ' + self.path(name)])


def copy_bundle(source, store):
    source = Path(source)
    if not NAME.fullmatch(source.name) or source.is_symlink():
        raise ValueError('Invalid backup directory name.')
    verify_bundle(source)
    with tempfile.TemporaryDirectory(prefix='duxing-restore-check-') as temporary:
        restored = Path(temporary)
        if store.fetch(source.name, restored):
            verify_bundle(restored)
            if any(digest(source / file) != digest(restored / file) for file in FILES):
                raise RuntimeError('Destination name is already occupied by a different backup.')
            return False
        stage = '.partial-' + source.name + '-' + secrets.token_hex(4)
        store.stage(stage, source)
        if not store.fetch(stage, restored):
            raise RuntimeError('Uploaded backup could not be downloaded for verification.')
        verify_bundle(restored)
        if any(digest(source / file) != digest(restored / file) for file in FILES):
            raise RuntimeError('Offsite round-trip checksum mismatch.')
        store.publish(stage, source.name)
        return True


def write_status(path, values):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(4))
    write_private(temporary, (json.dumps(values, ensure_ascii=False, sort_keys=True) + '\n').encode())
    os.replace(temporary, path)


def run(config):
    root = Path(config['backup_root'])
    status_file = Path(config['status_file'])
    status_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if status_file.parent.stat().st_mode & 0o077:
        raise RuntimeError('Status directory must be private (0700).')
    lock_fd = os.open(status_file.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An offsite copy is already running.') from None
        previous = json.loads(status_file.read_text()) if status_file.exists() else {}
        now = lambda: datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        try:
            if not root.is_dir() or root.is_symlink() or root.stat().st_mode & 0o077:
                raise RuntimeError('Local backup root must be private (0700).')
            config_transport = config['transport']
            kind = config_transport['type']
            if kind not in ('directory', 'sftp'):
                raise ValueError('Unsupported backup transport.')
            store = DirectoryStore(config_transport) if kind == 'directory' else SFTPStore(config_transport)
            backups = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.') and (p / 'manifest.json').exists())
            if not backups:
                raise RuntimeError('No completed local backup exists.')
            copied = verified = legacy_pending = 0
            verified_bundles = []
            for backup in backups:
                metadata = json.loads((backup / 'manifest.json').read_text())
                if metadata.get('format_version') != 2 or not metadata.get('release'):
                    # Old production backups did not record matching code. Keep
                    # them intact and report the evidence gap; do not invent it.
                    legacy_pending += 1
                    continue
                copied += bool(copy_bundle(backup, store))
                verified += 1
                verified_bundles.append((backup, metadata))
            if not verified:
                raise RuntimeError('No backup with a verified release identity is available.')
            latest, metadata = max(verified_bundles, key=lambda item: datetime.fromisoformat(item[1]['created_at_beijing']))
            status = {'last_success_at': now(), 'last_attempt_at': now(), 'latest_backup': latest.name,
                      'latest_backup_created_at': metadata['created_at_beijing'],
                      'latest_local_backup': backups[-1].name,
                      'verified_backups': verified, 'new_copies': copied,
                      'legacy_pending': legacy_pending, 'ok': True}
            write_status(status_file, status)
            return status
        except Exception:
            write_status(status_file, {**previous, 'last_attempt_at': now(), 'ok': False,
                                       'error': 'offsite_copy_failed'})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    if args.config.is_symlink() or args.config.stat().st_mode & 0o077:
        parser.error('Configuration must be a private regular file (0600).')
    result = run(json.loads(args.config.read_text()))
    print('Offsite backup verified: {} bundles, {} new copies; {} legacy bundles need release evidence.'.format(
        result['verified_backups'], result['new_copies'], result['legacy_pending']))


if __name__ == '__main__':
    main()
