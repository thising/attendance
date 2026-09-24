"""Prefix namespaces and owner-only, on-demand encrypted credential retrieval."""
import json
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from manage.models import Class, CommitteeAccount, AuditEvent, Submission
from manage.services.committee import manage_accounts
from manage.services.errors import BusinessError
from manage.test_named_committee import AccountFixtures


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CommitteeCredentialTests(AccountFixtures,TestCase):
    def create(self,short='leader',classroom=None):
        classroom=classroom or self.c
        body=self.action(username=short,password=self.password,display_name='合成班委')
        response=self.owner_client.post(self.account_url(classroom),body,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        account=CommitteeAccount.objects.get(pk=response.json()['data']['receipt']['id'])
        return account,body,response.json()['data']

    def copy(self,account,client=None,classroom=None):
        return (client or self.owner_client).post(
            f'/classes/{(classroom or self.c).code}/committee/credentials/',
            {'id':account.pk},content_type='application/json')

    def test_short_name_is_namespaced_per_class_and_prefix_is_fixed(self):
        a,_,_=self.create('Leader')
        b,_,_=self.create('Leader',self.c2)
        self.assertEqual(a.username,self.c.committee_prefix+'.leader')
        self.assertNotEqual(a.username,b.username)
        self.assertRegex(a.username,r'^[a-f0-9]{4}\.leader$')
        self.c.classname='更名班级';self.c.save()
        self.assertTrue(a.username.startswith(self.c.committee_prefix+'.'))
        self.c.committee_prefix='0000' if self.c.committee_prefix!='0000' else '0001'
        with self.assertRaises(ValidationError):self.c.save()
        a.username=self.c2.committee_prefix+'.leader'
        with self.assertRaises(ValidationError):a.save()

    def test_hash_collision_retries_without_duplicate_prefix(self):
        candidate='ffff' if self.c.committee_prefix!='ffff' else 'fffe'
        Class.objects.filter(committee_prefix=candidate).update(committee_prefix='fffd')
        with patch('manage.models.committee_prefix',return_value=candidate):
            created=Class.objects.create(owner=self.owner,classname='冲突测试',committee_prefix=self.c.committee_prefix)
        self.assertEqual(created.committee_prefix,candidate)
        self.assertNotEqual(created.committee_prefix,self.c.committee_prefix)

    def test_full_username_required_and_40_character_suffix_supported(self):
        account,_,_=self.create('x'*40)
        self.assertEqual(len(account.username),45)
        good=Client().post('/login/',{'role':'committee','username':account.username.upper(),'password':self.password},content_type='application/json')
        self.assertEqual(good.status_code,200)
        bad=Client().post('/login/',{'role':'committee','username':'x'*40,'password':self.password},content_type='application/json')
        self.assertEqual(bad.status_code,401)

    def test_owner_can_retrieve_after_new_session_without_plaintext_storage(self):
        account,_,response=self.create()
        self.assertNotIn(self.password,json.dumps(response))
        self.assertNotIn(self.password,account.credential_ciphertext)
        self.assertNotEqual(account.password,self.password)
        self.assertTrue(account.check_password(self.password))
        fresh=Client();fresh.force_login(self.owner)
        first=self.copy(account,client=fresh);second=self.copy(account)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['data'],{'username':account.username,'password':self.password,'class_name':self.c.classname,'active':True})
        self.assertEqual(second.json(),first.json())
        self.assertIn('no-store',first['Cache-Control'])
        self.assertEqual(AuditEvent.objects.filter(kind='committee_credentials_read').count(),2)
        self.assertNotIn(self.password,str(list(AuditEvent.objects.values())))
        self.assertNotIn(self.password,str(list(Submission.objects.values())))
        listing=fresh.get(self.account_url()).json()
        self.assertNotIn(self.password,str(listing))
        self.assertNotIn('credential_ciphertext',str(listing))

    def test_anonymous_committee_other_owner_and_other_class_cannot_read(self):
        account,_,_=self.create()
        other=Client();other.force_login(self.other)
        committee,_=self.sign_in()
        for client in (Client(),other,committee):
            response=self.copy(account,client=client)
            self.assertEqual(response.status_code,403)
            self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.copy(account,classroom=self.c2).status_code,404)
        self.assertFalse(AuditEvent.objects.filter(kind='committee_credentials_read').exists())

    def test_csrf_and_post_are_required(self):
        account,_,_=self.create()
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(self.copy(account,client=client).status_code,403)
        url=f'/classes/{self.c.code}/committee/credentials/'
        self.assertEqual(self.owner_client.get(url,{'id':account.pk}).status_code,405)

    def test_legacy_hash_only_account_requires_reset_then_is_copyable(self):
        self.assertEqual(self.copy(self.a).json()['error']['code'],'credential_unavailable')
        reset=self.action('reset_password',id=self.a.pk,revision=1,password=self.password)
        response=self.owner_client.post(self.account_url(),reset,content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.copy(self.a).json()['data']['password'],self.password)

    def test_tamper_wrong_key_and_ciphertext_swap_are_rejected(self):
        a,_,_=self.create('first');b,_,_=self.create('second')
        original=a.credential_ciphertext
        for ciphertext in ('invalid',b.credential_ciphertext):
            CommitteeAccount.objects.filter(pk=a.pk).update(credential_ciphertext=ciphertext)
            self.assertEqual(self.copy(a).status_code,409)
        CommitteeAccount.objects.filter(pk=a.pk).update(credential_ciphertext=original)
        with override_settings(DUXING_CREDENTIAL_KEY=Fernet.generate_key().decode()):
            self.assertEqual(self.copy(a).status_code,409)
        self.assertEqual(self.copy(a).status_code,200)

    def test_reset_replaces_copy_and_old_submission_receipt_is_not_current(self):
        account,body,_=self.create()
        reset=self.action('reset_password',id=account.pk,revision=1,password='updated-synthetic-pass')
        self.owner_client.post(self.account_url(),reset,content_type='application/json')
        self.assertEqual(self.copy(account).json()['data']['password'],'updated-synthetic-pass')
        replay=self.owner_client.post(self.account_url(),body,content_type='application/json').json()['data']
        self.assertTrue(replay['replayed'])
        self.assertFalse(replay['receipt']['credentials_current'])
        self.assertNotIn('updated-synthetic-pass',str(replay))

    def test_unavailable_encryption_rolls_back_account_and_audit(self):
        from types import SimpleNamespace
        before=AuditEvent.objects.count()
        with override_settings(DUXING_CREDENTIAL_KEY='invalid'):
            with self.assertRaises(BusinessError):
                manage_accounts(SimpleNamespace(user=self.owner,session={}),self.c.code,
                                self.action(username='rollback',password=self.password))
        self.assertFalse(CommitteeAccount.objects.filter(username=self.c.committee_prefix+'.rollback').exists())
        self.assertEqual(AuditEvent.objects.count(),before)
