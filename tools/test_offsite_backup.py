"""Restore real synthetic SQLite bundles and reject incomplete/offsite corruption."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import backup_sqlite, offsite_backup


class OffsiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'source.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO facts VALUES (1, 'synthetic-fact')")
        self.environment = self.root / 'private.env'
        self.environment.write_text('DUXING_CREDENTIAL_KEY=synthetic-only\n')
        self.release = self.root / 'candidate-code'
        self.release.mkdir()
        self.backups = self.root / 'local-backups'
        self.nas = self.root / 'nas-simulation'
        self.nas.mkdir(mode=0o700)
        mounted = patch.object(offsite_backup.os.path, 'ismount', side_effect=lambda path: Path(path) == self.nas)
        mounted.start()
        self.addCleanup(mounted.stop)
        self.config = {'backup_root': str(self.backups),
                       'status_file': str(self.root / 'status' / 'offsite.json'),
                       'transport': {'type': 'directory', 'destination': str(self.nas), 'mount_point': str(self.nas)}}
        with patch.multiple(backup_sqlite, DATABASE=self.database, ENVIRONMENT=self.environment,
                            ROOT=self.backups, RELEASE=self.release), patch.object(os, 'geteuid', return_value=0):
            backup_sqlite.main()
        self.bundle = next(self.backups.iterdir())

    def test_copy_retry_and_isolated_restore_preserve_facts_environment_and_release(self):
        first = offsite_backup.run(self.config)
        self.assertEqual(first['new_copies'], 1)
        self.assertEqual(offsite_backup.run(self.config)['new_copies'], 0)
        target = self.nas / self.bundle.name
        manifest = offsite_backup.verify_bundle(target)
        self.assertEqual(manifest['release'], 'candidate-code')
        self.assertEqual((target / 'ams.env').read_bytes(), self.environment.read_bytes())
        with sqlite3.connect(target / 'managedb.sqlite3') as db:
            self.assertEqual(db.execute('SELECT value FROM facts').fetchone()[0], 'synthetic-fact')
        self.assertEqual({p.name for p in self.nas.iterdir()}, {self.bundle.name})
        for p in [target, *(target / file for file in offsite_backup.FILES)]:
            self.assertEqual(p.stat().st_mode & 0o077, 0)

    def test_interrupted_upload_is_never_published_and_retry_recovers(self):
        original = offsite_backup.DirectoryStore.stage
        def interrupted(store, name, source):
            original(store, name, source)
            raise OSError('synthetic interruption')
        with patch.object(offsite_backup.DirectoryStore, 'stage', interrupted):
            with self.assertRaises(OSError): offsite_backup.run(self.config)
        self.assertFalse((self.nas / self.bundle.name).exists())
        status = json.loads(Path(self.config['status_file']).read_text())
        self.assertFalse(status['ok'])
        self.assertNotIn('last_success_at', status)
        self.assertTrue(offsite_backup.run(self.config)['ok'])
        self.assertTrue((self.nas / self.bundle.name).exists())

    def test_corrupted_round_trip_is_rejected_before_publication(self):
        original = offsite_backup.DirectoryStore.fetch
        def corrupt(store, name, target):
            result = original(store, name, target)
            if result: (target / 'ams.env').write_text('corrupted')
            return result
        with patch.object(offsite_backup.DirectoryStore, 'fetch', corrupt):
            with self.assertRaisesRegex(RuntimeError, 'checksum'): offsite_backup.run(self.config)
        self.assertFalse((self.nas / self.bundle.name).exists())

    def test_existing_destination_corruption_is_never_overwritten(self):
        offsite_backup.run(self.config)
        damaged = self.nas / self.bundle.name / 'ams.env'
        damaged.write_text('corrupted')
        with self.assertRaisesRegex(RuntimeError, 'checksum'): offsite_backup.run(self.config)
        self.assertEqual(damaged.read_text(), 'corrupted')
        self.assertFalse(json.loads(Path(self.config['status_file']).read_text())['ok'])

    def test_failed_nas_preserves_previous_success_and_returns_failure(self):
        before = offsite_backup.run(self.config)
        self.nas.rename(self.root / 'temporarily-offline')
        with self.assertRaises(RuntimeError): offsite_backup.run(self.config)
        status = json.loads(Path(self.config['status_file']).read_text())
        self.assertFalse(status['ok'])
        self.assertEqual(status['last_success_at'], before['last_success_at'])

    def test_legacy_bundle_is_reported_but_not_named_as_latest_verified(self):
        legacy = self.backups / '99999999-legacy'
        legacy.mkdir(mode=0o700)
        (legacy / 'manifest.json').write_text('{"database_sha256":"legacy"}')
        status = offsite_backup.run(self.config)
        self.assertEqual(status['legacy_pending'], 1)
        self.assertEqual(status['latest_local_backup'], legacy.name)
        self.assertEqual(status['latest_backup'], self.bundle.name)
        self.assertEqual(status['latest_backup_created_at'], offsite_backup.verify_bundle(self.bundle)['created_at_beijing'])
        self.assertFalse((self.nas / legacy.name).exists())

    def test_local_backup_root_failure_updates_previous_ok_status(self):
        offsite_backup.run(self.config)
        self.backups.chmod(0o755)
        with self.assertRaises(RuntimeError): offsite_backup.run(self.config)
        self.assertFalse(json.loads(Path(self.config['status_file']).read_text())['ok'])

    def test_unmounted_nas_directory_is_not_accepted_as_local_storage(self):
        with patch.object(offsite_backup.os.path, 'ismount', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not mounted'): offsite_backup.run(self.config)
        self.assertEqual(list(self.nas.iterdir()), [])

    def test_sftp_uses_strict_identity_and_no_shell(self):
        config = {'host': 'nas.example.test', 'username': 'backup', 'port': 65022,
                  'destination': '/private/duxing', 'identity_file': '/private/key',
                  'known_hosts': '/private/known_hosts'}
        store = offsite_backup.SFTPStore(config)
        with patch.object(offsite_backup.subprocess, 'run') as process:
            process.return_value.returncode = 0
            store.publish('.partial-copy', 'copy')
            args, kwargs = process.call_args
            self.assertIn('-oStrictHostKeyChecking=yes', args[0])
            self.assertIn('-oBatchMode=yes', args[0])
            self.assertNotIn('shell', kwargs)
            self.assertIn('rename', kwargs['input'])
        with self.assertRaises(ValueError): offsite_backup.sftp_quote('/path\nput secret')
        with self.assertRaises(ValueError): offsite_backup.SFTPStore({**config, 'host': '-oProxyCommand=bad'})

    def test_incomplete_local_backup_is_not_mistaken_for_completed(self):
        incomplete = self.backups / 'incomplete'
        incomplete.mkdir()
        (incomplete / 'managedb.sqlite3').write_text('incomplete')
        result = offsite_backup.run(self.config)
        self.assertEqual(result['verified_backups'], 1)
        self.assertFalse((self.nas / 'incomplete').exists())


if __name__ == '__main__':
    unittest.main()
