"""Verify private paired SQLite backups without touching production paths."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import backup_sqlite


class PairedBackupTests(unittest.TestCase):
    def test_two_runs_keep_independent_consistent_database_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / 'source.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
                connection.execute('INSERT INTO facts (value) VALUES (?)', ('original',))
            environment = root / 'private.env'
            environment.write_text('DUXING_CREDENTIAL_KEY=test-only\n')
            output = root / 'backups'
            with patch.object(backup_sqlite, 'DATABASE', database), \
                    patch.object(backup_sqlite, 'ENVIRONMENT', environment), \
                    patch.object(backup_sqlite, 'ROOT', output), \
                    patch.object(backup_sqlite.os, 'geteuid', return_value=0):
                backup_sqlite.main()
                with sqlite3.connect(database) as connection:
                    connection.execute('UPDATE facts SET value=?', ('updated',))
                backup_sqlite.main()
            copies = sorted(output.iterdir())
            self.assertEqual(len(copies), 2)
            self.assertEqual(output.stat().st_mode & 0o077, 0)
            values = set()
            for directory in copies:
                self.assertEqual(directory.stat().st_mode & 0o077, 0)
                manifest = json.loads((directory / 'manifest.json').read_text())
                self.assertEqual(manifest['database_sha256'], backup_sqlite.digest(directory / 'managedb.sqlite3'))
                self.assertEqual(manifest['environment_sha256'], backup_sqlite.digest(directory / 'ams.env'))
                self.assertEqual((directory / 'ams.env').read_text(), environment.read_text())
                self.assertEqual((directory / 'ams.env').stat().st_mode & 0o077, 0)
                with sqlite3.connect(directory / 'managedb.sqlite3') as connection:
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    values.add(connection.execute('SELECT value FROM facts').fetchone()[0])
            self.assertEqual(values, {'original', 'updated'})


if __name__ == '__main__':
    unittest.main()
