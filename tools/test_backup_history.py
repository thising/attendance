"""Test destructive retention boundaries and verified NAS-history catch-up."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from deploy import backup_history as history, backup_sqlite, nas_backup


class BackupHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {key: str(self.root / directory) for key, directory in [
            ('backup_root', 'backups'), ('state_root', 'state'), ('artifact_root', 'artifacts')]}
        self.config['local_retention_days'] = 7
        for key in ['backup_root', 'state_root', 'artifact_root']:
            Path(self.config[key]).mkdir(mode=0o700)
        self.clock = datetime(2026, 10, 5, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        (Path(self.config['artifact_root']) / 'test.tar.gz').write_bytes(b'synthetic-code')
        self.remote = self.root / 'remote'; self.remote.mkdir(mode=0o700)
        self.sequence = 0

    def bundle(self, days, legacy=False, seconds=0):
        stamp = self.clock - timedelta(days=days, seconds=seconds)
        name = stamp.strftime('%Y%m%dT%H%M%S%z') + '-' + f'{days:08x}'
        path = Path(self.config['backup_root']) / name; path.mkdir(mode=0o700)
        database = path / 'managedb.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE facts(id INTEGER PRIMARY KEY, value TEXT)')
            db.execute('INSERT INTO facts VALUES(1,?)', (str(days),))
        (path / 'ams.env').write_text('synthetic-secret')
        meta = {'created_at_beijing': stamp.isoformat(), 'database_sha256': backup_sqlite.digest(database),
                'environment_sha256': backup_sqlite.digest(path / 'ams.env')}
        if not legacy: meta.update(format_version=2, release='test', migration_ledger=[])
        (path / 'manifest.json').write_text(json.dumps(meta))
        for p in path.iterdir(): p.chmod(0o600)
        return path

    def restic(self, config, arguments):
        if arguments[0] == 'backup':
            self.sequence += 1; snapshot = f'{self.sequence:064x}'
            shutil.copytree(arguments[-1], self.remote / snapshot)
            return json.dumps({'message_type': 'summary', 'snapshot_id': snapshot})
        if arguments[0] == 'restore':
            shutil.copytree(self.remote / arguments[1], Path(arguments[-1]) / 'archive')
            return ''
        self.fail('Unexpected remote command: ' + arguments[0])

    def preserve(self):
        with tempfile.TemporaryDirectory(dir=self.root) as temp, patch.object(history, 'restic', side_effect=self.restic):
            return history.preserve(self.config, Path(temp))

    def cleanup(self, apply=False):
        with tempfile.TemporaryDirectory(dir=self.root) as temp, patch.object(history, 'restic', side_effect=self.restic):
            return history.cleanup(self.config, Path(temp), apply=apply, as_of=self.clock)

    def test_all_pending_bundles_are_preserved_with_legacy_metadata_unchanged(self):
        old = self.bundle(10, legacy=True); current = self.bundle(0)
        manifest = (old / 'manifest.json').read_bytes()
        values = self.preserve()
        self.assertEqual(set(values['bundles']), {old.name, current.name})
        self.assertTrue(values['bundles'][old.name]['legacy_code_unknown'])
        self.assertEqual((old / 'manifest.json').read_bytes(), manifest)
        self.assertIn('code_sha256', values['bundles'][current.name])

    def test_repeat_does_not_duplicate_history_and_later_bundle_is_caught_up(self):
        a = self.bundle(8); self.preserve(); self.preserve()
        self.assertEqual(self.sequence, 1)
        b = self.bundle(0); values = self.preserve()
        self.assertEqual(self.sequence, 2); self.assertEqual(set(values['bundles']), {a.name, b.name})

    def test_seven_day_boundary_preview_and_applied_cleanup_keep_latest(self):
        old = self.bundle(7, seconds=1); boundary = self.bundle(7); recent = self.bundle(0)
        self.preserve(); plan = self.cleanup()
        self.assertEqual(plan['candidates'], [old.name]); self.assertTrue(old.exists())
        result = self.cleanup(apply=True)
        self.assertEqual(result['deleted'], [old.name])
        self.assertFalse(old.exists()); self.assertTrue(boundary.exists()); self.assertTrue(recent.exists())
        self.assertEqual(len(history.ledger(self.config)['bundles']), 3)
        self.assertTrue(self.remote.exists())

    def test_no_receipt_never_deletes_old_backups(self):
        old = self.bundle(10); self.bundle(0)
        result = self.cleanup(apply=True)
        self.assertIn(old.name, result['blocked']); self.assertTrue(old.exists())

    def test_only_last_recovery_point_is_kept_even_if_overdue(self):
        only = self.bundle(20); self.preserve(); result = self.cleanup(apply=True)
        self.assertEqual(result['deleted'], []); self.assertTrue(only.exists())

    def test_nas_outage_keeps_all_local_candidates(self):
        old = self.bundle(10); self.bundle(0); self.preserve()
        with patch.object(history, 'restic', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): history.cleanup(self.config, self.root / 'restore', True, self.clock)
        self.assertTrue(old.exists())

    def test_corrupt_remote_history_or_code_prevents_local_deletion(self):
        old = self.bundle(10); self.bundle(0); values = self.preserve()
        remote = self.remote / values['bundles'][old.name]['snapshot']
        (remote / 'codes/test.tar.gz').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'code'): self.cleanup(apply=True)
        self.assertTrue(old.exists())
        (remote / 'codes/test.tar.gz').write_bytes(b'synthetic-code')
        (remote / 'stored-bundles' / old.name / 'ams.env').write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'checksum'): self.cleanup(apply=True)
        self.assertTrue(old.exists())

    def test_changed_local_identity_and_unexpected_files_are_retained(self):
        old = self.bundle(10); self.bundle(0); self.preserve()
        (old / 'extra-private-file').write_text('keep')
        result = self.cleanup(apply=True)
        self.assertIn(old.name, result['blocked']); self.assertTrue(old.exists())
        (old / 'extra-private-file').unlink()
        (old / 'ams.env').write_text('changed')
        with self.assertRaises(RuntimeError): self.preserve()
        self.assertTrue(old.exists())

    def test_incomplete_and_symlink_directories_are_never_removed(self):
        self.bundle(0)
        partial = Path(self.config['backup_root']) / '20260901T042000+0800-12345678'
        partial.mkdir(mode=0o700); (partial / 'managedb.sqlite3').write_text('partial')
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'keep').write_text('protected')
        link = Path(self.config['backup_root']) / '20260902T042000+0800-12345678'
        link.symlink_to(outside, target_is_directory=True)
        result = self.cleanup(apply=True)
        self.assertIn(partial.name, result['blocked']); self.assertIn(link.name, result['blocked'])
        self.assertTrue(partial.exists()); self.assertTrue((outside / 'keep').exists())

    def test_mutation_during_remote_restore_prevents_any_deletion(self):
        old = self.bundle(10); self.bundle(0); self.preserve()
        def changed(config, args):
            result = self.restic(config, args)
            (old / 'ams.env').write_text('changed while restoring')
            return result
        with patch.object(history, 'restic', side_effect=changed):
            with self.assertRaises(RuntimeError): history.cleanup(self.config, self.root / 'restore', True, self.clock)
        self.assertTrue(old.exists())

    def test_wrong_policy_and_public_root_fail_closed(self):
        self.bundle(0)
        with self.assertRaises(RuntimeError): history.cleanup({**self.config, 'local_retention_days': 1})
        Path(self.config['backup_root']).chmod(0o755)
        with self.assertRaises(RuntimeError): self.cleanup()

    def test_roundtrip_failure_does_not_publish_history_receipt(self):
        old = self.bundle(10)
        def failed(config, args):
            if args[0] == 'restore': raise RuntimeError('partial')
            return self.restic(config, args)
        with patch.object(history, 'restic', side_effect=failed):
            with self.assertRaises(RuntimeError): history.preserve(self.config, self.root / 'stage')
        self.assertEqual(history.ledger(self.config)['bundles'], {}); self.assertTrue(old.exists())

    def test_automatic_cleanup_runs_only_after_both_latest_chains_succeed(self):
        self.bundle(0); calls = []
        with patch.object(nas_backup, 'now', return_value=self.clock), patch.object(nas_backup, 'capacity'), \
                patch.object(history, 'preserve', side_effect=lambda *args: calls.append('preserve')), \
                patch.object(history, 'cleanup', side_effect=lambda *args, **kwargs: calls.append('cleanup')), \
                patch.object(nas_backup, 'readable_backup', side_effect=lambda *args: calls.append('readable') or 'r'), \
                patch.object(nas_backup, 'system_backup', side_effect=lambda *args: calls.append('system') or 's'):
            nas_backup.execute(self.config, 'primary')
        self.assertEqual(calls, ['preserve', 'readable', 'system', 'cleanup'])

    def test_failed_latest_system_copy_never_starts_cleanup(self):
        old = self.bundle(10); self.bundle(0)
        with patch.object(nas_backup, 'now', return_value=self.clock), patch.object(nas_backup, 'capacity'), \
                patch.object(history, 'preserve'), patch.object(history, 'cleanup') as cleanup, \
                patch.object(nas_backup, 'readable_backup', return_value='r'), \
                patch.object(nas_backup, 'system_backup', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): nas_backup.execute(self.config, 'primary')
        cleanup.assert_not_called(); self.assertTrue(old.exists())

    def test_repeat_latest_still_preserves_pending_history_before_cleanup(self):
        latest = self.bundle(0)
        (Path(self.config['state_root']) / 'offsite.json').write_text(json.dumps({'ok': True, 'latest_backup': latest.name}))
        calls = []
        with patch.object(nas_backup, 'now', return_value=self.clock), patch.object(nas_backup, 'capacity'), \
                patch.object(history, 'preserve', side_effect=lambda *args: calls.append('preserve')), \
                patch.object(history, 'cleanup', side_effect=lambda *args, **kwargs: calls.append('cleanup')), \
                patch.object(nas_backup, 'system_backup') as system:
            nas_backup.execute(self.config, 'retry')
        self.assertEqual(calls, ['preserve', 'cleanup']); system.assert_not_called()


if __name__ == '__main__':
    unittest.main()
