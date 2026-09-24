"""Monthly base/floor/ceiling regressions on synthetic, isolated fixtures."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from manage.models import (Activity, AuditEvent, Class, ClassTerm, OwnerScoringSettings,
    OwnerTermPolicy, Report, RosterVersion, ScoringPolicyVersion, Student, Submission, WEIGHT_DEFAULTS)
from manage.services import calendar, roster, rules, scoring
from manage.services.errors import BusinessError
from manage.test_domain import business_day


class MonthlyValueTests(SimpleTestCase):
    def test_defaults_keep_legacy_floor_and_no_ceiling(self):
        self.assertEqual(scoring.month_score({}, WEIGHT_DEFAULTS), Decimal('60'))
        self.assertEqual(scoring.month_score({'dhigh': 10}, WEIGHT_DEFAULTS), Decimal('0'))
        self.assertEqual(scoring.month_score({'high': 100}, WEIGHT_DEFAULTS), Decimal('560'))

    def test_configurable_floor_and_ceiling_apply_to_each_month(self):
        monthly = {'base': '75.25', 'minimum': '70.50', 'maximum': '80.75'}
        self.assertEqual(scoring.month_score({}, WEIGHT_DEFAULTS, monthly), Decimal('75.25'))
        self.assertEqual(scoring.month_score({'dhigh': 10}, WEIGHT_DEFAULTS, monthly), Decimal('70.50'))
        self.assertEqual(scoring.month_score({'high': 10}, WEIGHT_DEFAULTS, monthly), Decimal('80.75'))
        monthly['maximum'] = None
        self.assertEqual(scoring.month_score({'high': 10}, WEIGHT_DEFAULTS, monthly), Decimal('125.25'))

    def test_validation_normalizes_numbers_and_allows_equal_bounds(self):
        self.assertEqual(rules.validate_monthly({'base': 0, 'minimum': '-0.00', 'maximum': '0'}),
                         {'base': '0.00', 'minimum': '0.00', 'maximum': '0.00'})
        self.assertEqual(rules.validate_monthly({'base': '999999.50', 'minimum': 1, 'maximum': None}),
                         {'base': '999999.50', 'minimum': '1.00', 'maximum': None})

    def test_validation_rejects_bad_shape_values_precision_and_order(self):
        valid = {'base': '60.00', 'minimum': '0.00', 'maximum': None}
        bad = [None, [], {}, {'base': '60.00', 'minimum': '0.00'}, {**valid, 'unexpected': 1},
               {**valid, 'minimum': '60.01'}, {**valid, 'maximum': '59.99'}]
        for field in valid:
            for value in (True, '-0.01', '1000000', '1.001', '0.25', '999999.99', 'NaN', 'Infinity', '', [], {}):
                bad.append({**valid, field: value})
        bad += [{**valid, 'base': None}, {**valid, 'minimum': None}]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(BusinessError) as error:
                rules.validate_monthly(data)
            self.assertEqual(error.exception.code, 'invalid_monthly')

    def test_weights_and_monthly_values_use_half_point_steps(self):
        self.assertEqual(rules.validate_weights({**WEIGHT_DEFAULTS, 'late':'1.5'})['late'], '1.50')
        with self.assertRaises(BusinessError) as error:
            rules.validate_weights({**WEIGHT_DEFAULTS, 'late':'1.25'})
        self.assertEqual(error.exception.details['field'], 'late')
        with self.assertRaises(BusinessError) as error:
            rules.validate_monthly({'base':'60.25','minimum':'0','maximum':None})
        self.assertEqual(error.exception.details['field'], 'monthly.base')
        self.assertEqual(rules.validate_average_decimal_places('4'), 4)
        for value in (-1, 5, True, '1.5', None):
            with self.subTest(value=value), self.assertRaises(BusinessError):
                rules.validate_average_decimal_places(value)


class MonthlyScoringTests(TestCase):
    def setUp(self):
        clock = business_day('2026-10-15')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('monthly-owner')
        self.other = get_user_model().objects.create_user('monthly-other-owner')
        self.request = SimpleNamespace(user=self.owner, session={})
        self.term = calendar.Term(2026, 'autumn')
        self.c, self.student = self.classroom(self.owner, '月度合成一班')

    def classroom(self, owner, name):
        classroom = Class.objects.create(owner=owner, classname=name, started_on=date(2025, 2, 1))
        student = Student.objects.create(inclass=classroom, number='SYN001', name='合成学生')
        RosterVersion.objects.create(student=student, inclass=classroom, number=student.number,
            name=student.name, sex=student.sex, effective_term_start=date(2025, 2, 1))
        return classroom, student

    def payload(self, monthly=None, **changes):
        current = scoring.policy_for(self.owner.pk, calendar.term_for_date(calendar.business_today()))
        result = {'weights': current['weights'], 'revision': current['revision'],
                  'term_key': calendar.term_for_date(calendar.business_today()).key,
                  'submission_id': str(uuid4()), 'action': 'save', **changes}
        if monthly is not None:
            result['monthly'] = monthly
        return result

    def save_monthly(self, base='75.00', minimum='70.00', maximum='80.00'):
        return rules.change_rules(self.request, self.payload({'base': base, 'minimum': minimum, 'maximum': maximum}))

    def report(self, classroom=None, term=None):
        return scoring.class_report(classroom or self.c, term or self.term)

    def snapshot(self):
        return {model.__name__: list(model.objects.order_by('pk').values()) for model in (
            ScoringPolicyVersion, OwnerScoringSettings, OwnerTermPolicy, Submission, AuditEvent,
        )}

    def test_default_read_is_nonmutating_and_monthly_config_is_serialized(self):
        before = self.snapshot()
        self.assertEqual(scoring.policy_for(self.owner.pk, self.term)['monthly'], scoring.MONTHLY_DEFAULTS)
        self.assertEqual(self.snapshot(), before)
        saved = self.save_monthly()
        expected = {'base': '75.00', 'minimum': '70.00', 'maximum': '80.00'}
        self.assertEqual(saved['policy']['monthly'], expected)
        report = self.report()
        self.assertEqual(report['policy']['monthly'], expected)
        self.assertEqual([month['score'] for month in report['rows'][0]['months']], ['75.00', '75.00'])
        self.assertEqual(report['rows'][0]['score'], '75.00')

    def test_months_are_clamped_before_average_and_average_uses_half_up(self):
        activity = Activity.objects.create(inclass=self.c, name='合成违纪', activity_type='discipline', occurred_on=date(2026, 9, 20))
        Report.objects.create(activity=activity, student=self.student, discipline='high')
        self.save_monthly(base='60.00', minimum='59.50', maximum='60.50')
        row = self.report()['rows'][0]
        self.assertEqual([month['score'] for month in row['months']], ['59.50', '60.00'])
        self.assertEqual(row['score'], '59.75')
        bonus = Activity.objects.create(inclass=self.c, name='合成活动', activity_type='activity', occurred_on=date(2026, 10, 3))
        Report.objects.create(activity=bonus, student=self.student, level='high')
        row = self.report()['rows'][0]
        self.assertEqual([month['score'] for month in row['months']], ['59.50', '60.50'])
        self.assertEqual(row['score'], '60.00')

    def test_preview_covers_all_owned_classes_and_does_not_persist_any_policy(self):
        second, _ = self.classroom(self.owner, '月度合成二班')
        foreign, _ = self.classroom(self.other, '另一负责人合成班')
        before = self.snapshot()
        proposed = {'base': '75.00', 'minimum': '70.00', 'maximum': '80.00'}
        payload = self.payload(proposed, action='preview')
        with CaptureQueriesContext(connection) as queries:
            preview = rules.change_rules(self.request, payload)['preview']
        self.assertEqual({item['id'] for item in preview['classes']}, {self.c.pk, second.pk})
        self.assertEqual(preview['student_count'], 2)
        self.assertEqual({(item['changed_count'], item['max_absolute_change']) for item in preview['classes']}, {(1, '15.00')})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        override = scoring.class_report(self.c, self.term, monthly=proposed)
        self.assertEqual(override['policy']['monthly'], proposed)
        self.assertEqual(override['rows'][0]['score'], '75.00')
        self.save_monthly()
        self.assertEqual([self.report(c)['rows'][0]['score'] for c in (self.c, second, foreign)], ['75.00', '75.00', '60.00'])

    def test_omitting_monthly_keeps_current_configuration_in_preview_and_save(self):
        self.save_monthly()
        before = self.snapshot()
        payload = self.payload(action='preview', weights={**WEIGHT_DEFAULTS, 'late': '2.00'})
        preview = rules.change_rules(self.request, payload)['preview']
        self.assertEqual(preview['classes'][0]['changed_count'], 0)
        self.assertEqual(self.snapshot(), before)
        payload = self.payload(weights={**WEIGHT_DEFAULTS, 'late': '2.00'})
        saved = rules.change_rules(self.request, payload)
        self.assertEqual(saved['policy']['monthly'], {'base': '75.00', 'minimum': '70.00', 'maximum': '80.00'})
        self.assertEqual(self.report()['rows'][0]['score'], '75.00')

    def test_explicit_null_ceiling_removes_only_the_ceiling(self):
        self.save_monthly()
        activity = Activity.objects.create(inclass=self.c, name='合成加分', activity_type='activity', occurred_on=date(2026, 9, 3))
        Report.objects.create(activity=activity, student=self.student, level='high')
        payload = self.payload({'base': '75.00', 'minimum': '70.00', 'maximum': None}, weights={**WEIGHT_DEFAULTS, 'high': '100.00'})
        result = rules.change_rules(self.request, payload)
        self.assertIsNone(result['policy']['monthly']['maximum'])
        self.assertEqual(self.report()['rows'][0]['months'][0]['score'], '175.00')

    def test_history_and_unvisited_terms_keep_then_effective_monthly_configuration(self):
        with business_day('2025-03-01'):
            self.save_monthly(base='65.00', minimum='10.00', maximum=None)
        self.save_monthly()
        for term in (calendar.Term(2025, 'autumn'), calendar.Term(2026, 'spring')):
            with self.subTest(term=term.key):
                policy = scoring.policy_for(self.owner.pk, term)
                self.assertEqual(policy['monthly'], {'base': '65.00', 'minimum': '10.00', 'maximum': None})
                self.assertEqual(self.report(term=term)['rows'][0]['score'], '65.00')
        future = calendar.Term(2027, 'spring')
        self.assertEqual(scoring.policy_for(self.owner.pk, future)['monthly']['base'], '75.00')
        with business_day('2027-02-01'):
            self.assertEqual(self.report(term=future)['rows'][0]['score'], '75.00')

    def test_average_display_places_are_versioned_and_do_not_change_month_scores(self):
        activity = Activity.objects.create(inclass=self.c, name='合成迟到', activity_type='class', occurred_on=date(2026, 9, 20))
        Report.objects.create(activity=activity, student=self.student, status='late')
        before = self.snapshot()
        preview = rules.change_rules(self.request,self.payload(action='preview',average_decimal_places=3))
        self.assertIn('preview',preview)
        self.assertEqual(self.snapshot(),before)
        saved = rules.change_rules(self.request,self.payload(average_decimal_places=3))
        self.assertEqual(saved['policy']['average_decimal_places'],3)
        report = self.report()
        self.assertEqual(report['rows'][0]['score'],'59.500')
        self.assertEqual([month['score'] for month in report['rows'][0]['months']],['59.00','60.00'])
        self.assertEqual(self.report(term=calendar.Term(2026,'spring'))['rows'][0]['score'],'60.00')
        self.assertEqual(scoring.policy_for(self.owner.pk,calendar.Term(2027,'spring'))['average_decimal_places'],3)
        with business_day('2027-02-01'):
            rules.change_rules(self.request,self.payload(average_decimal_places=1))
            self.assertEqual(self.report(term=self.term)['rows'][0]['score'],'59.800')
            self.assertEqual(self.report(term=calendar.Term(2027,'spring'))['rows'][0]['score'],'60.0')
        with self.assertRaises(BusinessError) as error:
            rules.change_rules(self.request,self.payload(average_decimal_places=5))
        self.assertEqual(error.exception.code,'invalid_average_decimal_places')

    def test_archived_payload_and_old_version_are_never_rewritten_by_new_monthly_config(self):
        with business_day('2026-03-01'):
            saved = self.save_monthly(base='65.00', minimum='0.00', maximum=None)
        version = ScoringPolicyVersion.objects.get(pk=saved['policy']['version'])
        old_values = deepcopy(version.monthly)
        archived = {'rows': [{'id': self.student.pk, 'score': '41.23'}],
                    'summary': {'student_count': 1}, 'policy': {'algorithm': 'legacy-baseline'},
                    'term_key': '2026-spring'}
        archive = ClassTerm.objects.create(inclass=self.c, owner=self.owner, term_key='2026-spring',
                                          policy=version, archived_data=archived)
        self.save_monthly()
        self.assertEqual(scoring.class_report(self.c, calendar.Term(2026, 'spring'),
            monthly={'base': '99.00', 'minimum': '0.00', 'maximum': None}), archived)
        archive.refresh_from_db(); version.refresh_from_db()
        self.assertEqual(archive.archived_data, archived)
        self.assertEqual(version.monthly, old_values)
        version.base_score = Decimal('66.00')
        with self.assertRaises(ValidationError):
            version.save()

    def test_invalid_monthly_does_not_create_baseline_submission_or_audit(self):
        for monthly in ({'base': '20.00', 'minimum': '30.00', 'maximum': None},
                        {'base': '80.00', 'minimum': '0.00', 'maximum': '60.00'}, None):
            with self.subTest(monthly=monthly):
                before = self.snapshot()
                payload = self.payload(); payload['monthly'] = monthly
                with self.assertRaises(BusinessError) as failure:
                    rules.change_rules(self.request, payload)
                self.assertEqual(failure.exception.code, 'invalid_monthly')
                self.assertEqual(self.snapshot(), before)

    def test_model_and_database_reject_invalid_monthly_order_and_range(self):
        for fields in ({'base_score': '5.00', 'minimum_score': '6.00'}, {'maximum_score': '59.00'},
                       {'base_score': '1000000.00'}, {'minimum_score': '-0.01'}, {'base_score': '60.001'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ScoringPolicyVersion.objects.create(owner=self.owner, effective_term_start=self.term.start, **fields)
        self.save_monthly()
        version = OwnerScoringSettings.objects.get(owner=self.owner).default_policy
        for values in ({'minimum_score': Decimal('76')}, {'maximum_score': Decimal('74')},
                       {'maximum_score': Decimal('1000000')}, {'minimum_score': Decimal('-1')}):
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                ScoringPolicyVersion.objects.filter(pk=version.pk).update(**values)

    def test_retry_with_same_payload_preserves_original_monthly_policy_and_one_audit(self):
        payload = self.payload({'base': '75.00', 'minimum': '70.00', 'maximum': '80.00'})
        first = rules.change_rules(self.request, payload)
        before = self.snapshot()
        retry = rules.change_rules(self.request, payload)
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['policy'], first['policy'])
        self.assertEqual(self.snapshot(), before)
        changed = deepcopy(payload); changed['monthly']['base'] = '76.00'
        with self.assertRaises(BusinessError) as failure:
            rules.change_rules(self.request, changed)
        self.assertEqual(failure.exception.code, 'idempotency_conflict')
        self.assertEqual(self.snapshot(), before)

    def test_audit_failure_rolls_back_new_config_and_revision(self):
        before = self.snapshot()
        with patch('manage.services.writes.AuditEvent.objects.create', side_effect=IntegrityError('synthetic')):
            with self.assertRaises(IntegrityError):
                self.save_monthly()
        self.assertEqual(self.snapshot(), before)

    def test_rules_http_exposes_monthly_and_rejects_invalid_fields(self):
        client = Client(); client.force_login(self.owner)
        current = client.get('/rules/?format=json')
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()['data']['policy']['monthly'], scoring.MONTHLY_DEFAULTS)
        payload = self.payload({'base': '75.00', 'minimum': '70.00', 'maximum': '80.00'})
        saved = client.post('/rules/', payload, content_type='application/json')
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()['data']['policy']['monthly'], payload['monthly'])
        payload = self.payload({'base': '75.00', 'minimum': '80.00', 'maximum': None})
        response = client.post('/rules/', payload, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['details']['field'], 'monthly.minimum')
