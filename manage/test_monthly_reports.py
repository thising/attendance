"""Synthetic monthly-report contracts; no real database or workbook is read."""
from copy import deepcopy
from datetime import date, datetime
import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from manage.models import Activity, Class, ClassTerm, Report, RosterVersion, ScoringPolicyVersion, Student, WEIGHT_DEFAULTS
from manage.services import calendar, monthly
from manage.test_domain import business_day


class MonthlyReportTests(TestCase):
    def test_historical_snapshot_keeps_roster_scores_and_record_details_after_fact_drift(self):
        student = self.student(start=date(2026, 2, 1))
        activity = self.fact(student, '2026-07-12', 'late')
        activity.details = '旧学期课堂说明'
        activity.save(update_fields=['details'])
        url = f'/classes/{self.classroom.code}/?format=json&term=2026-spring'
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200, first.content)
        original = first.json()['data']
        self.assertEqual(original['records'][0]['details'], '旧学期课堂说明')
        archive = ClassTerm.objects.get(inclass=self.classroom, term_key='2026-spring')
        self.assertEqual(archive.archived_data['snapshot_version'], 1)
        original_score = original['students'][0]['score']
        Activity.objects.filter(pk=activity.pk).update(name='被改写的实时名称', details='被改写的实时详情')
        Report.objects.filter(activity=activity).delete()
        RosterVersion.objects.filter(student=student).update(name='被改写的实时姓名')
        again = self.client.get(url).json()['data']
        self.assertEqual(again['students'][0]['score'], original_score)
        self.assertEqual(again['students'][0]['name'], original['students'][0]['name'])
        self.assertEqual(again['records'][0]['name'], original['records'][0]['name'])
        self.assertEqual(again['summary']['activity_count'], 1)
        detail = self.client.get(f'/classes/{self.classroom.code}/records/{activity.pk}/?format=json&term=2026-spring')
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()['data']['record']['details'], '旧学期课堂说明')
        personal = self.client.get(self.detail_url(student, '2026-spring')).json()['data']
        self.assertEqual(personal['records'][0]['name'], original['records'][0]['name'])
        Activity.objects.filter(pk=activity.pk).delete()
        retained = self.client.get(f'/classes/{self.classroom.code}/records/{activity.pk}/?format=json&term=2026-spring')
        self.assertEqual(retained.status_code, 200, retained.content)
        self.assertEqual(retained.json()['data']['record']['name'], original['records'][0]['name'])

    def test_snapshot_record_retains_student_removed_before_term_end(self):
        student = self.student(start=date(2026, 2, 1))
        activity = self.fact(student, '2026-07-12', 'late')
        RosterVersion.objects.filter(student=student).update(active=False)
        Student.objects.filter(pk=student.pk).update(active=False)
        detail = self.client.get(f'/classes/{self.classroom.code}/records/{activity.pk}/?format=json&term=2026-spring')
        self.assertEqual(detail.status_code, 200, detail.content)
        data = detail.json()['data']
        self.assertEqual([row['id'] for row in data['students']], [student.pk])
        self.assertEqual(data['record']['students'], [{'id':student.pk,'value':'late'}])

    def test_historical_fact_without_verified_roster_cannot_be_frozen_as_empty(self):
        Activity.objects.create(inclass=self.classroom, name='只有记录没有名单', occurred_on=date(2026, 7, 12))
        response = self.client.get(f'/classes/{self.classroom.code}/?format=json&term=2026-spring')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'historical_roster_unverified')
        self.assertFalse(ClassTerm.objects.filter(inclass=self.classroom,term_key='2026-spring').exists())

    def setUp(self):
        clock = business_day('2026-10-15')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('monthly-owner')
        self.classroom = Class.objects.create(owner=self.owner, classname='合成月报班', started_on=date(2026, 2, 1))
        self.client = Client()
        self.client.force_login(self.owner)

    def student(self, number='001', start=date(2026, 9, 1)):
        student = Student.objects.create(inclass=self.classroom, number=number, name=f'合成学生{number}')
        RosterVersion.objects.create(student=student, inclass=self.classroom,
            effective_term_start=start, number=student.number, name=student.name, sex=student.sex)
        return student

    def fact(self, student, day, value='late', kind='class', created_at=None):
        activity = Activity.objects.create(inclass=self.classroom, name=f'合成{day}', occurred_on=date.fromisoformat(day), activity_type=kind)
        fields = {'status': value} if kind == 'class' else {'level': value} if kind == 'activity' else {'discipline': value.removeprefix('d')}
        Report.objects.create(activity=activity, student=student, **fields)
        if created_at:
            Activity.objects.filter(pk=activity.pk).update(time=created_at)
        return activity

    def detail_url(self, student, term='2026-autumn', month=None):
        return f'/classes/{self.classroom.code}/students/{student.pk}/?format=json&term={term}' + (f'&month={month}' if month is not None else '')

    def test_class_page_lists_all_elapsed_term_months_and_nine_counts_without_writes(self):
        student = self.student()
        self.student('002')
        self.fact(student, '2026-09-12')
        self.fact(student, '2026-10-12', 'high', 'activity')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f'/classes/{self.classroom.code}/?format=json')
        self.assertEqual(response.status_code, 200)
        forbidden = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b', re.I)
        self.assertFalse([query['sql'] for query in queries if forbidden.search(query['sql'])])
        overview = response.json()['data']['monthly_overview']
        self.assertEqual([column['key'] for column in overview['months']], ['2026-09', '2026-10'])
        self.assertEqual([column['label'] for column in overview['months']], ['2026年9月', '2026年10月'])
        rows = {row['number']: row for row in overview['rows']}
        self.assertEqual([cell['score'] for cell in rows['001']['months']], ['59.00', '65.00'])
        self.assertEqual([cell['score'] for cell in rows['002']['months']], ['60.00', '60.00'])
        self.assertEqual(rows['001']['months'][1]['counts']['high'], 1)
        self.assertEqual(rows['001']['months'][0]['counts']['late'], 1)
        for row in rows.values():
            for cell in row['months']:
                self.assertEqual(set(cell['counts']), set(WEIGHT_DEFAULTS))
                self.assertEqual(cell['score_base'], '60.00')
                self.assertEqual(cell['reason'], '')
                self.assertIn(f"&month={cell['key']}", cell['url'])

    def test_february_previous_month_uses_historical_roster_rule_and_base(self):
        old_student = self.student(start=date(2026, 9, 1))
        new_student = self.student('002', start=date(2027, 2, 1))
        ScoringPolicyVersion.objects.create(owner=self.owner, effective_term_start=date(2026, 9, 1), base_score='70.00', late='3.00')
        ScoringPolicyVersion.objects.create(owner=self.owner, effective_term_start=date(2027, 2, 1), base_score='80.00', late='8.00')
        self.fact(old_student, '2027-01-10')
        self.fact(old_student, '2027-02-10')
        self.fact(new_student, '2027-02-10')
        with business_day('2027-02-15'):
            overview = monthly.monthly_overview(self.classroom, calendar.Term(2027, 'spring'))
        self.assertEqual([column['term_key'] for column in overview['months']], ['2027-spring', '2026-autumn'])
        rows = {row['id']: row for row in overview['rows']}
        self.assertEqual([cell['score'] for cell in rows[old_student.pk]['months']], ['72.00', '67.00'])
        self.assertEqual([cell['score_base'] for cell in rows[old_student.pk]['months']], ['80.00', '70.00'])
        self.assertIn('?term=2026-autumn&month=2027-01', rows[old_student.pk]['months'][1]['url'])
        previous = rows[new_student.pk]['months'][1]
        self.assertEqual(previous['reason'], '该学期未在册')
        self.assertIsNone(previous['score'])
        self.assertIsNone(previous['counts'])
        self.assertIsNone(previous['url'])

    def test_august_gap_never_gets_a_score_in_august_or_september(self):
        self.student(start=date(2026, 2, 1))
        for day, term, expected in (
            ('2026-08-15', calendar.Term(2026, 'spring'), [('2026-08', None), ('2026-07', '60.00')]),
            ('2026-09-15', calendar.Term(2026, 'autumn'), [('2026-09', '60.00'), ('2026-08', None)]),
        ):
            with self.subTest(day=day), business_day(day):
                overview = monthly.monthly_overview(self.classroom, term)
                cells = overview['rows'][0]['months']
                self.assertEqual([(cell['key'], cell['score']) for cell in cells], expected)
                august = next(cell for cell in cells if cell['key'] == '2026-08')
                self.assertEqual(august['reason'], '8月不计分')
                self.assertIsNone(august['counts'])
                self.assertIsNone(august['url'])
                descriptor = next(item for item in overview['months'] if item['key'] == '2026-08')
                self.assertFalse(descriptor['available'])
                self.assertIsNone(descriptor['term_key'])

    def test_january_uses_december_of_previous_calendar_year(self):
        student = self.student()
        self.fact(student, '2026-12-20', 'absent')
        self.fact(student, '2027-01-02')
        with business_day('2027-01-15'):
            overview = monthly.monthly_overview(self.classroom, calendar.Term(2026, 'autumn'))
        self.assertEqual([column['key'] for column in overview['months']], ['2027-01', '2026-12'])
        self.assertEqual([cell['score'] for cell in overview['rows'][0]['months']], ['59.00', '58.00'])

    def test_historical_term_uses_its_last_two_months_and_future_term_stays_unknown(self):
        self.student(start=date(2026, 2, 1))
        history = monthly.monthly_overview(self.classroom, calendar.Term(2026, 'spring'))
        self.assertEqual([column['key'] for column in history['months']], ['2026-07', '2026-06'])
        self.assertEqual([column['label'] for column in history['months']], ['期末月 · 2026年7月', '前一月 · 2026年6月'])
        future = monthly.monthly_overview(self.classroom, calendar.Term(2027, 'spring'))
        self.assertTrue(all(not column['available'] for column in future['months']))
        self.assertEqual([cell['score'] for cell in future['rows'][0]['months']], [None, None])
        self.assertEqual({cell['reason'] for cell in future['rows'][0]['months']}, {'月份尚未开始'})

    def test_reviewed_archive_missing_months_is_unknown_without_reconstruction(self):
        student = self.student(start=date(2026, 2, 1))
        self.fact(student, '2026-07-12', 'absent')
        counts = {key: 0 for key in WEIGHT_DEFAULTS}
        counts['low'] = 1
        baseline = {'term_key': '2026-spring', 'policy': {'version': 0, 'weights': {key: str(value) for key, value in WEIGHT_DEFAULTS.items()}},
            'summary': {'student_count': 1, 'month_count': 6, 'average': '42.10'},
            'rows': [{'id': student.pk, 'number': 'HIST001', 'name': '保存的历史姓名', 'score': '42.10', 'counts': counts,
                      'months': [{'key': '2026-07', 'score': '61.23', 'counts': counts}, {'key': '2026-06', 'score': '60.00'}]}]}
        archive = ClassTerm.objects.create(inclass=self.classroom, owner=self.owner, term_key='2026-spring',
            archived_data=deepcopy(baseline), baseline_source='synthetic-reviewed')
        with CaptureQueriesContext(connection) as queries:
            overview = monthly.monthly_overview(self.classroom, calendar.Term(2026, 'spring'))
        self.assertFalse(any('manage_report' in query['sql'].lower() for query in queries))
        row = overview['rows'][0]
        self.assertEqual((row['number'], row['name']), ('HIST001', '保存的历史姓名'))
        self.assertEqual(row['months'][0]['score'], '61.23')  # Deliberately not recomputed from current facts.
        self.assertEqual(row['months'][0]['score_base'], '60.00')
        self.assertIsNone(row['months'][1]['score'])
        self.assertIsNone(row['months'][1]['counts'])
        self.assertEqual(row['months'][1]['reason'], '历史月份明细待核实')
        response = self.client.get(self.detail_url(student, '2026-spring', '2026-06'))
        self.assertEqual(response.status_code, 200)
        detail = response.json()['data']['selected_month']
        self.assertFalse(detail['available'])
        self.assertIsNone(detail['score'])
        archive.refresh_from_db()
        self.assertEqual(archive.archived_data, baseline)

    def test_student_month_link_filters_records_and_keeps_return_to_term(self):
        student = self.student()
        september = self.fact(student, '2026-09-30')
        october = self.fact(student, '2026-10-01', 'high', 'activity')
        response = self.client.get(self.detail_url(student, month='2026-10'))
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual([record['id'] for record in data['records']], [october.pk])
        self.assertEqual(data['pagination']['count'], 1)
        self.assertEqual(data['selected_month']['score'], '65.00')
        self.assertEqual(data['selected_month']['counts']['high'], 1)
        self.assertEqual(data['selected_month']['counts']['late'], 0)
        self.assertTrue(data['selected_month']['available'])
        self.assertNotIn('&month=', data['term_detail_url'])
        full_term = self.client.get(data['term_detail_url'] + '&format=json').json()['data']
        self.assertIsNone(full_term['selected_month'])
        self.assertEqual({record['id'] for record in full_term['records']}, {september.pk, october.pk})

    def test_explicit_month_rejects_invalid_out_of_term_and_future_values(self):
        student = self.student()
        cases = {
            '': 'invalid_month', '2026-1': 'invalid_month', '2026-13': 'invalid_month',
            '0000-01': 'invalid_month', '2026-10-01': 'invalid_month',
            '2026-08': 'month_outside_term', '2026-07': 'month_outside_term',
            '2026-11': 'future_month', '2027-01': 'future_month',
        }
        for key, code in cases.items():
            with self.subTest(month=key):
                response = self.client.get(self.detail_url(student, month=key))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['error']['code'], code)

    def test_month_detail_preserves_anonymous_and_cross_class_boundaries(self):
        student = self.student()
        anonymous = Client()
        self.assertEqual(anonymous.get(self.detail_url(student, month='2026-10')).status_code, 401)
        foreign = Client()
        foreign.force_login(get_user_model().objects.create_user('monthly-foreign-owner'))
        self.assertEqual(foreign.get(self.detail_url(student, month='2026-10')).status_code, 403)
        other_class = Class.objects.create(owner=self.owner, classname='另一个合成班')
        other_student = Student.objects.create(inclass=other_class, number='001', name='别班学生')
        self.assertEqual(self.client.get(self.detail_url(other_student, month='2026-10')).status_code, 404)

    def test_class_metrics_use_selected_term_dates_and_all_three_record_types(self):
        student = self.student(start=date(2026, 2, 1))
        for day, kind, value, created_at in (
            ('2026-07-31', 'class', 'late', datetime(2026, 10, 15, 23)),
            ('2026-08-10', 'activity', 'low', datetime(2026, 10, 15, 22)),
            ('2026-09-01', 'class', 'late', datetime(2026, 9, 1, 12)),
            ('2026-10-01', 'activity', 'low', datetime(2026, 10, 2, 12)),
            ('2026-10-10', 'discipline', 'dlow', datetime(2026, 10, 11, 12)),
        ):
            self.fact(student, day, value, kind, created_at)
        ScoringPolicyVersion.objects.create(owner=self.owner, effective_term_start=date(2026, 9, 1), base_score='75.00')
        for suffix in ('', f'classes/{self.classroom.code}/'):
            with self.subTest(page=suffix):
                data = self.client.get(f'/{suffix}?format=json&term=2026-autumn').json()['data']
                summary = data['class_summaries'][0] if not suffix else data['summary']
                self.assertEqual(summary['activity_count'], 3)
                self.assertEqual(summary['last_activity_at'], '2026-10-11T12:00:00')
                self.assertNotIn('average', summary)
                if not suffix:
                    self.assertEqual(data['dashboard_students'][0]['score_base'], '75.00')
        history = self.client.get(f'/classes/{self.classroom.code}/?format=json&term=2026-spring').json()['data']
        self.assertEqual(history['summary']['activity_count'], 1)
        self.assertEqual(history['summary']['last_activity_at'], '2026-10-15T23:00:00')
