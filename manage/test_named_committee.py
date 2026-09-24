"""Synthetic regressions for the named-account and personal-dashboard revision."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import connections, close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from manage.models import Class, CommitteeAccount, AuditEvent, RosterVersion, Student, Activity
from manage.services.committee import manage_accounts
from manage.services.errors import BusinessError
from manage.test_domain import business_day


class AccountFixtures:
    def setUp(self):
        super().setUp()
        clock=business_day(date(2026,10,15));clock.__enter__()
        self.addCleanup(clock.__exit__,None,None,None)
        self.owner=get_user_model().objects.create_user('named-owner',password='teacher-synthetic-pass')
        self.other=get_user_model().objects.create_user('other-named-owner')
        self.c=Class.objects.create(owner=self.owner,classname='一班',started_on=date(2026,9,1))
        self.c2=Class.objects.create(owner=self.owner,classname='二班',started_on=date(2026,9,1))
        self.private=Class.objects.create(owner=self.other,classname='私有班',started_on=date(2026,9,1))
        self.owner_client=Client();self.owner_client.force_login(self.owner)
        self.password='named-synthetic-pass'
        self.a=CommitteeAccount.objects.create(inclass=self.c,username='committee.one',display_name='值日班长',
                                               password=make_password(self.password))

    def action(self,action='create',**values):
        return {'action':action,'submission_id':str(uuid4()),**values}

    def account_url(self,c=None):return f'/classes/{(c or self.c).code}/committee/'

    def sign_in(self,client=None,**values):
        client=client or Client()
        response=client.post('/login/',{'role':'committee','username':self.a.username,'password':self.password,**values},
                             content_type='application/json')
        return client,response


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NamedCommitteeTests(AccountFixtures,TestCase):
    def test_dual_login_and_single_class_scope(self):
        landing=Client().get('/')
        self.assertEqual(landing.context['page'],'login')
        self.assertEqual(landing.context['data']['login_role'],'owner')
        client,response=self.sign_in()
        self.assertEqual(response.status_code,200)
        home=client.get('/?format=json').json()['data']
        self.assertEqual([c['id'] for c in home['classes']],[self.c.pk])
        self.assertEqual(home['actor']['username'],self.a.username)
        self.assertEqual(client.get(f'/classes/{self.c2.code}/?format=json').status_code,403)
        self.assertEqual(client.get(self.account_url()).status_code,403)
        self.assertEqual(client.get('/rules/').status_code,401)
        # A committee password never authenticates through the teacher channel.
        self.assertEqual(Client().post('/login/',{'role':'owner','username':self.a.username,'password':self.password},
                                       content_type='application/json').status_code,401)

    def test_old_class_code_and_shared_grant_are_retired(self):
        client=Client();session=client.session
        session['class_grants']={str(self.c.pk):{'version':1,'expires_at':9999999999}}
        session['committee_identity']='old-synthetic-grant';session.save()
        self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,401)
        public=client.get(f'/classes/{self.c.code}/')
        self.assertEqual(public.context['page'],'login')
        self.assertIsNone(public.context['data']['classroom'])
        self.assertNotContains(public,self.c.classname)
        self.assertEqual(client.post(f'/classes/{self.c.sharecode}/authorize/',{'password':self.password}).status_code,410)
        self.assertEqual(client.get(f'/classes/{self.c.sharecode}/?format=json').status_code,404)

    def test_only_class_owner_can_manage_accounts_and_password_is_never_returned(self):
        p=self.action(username='new.account',display_name='新班委',password=self.password)
        foreign=Client();foreign.force_login(self.other)
        committee,_=self.sign_in()
        for client in (Client(),foreign,committee):
            self.assertEqual(client.post(self.account_url(),p,content_type='application/json').status_code,403)
        response=self.owner_client.post(self.account_url(),p,content_type='application/json')
        self.assertEqual(response.status_code,200)
        created=CommitteeAccount.objects.get(username=self.c.committee_prefix+'.new.account')
        self.assertNotEqual(created.password,self.password)
        self.assertNotIn('password',str(response.json()))
        retry=self.owner_client.post(self.account_url(),p,content_type='application/json')
        self.assertTrue(retry.json()['data']['replayed'])
        self.assertEqual(AuditEvent.objects.filter(kind='committee_create').count(),1)

    def test_five_active_accounts_and_reactivation_limit(self):
        for i in range(4):
            p=self.action(username=f'additional.{i}',password=self.password)
            self.assertEqual(self.owner_client.post(self.account_url(),p,content_type='application/json').status_code,200)
        p=self.action(username='sixth.account',password=self.password)
        self.assertEqual(self.owner_client.post(self.account_url(),p,content_type='application/json').status_code,409)
        stop=self.action('deactivate',id=self.a.pk,revision=1)
        self.assertEqual(self.owner_client.post(self.account_url(),stop,content_type='application/json').status_code,200)
        self.assertEqual(self.owner_client.post(self.account_url(),p,content_type='application/json').status_code,200)
        activate=self.action('activate',id=self.a.pk,revision=2)
        self.assertEqual(self.owner_client.post(self.account_url(),activate,content_type='application/json').status_code,409)
        self.assertEqual(self.c.committee_accounts.filter(active=True).count(),5)

    def test_reset_disable_and_logout_revoke_named_login(self):
        client,_=self.sign_in()
        p=self.action('reset_password',id=self.a.pk,revision=1,password='new-synthetic-password')
        self.assertEqual(self.owner_client.post(self.account_url(),p,content_type='application/json').status_code,200)
        self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,401)
        self.assertEqual(self.sign_in()[1].status_code,401)
        client,response=self.sign_in(password='new-synthetic-password')
        self.assertEqual(response.status_code,200)
        p=self.action('deactivate',id=self.a.pk,revision=2)
        self.assertEqual(self.owner_client.post(self.account_url(),p,content_type='application/json').status_code,200)
        self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,401)
        client.post('/logout/')
        self.assertNotIn('committee_auth',client.session)

    def test_expiry_is_absolute_and_reauth_cannot_switch_class_or_account(self):
        with patch('manage.services.committee.time.time',return_value=1000):client,response=self.sign_in()
        self.assertEqual(response.json()['data']['expires_at'],11800)
        with patch('manage.services.access.time.time',return_value=11799):
            self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,200)
            self.assertEqual(client.session['committee_auth']['expires_at'],11800)
        with patch('manage.services.access.time.time',return_value=11800):
            self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,401)
        wrong=self.sign_in(client,reauthorize=True,class_id=self.c2.pk)[1]
        self.assertEqual(wrong.status_code,403)
        second=CommitteeAccount.objects.create(inclass=self.c,username='different.actor',password=make_password(self.password))
        self.assertEqual(self.sign_in(client,username=second.username,reauthorize=True,class_id=self.c.pk)[1].status_code,403)
        self.assertEqual(self.sign_in(client,reauthorize=True,class_id=self.c.pk)[1].status_code,200)

    def test_failed_login_throttle_survives_new_cookie(self):
        for _ in range(7):self.assertEqual(self.sign_in(password='incorrect')[1].status_code,401)
        self.assertEqual(self.sign_in(password='incorrect')[1].status_code,429)
        self.assertEqual(self.sign_in()[1].status_code,429)

    def test_no_cross_class_account_edit_or_transfer(self):
        wrong=self.owner_client.post(self.account_url(self.c2),self.action('deactivate',id=self.a.pk,revision=1),content_type='application/json')
        self.assertEqual(wrong.status_code,404)
        self.a.inclass=self.c2
        with self.assertRaises(ValidationError):self.a.save()

    def test_account_management_is_csrf_protected(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        self.assertEqual(client.post(self.account_url(),self.action(username='test.csrf',password=self.password),content_type='application/json').status_code,403)

    def test_same_account_retry_after_password_reset_keeps_one_record_and_named_audit(self):
        client,_=self.sign_in()
        body={'term_key':'2026-autumn','submission_id':str(uuid4()),'roster_revision':1,'revision':0,
              'actor_context':{'role':'committee','id':self.a.pk},
              'record':{'kind':'class','name':'个人账号重试','date':'2026-10-15','students':[]}}
        url=f'/classes/{self.c.code}/records/new/'
        original=client.post(url,body,content_type='application/json')
        self.assertEqual(original.status_code,200)
        self.owner_client.post(self.account_url(),self.action('reset_password',id=self.a.pk,revision=1,
                                 password='new-synthetic-password'),content_type='application/json')
        self.sign_in(client,password='new-synthetic-password',reauthorize=True,class_id=self.c.pk)
        retry=client.post(url,body,content_type='application/json')
        self.assertEqual(retry.status_code,200)
        self.assertTrue(retry.json()['data']['replayed'])
        self.assertEqual(Activity.objects.count(),1)
        log=AuditEvent.objects.get(kind='record_created')
        self.assertEqual(log.actor_id,self.a.pk)
        self.assertIn(self.a.username,log.actor_label)
        # A different operator must not accidentally retry an uncertain original draft.
        second=CommitteeAccount.objects.create(inclass=self.c,username='second.operator',password=make_password(self.password))
        other,_=self.sign_in(username=second.username)
        rejected=other.post(url,body,content_type='application/json')
        self.assertEqual(rejected.status_code,409)
        self.assertEqual(rejected.json()['error']['code'],'actor_changed')
        self.assertEqual(Activity.objects.count(),1)

    def test_credentials_can_be_revoked_in_august_without_changing_business_history(self):
        client,_=self.sign_in()
        with business_day(date(2026,8,15)):
            response=self.owner_client.post(self.account_url(),self.action('deactivate',id=self.a.pk,revision=1),
                                            content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(client.get(f'/classes/{self.c.code}/?format=json').status_code,401)
        self.assertEqual(Activity.objects.count(),0)

    def test_account_event_failure_rolls_back_creation(self):
        from django.db import IntegrityError
        from types import SimpleNamespace
        with patch('manage.services.writes.AuditEvent.objects.create',side_effect=IntegrityError('synthetic')):
            with self.assertRaises(IntegrityError):
                manage_accounts(SimpleNamespace(user=self.owner,session={}),self.c.code,
                                self.action(username='rollback.account',password=self.password))
        self.assertFalse(CommitteeAccount.objects.filter(username=self.c.committee_prefix+'.rollback.account').exists())

    def test_dashboard_has_all_owned_students_and_no_class_average(self):
        for index,c in enumerate((self.c,self.c2,self.private)):
            student=Student.objects.create(inclass=c,number=f'00{index}',name=f'学生{index}')
            RosterVersion.objects.create(student=student,inclass=c,effective_term_start=date(2026,9,1),
                                         number=student.number,name=student.name,sex='male')
        data=self.owner_client.get('/?format=json').json()['data']
        self.assertEqual({r['class_id'] for r in data['dashboard_students']},{self.c.pk,self.c2.pk})
        self.assertEqual(len(data['dashboard_students']),2)
        for row in data['dashboard_students']:
            self.assertEqual(len(row['counts']),9)
            self.assertEqual(row['score'],'60.00')
        for c in data['class_summaries']:
            self.assertNotIn('average',c)
            self.assertEqual(c['activity_count'],0)
            self.assertIsNone(c['last_activity_at'])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NamedCommitteeConcurrencyTests(AccountFixtures,TransactionTestCase):
    def test_two_concurrent_creations_cannot_exceed_five(self):
        from types import SimpleNamespace
        for i in range(3):CommitteeAccount.objects.create(inclass=self.c,username=f'existing.{i}',password='!')
        barrier=Barrier(2)
        def create(i):
            close_old_connections()
            try:
                request=SimpleNamespace(user=self.owner,session={})
                barrier.wait(timeout=5)
                try:
                    manage_accounts(request,self.c.code,self.action(username=f'racing.{i}',password=self.password))
                    return 'created'
                except BusinessError as error:return error.code
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create,range(2)))
        self.assertCountEqual(results,['created','committee_limit'])
        self.assertEqual(self.c.committee_accounts.filter(active=True).count(),5)
