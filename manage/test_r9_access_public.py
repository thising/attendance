"""Synthetic R9 contracts; never reads the local production-data audit copy."""
import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from manage.models import Activity, Class, CommitteeAccount, OwnerLoginGuard, Report, RosterVersion, ScoringPolicyVersion, Student
from manage.services.access import digest
from manage.test_domain import business_day


class PublicAndThrottleTests(TestCase):
    def setUp(self):
        clock = business_day('2026-09-24')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('r9-teacher', password='safe-test-password-123')
        self.classroom = Class.objects.create(owner=self.owner, classname='合成测试班', started_on=date(2026,9,1))
        self.student = Student.objects.create(inclass=self.classroom,number='001',name='测试学生',sex='female')
        RosterVersion.objects.create(inclass=self.classroom,student=self.student,effective_term_start=date(2026,9,1),
                                     number='001',name='测试学生',sex='female')
        self.owner_client = Client()
        self.owner_client.force_login(self.owner)

    def login(self, username='r9-teacher', password='bad-password'):
        return Client().post('/login/',json.dumps({'role':'owner','username':username,'password':password}),content_type='application/json')

    def test_owner_throttle_eighth_failure_blocks_for_thirty_minutes_and_clears_after_success(self):
        with patch('manage.services.login_guard.time.time',return_value=1000):
            for _ in range(7):self.assertEqual(self.login().status_code,401)
            eighth=self.login()
            self.assertEqual(eighth.status_code,429)
            self.assertEqual(eighth['Retry-After'],'1800')
            self.assertEqual(self.login(password='safe-test-password-123').status_code,429)
        guard=OwnerLoginGuard.objects.get(username_digest=digest('r9-teacher'))
        self.assertEqual(guard.blocked_until,2800)
        with patch('manage.services.login_guard.time.time',return_value=2799):
            self.assertEqual(self.login(password='safe-test-password-123').status_code,429)
        with patch('manage.services.login_guard.time.time',return_value=2800):
            self.assertEqual(self.login(password='safe-test-password-123').status_code,200)
        guard.refresh_from_db()
        self.assertEqual((guard.failures,guard.blocked_until),(0,0))

    def test_failure_window_resets_after_five_minutes_and_unknown_name_is_limited(self):
        with patch('manage.services.login_guard.time.time',return_value=1000):
            for _ in range(7):self.assertEqual(self.login(username='unknown-r9').status_code,401)
        with patch('manage.services.login_guard.time.time',return_value=1300):
            self.assertEqual(self.login(username='unknown-r9').status_code,401)
        guard=OwnerLoginGuard.objects.get(username_digest=digest('unknown-r9'))
        self.assertEqual(guard.failures,1)

    def test_public_link_is_owner_managed_current_term_only_and_revocable(self):
        settings=f'/classes/{self.classroom.code}/public-report/'
        anon=Client()
        self.assertEqual(anon.get(settings).status_code,401)
        self.assertEqual(anon.get(f'/classes/{self.classroom.code}/?format=json').status_code,401)
        issued=self.owner_client.post(settings,json.dumps({'action':'enable'}),content_type='application/json')
        self.assertEqual(issued.status_code,200,issued.content)
        first=issued.json()['data']
        self.assertIn('<svg',first['qr_svg'])
        self.assertNotIn('001',first['url'])
        public=anon.get(first['url']+'?term=2026-spring')
        self.assertEqual(public.status_code,200)
        self.assertContains(public,'2026 秋季')
        self.assertContains(public,'测试学生')
        self.assertContains(public,'2026年9月')
        self.assertContains(public,'报告更新于')
        self.assertNotContains(public,'2026 春季')
        self.assertEqual(public['Referrer-Policy'],'no-referrer')
        self.assertIn('no-store',public['Cache-Control'])
        rotated=self.owner_client.post(settings,json.dumps({'action':'rotate'}),content_type='application/json').json()['data']
        self.assertNotEqual(first['url'],rotated['url'])
        self.assertEqual(anon.get(first['url']).status_code,404)
        self.assertEqual(anon.get(rotated['url']).status_code,200)
        self.owner_client.post(settings,json.dumps({'action':'disable'}),content_type='application/json')
        self.assertEqual(anon.get(rotated['url']).status_code,404)
        renewed=self.owner_client.post(settings,json.dumps({'action':'enable'}),content_type='application/json').json()['data']
        self.assertNotEqual(rotated['url'],renewed['url'])
        with business_day('2027-08-01'):
            august=anon.get(renewed['url'])
            self.assertEqual(august.status_code,200)
            self.assertContains(august,'当前暂无开放学期')
            self.assertNotContains(august,'测试学生')

    def test_ended_term_uses_neutral_history_view(self):
        history=self.owner_client.get(f'/classes/{self.classroom.code}/?term=2026-spring')
        self.assertEqual(history.status_code,200)
        self.assertContains(history,'class="historical-view"')

    def test_record_counts_follow_kind_and_positive_scoring_weight(self):
        ScoringPolicyVersion.objects.create(owner=self.owner,effective_term_start=date(2026,9,1),low=0,dlow=0)
        other=Student.objects.create(inclass=self.classroom,number='002',name='第二位')
        RosterVersion.objects.create(inclass=self.classroom,student=other,effective_term_start=date(2026,9,1),
                                     number='002',name='第二位',sex='male')
        attendance=Activity.objects.create(inclass=self.classroom,name='点名',activity_type='class',occurred_on=date(2026,9,10))
        activity=Activity.objects.create(inclass=self.classroom,name='活动',activity_type='activity',occurred_on=date(2026,9,11))
        discipline=Activity.objects.create(inclass=self.classroom,name='违纪',activity_type='discipline',occurred_on=date(2026,9,12))
        Report.objects.create(activity=attendance,student=self.student,status='late')
        Report.objects.create(activity=attendance,student=other,status='leave')
        Report.objects.create(activity=activity,student=self.student,level='low')
        Report.objects.create(activity=activity,student=other,level='high')
        Report.objects.create(activity=discipline,student=self.student,discipline='low')
        Report.objects.create(activity=discipline,student=other,discipline='high')
        result=self.owner_client.get(f'/classes/{self.classroom.code}/?format=json').json()['data']
        counts={record['id']:record['student_count'] for record in result['records']}
        self.assertEqual(counts,{attendance.pk:2,activity.pk:1,discipline.pk:1})

    def test_committee_can_issue_and_share_own_class_link_but_cannot_revoke_it(self):
        account=CommitteeAccount(inclass=self.classroom,username='classrep',display_name='班委')
        account.set_password('safe-committee-password-123')
        account.save()
        committee=Client()
        login=committee.post('/login/',json.dumps({'role':'committee','username':account.username,
                                                     'password':'safe-committee-password-123'}),content_type='application/json')
        self.assertEqual(login.status_code,200,login.content)
        settings=f'/classes/{self.classroom.code}/public-report/'
        issued=committee.post(settings,json.dumps({'action':'enable'}),content_type='application/json')
        self.assertEqual(issued.status_code,200,issued.content)
        self.assertIn('<svg',committee.get(settings).json()['data']['qr_svg'])
        self.assertEqual(committee.get(f'/classes/{self.classroom.code}/?format=json').json()['data']['public_report']['active'],True)
        self.assertEqual(committee.post(settings,json.dumps({'action':'rotate'}),content_type='application/json').status_code,403)
        self.assertEqual(committee.post(settings,json.dumps({'action':'disable'}),content_type='application/json').status_code,403)
        other=Class.objects.create(owner=self.owner,classname='另一班',started_on=date(2026,9,1))
        self.assertEqual(committee.get(f'/classes/{other.code}/public-report/').status_code,403)
