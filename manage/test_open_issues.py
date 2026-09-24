"""Regression coverage for history navigation and searchable record evidence."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from manage.models import Activity, Class, Report, RosterVersion, Student


class OpenIssueViewsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('open-issue-owner', password='unused')
        self.client = Client()
        self.client.force_login(self.owner)
        clock = patch('manage.services.calendar.business_today', return_value=date(2026, 9, 24))
        clock.start()
        self.addCleanup(clock.stop)

    def classroom(self, name='本班', started=date(2026, 9, 1), owner=None):
        return Class.objects.create(classname=name, owner=owner or self.owner,
                                    started_on=started, managecode='!')

    def test_global_term_menu_reaches_owned_history_only(self):
        self.classroom(started=date(2024, 9, 1))
        other = get_user_model().objects.create_user('other-open-issue-owner')
        self.classroom(name='他人班', started=date(2020, 2, 1), owner=other)
        response = self.client.get('/?format=json')
        self.assertEqual(response.status_code, 200)
        terms = [item['key'] for item in response.json()['data']['terms']]
        self.assertEqual(terms[0], '2026-autumn')
        self.assertIn('2024-autumn', terms)
        self.assertNotIn('2020-spring', terms)

    def test_august_legacy_record_is_separate_read_only_and_does_not_invent_roster(self):
        classroom = self.classroom(started=date(2026, 2, 1))
        explicit = Student.objects.create(inclass=classroom, number='001', name='有记录')
        Student.objects.create(inclass=classroom, number='002', name='无记录')
        activity = Activity.objects.create(inclass=classroom, name='旧八月点名',
                                           occurred_on=date(2026, 8, 15))
        Report.objects.create(activity=activity, student=explicit, status='absent')
        listing = self.client.get(f'/classes/{classroom.code}/august/?year=2026&format=json')
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(listing.json()['data']['readonly'])
        self.assertEqual([item['id'] for item in listing.json()['data']['records']], [activity.pk])
        detail = self.client.get(f'/classes/{classroom.code}/records/{activity.pk}/?format=json')
        self.assertEqual(detail.status_code, 200)
        data = detail.json()['data']
        self.assertTrue(data['readonly'])
        self.assertEqual([item['id'] for item in data['students']], [explicit.pk])
        self.assertEqual(data['record']['students'], [{'id': explicit.pk, 'value': 'absent'}])

    def test_record_list_filters_and_creation_time(self):
        classroom = self.classroom()
        student = Student.objects.create(inclass=classroom, number='001', name='测试')
        RosterVersion.objects.create(inclass=classroom, student=student,
            effective_term_start=date(2026, 9, 1), number='001', name='测试', sex='male')
        target = Activity.objects.create(inclass=classroom, name='专业课点名',
                                         occurred_on=date(2026, 9, 20))
        Report.objects.create(activity=target, student=student, status='late')
        Activity.objects.create(inclass=classroom, name='其他活动',
                                activity_type='activity', occurred_on=date(2026, 9, 21))
        params = 'format=json&name=专业课&date=2026-09-20&kind=class'
        classroom_data = self.client.get(f'/classes/{classroom.code}/?{params}').json()['data']
        self.assertEqual([item['id'] for item in classroom_data['records']], [target.pk])
        self.assertIn('T', classroom_data['records'][0]['time'])
        student_data = self.client.get(
            f'/classes/{classroom.code}/students/{student.pk}/?{params}').json()['data']
        self.assertEqual([item['id'] for item in student_data['records']], [target.pk])
        normalized = self.client.get(f'/classes/{classroom.code}/?format=json&date=20260920').json()['data']
        self.assertEqual(normalized['record_filters']['date'], '2026-09-20')
        self.assertEqual(self.client.get(f'/classes/{classroom.code}/?format=json&date=bad').status_code, 400)
