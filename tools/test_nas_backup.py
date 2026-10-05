"""Backup confidentiality, recovery consistency and failure-state regression."""
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from deploy import backup_sqlite, nas_backup


class NASBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'source.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE manage_class(id INTEGER PRIMARY KEY,classname TEXT,managecode TEXT,sharecode TEXT)')
            db.execute("INSERT INTO manage_class VALUES(1,'合成班','must-not-export','private-old-link')")
            db.execute('CREATE TABLE manage_student(id INTEGER PRIMARY KEY,name TEXT,number TEXT)')
            db.execute("INSERT INTO manage_student VALUES(1,'=SUM(1)','001')")
            db.execute('CREATE TABLE auth_user(id INTEGER PRIMARY KEY,password TEXT)')
            db.execute("INSERT INTO auth_user VALUES(1,'must-not-export-auth')")
            db.execute('CREATE TABLE manage_currentclassreport(id INTEGER PRIMARY KEY,payload TEXT)')
            payload = {'class_name':'合成班','term':{'label':'2026 秋季'},'tables':[{'label':'学期汇总','rows':[
                {'name':'=SUM(1)','number':'001','sex':None,'score':'60.00','counts':{'dlow':2,'dmid':3,'dhigh':4}}]}]}
            db.execute('INSERT INTO manage_currentclassreport VALUES(1,?)',(json.dumps(payload),))
        self.environment = self.root / 'ams.env'; self.environment.write_text('DUXING_CREDENTIAL_KEY=private-key\n')
        self.release = self.root / 'candidate'; self.release.mkdir()
        self.backups = self.root / 'backups'
        with patch.multiple(backup_sqlite,DATABASE=self.database,ENVIRONMENT=self.environment,
                            ROOT=self.backups,RELEASE=self.release), patch.object(backup_sqlite.os,'geteuid',return_value=0):
            backup_sqlite.main()
        self.bundle = next(self.backups.iterdir())
        self.config = {'backup_root':str(self.backups),'state_root':str(self.root/'state'),
                       'rsync_remote':'rsync://synthetic/incoming/'}

    def test_readable_omits_authentication_and_keys_preserves_nine_types_and_protects_csv(self):
        target=self.root/'readable'; nas_backup.export_readable(self.bundle,target); nas_backup.verify_readable(target)
        text=''.join(p.read_text(encoding='utf-8-sig') for p in target.iterdir())
        for secret in ['must-not-export','private-old-link','private-key','auth_user']:
            self.assertNotIn(secret,text)
        row=list(csv.reader(io.StringIO((target/'scores.csv').read_text(encoding='utf-8-sig'))))[1]
        self.assertEqual(row[3],"'=SUM(1)"); self.assertEqual(row[-3:],['2','3','4'])

    def test_readable_rejects_corruption_and_unlisted_private_file(self):
        target=self.root/'readable'; nas_backup.export_readable(self.bundle,target)
        (target/'ams.env').write_text('private-key')
        with self.assertRaisesRegex(RuntimeError,'Unexpected'): nas_backup.verify_readable(target)
        (target/'ams.env').unlink(); (target/'business.json').write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError,'checksum'): nas_backup.verify_readable(target)

    def test_failure_preserves_last_recovery_point_and_notifies_without_success(self):
        state=Path(self.config['state_root']);state.mkdir(mode=0o700)
        (state/'offsite.json').write_text(json.dumps({'ok':True,'latest_backup':'older'}))
        with patch.object(nas_backup,'capacity'),patch.object(nas_backup,'readable_backup',side_effect=RuntimeError('simulated')),patch.object(nas_backup,'notify') as mail:
            with self.assertRaises(RuntimeError): nas_backup.execute(self.config,'primary')
        saved=json.loads((state/'offsite.json').read_text())
        self.assertFalse(saved['ok']);self.assertEqual(saved['latest_backup'],'older');mail.assert_called_once()

    def test_success_requires_both_chains_and_repeat_is_idempotent(self):
        with patch.object(nas_backup,'capacity'),patch.object(nas_backup,'readable_backup',return_value='readable') as readable,patch.object(nas_backup,'system_backup',return_value='encrypted') as system:
            result=nas_backup.execute(self.config,'primary'); repeat=nas_backup.execute(self.config,'retry')
        self.assertEqual(result,repeat);readable.assert_called_once();system.assert_called_once()
        self.assertEqual(result['latest_backup'],self.bundle.name)
        self.assertEqual(result['system_snapshot'],'encrypted')

    def test_old_local_backup_cannot_be_masked_by_recent_copy(self):
        meta=json.loads((self.bundle/'manifest.json').read_text())
        meta['created_at_beijing']=(nas_backup.now()-timedelta(days=3)).isoformat()
        (self.bundle/'manifest.json').write_text(json.dumps(meta))
        with patch.object(nas_backup,'notify'):
            with self.assertRaisesRegex(RuntimeError,'overdue'):nas_backup.execute(self.config,'retry')

    def test_capacity_is_fail_closed_for_low_or_stale_markers(self):
        for percent,stamp in [(19,nas_backup.now()),(60,nas_backup.now()-timedelta(minutes=10))]:
            def marker(config,args):
                Path(args[-1]).write_text(f'available_percent={percent}\nupdated_at={stamp.isoformat()}\n')
            with patch.object(nas_backup,'rsync',side_effect=marker):
                with self.assertRaises(RuntimeError):nas_backup.capacity(self.config,self.root)

    def test_notification_contains_only_project_status_and_time(self):
        password=self.root/'smtp';password.write_text('smtp-private')
        config={'smtp':{'sender':'sender@example.invalid','recipient':'backup@example.invalid',
                       'host':'smtp.example.invalid','port':465,'username':'user','password_file':str(password)}}
        client=MagicMock()
        with patch.object(nas_backup.smtplib,'SMTP_SSL') as factory:
            factory.return_value.__enter__.return_value=client;nas_backup.notify(config,'失败或超期')
        message=client.send_message.call_args.args[0].as_string()
        for secret in ['smtp-private','private-key','合成班',str(self.root)]:self.assertNotIn(secret,message)


if __name__=='__main__':unittest.main()
