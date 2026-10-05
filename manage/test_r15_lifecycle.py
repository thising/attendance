"""Adversarial lifecycle/range regressions using synthetic, isolated data."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from manage.models import Activity, AuditEvent, Class, ClassTerm, Report, RosterVersion, Student, Submission, WEIGHT_DEFAULTS
from manage.services import calendar, class_management, monthly, records, roster, rules, scoring
from manage.services.errors import BusinessError
from manage.services.report_snapshots import build_report_payload, get_current_report, refresh_report
from manage.test_domain import business_day


class LifecycleTests(TestCase):
    def setUp(self):
        clock = business_day('2026-10-05')
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.owner = get_user_model().objects.create_user('lifecycle-synthetic-owner')
        self.request = SimpleNamespace(user=self.owner, session={})
        self.term = calendar.Term(2026, 'autumn')
        self.c = Class.objects.create(owner=self.owner, classname='合成生命周期班', started_on=self.term.start)
        self.student = Student.objects.create(inclass=self.c, number='SYN-001', name='合成学生')
        RosterVersion.objects.create(student=self.student, inclass=self.c, effective_term_start=self.term.start,
                                    number=self.student.number, name=self.student.name, sex=self.student.sex)

    def management_payload(self, action='archive', **changes):
        self.c.refresh_from_db()
        status = class_management.management_status(self.c, self.term)
        return {'action': action, 'confirmation': self.c.classname, 'term_key': self.term.key,
                'revision': status['class_revision'], 'report_revision': status['report_revision'],
                'freeze_date': status['freeze_date'], 'submission_id': str(uuid4()), **changes}

    def record_payload(self, value='late', **changes):
        self.c.refresh_from_db()
        return {'term_key': self.term.key, 'submission_id': str(uuid4()), 'revision': 0,
                'roster_revision': self.c.revision,
                'record': {'kind': 'class', 'name': '合成点名', 'date': '2026-09-15',
                           'students': [{'id': self.student.pk, 'value': value}]}, **changes}

    def rule_payload(self, **changes):
        policy = scoring.policy_for(self.owner.pk, self.term)
        return {'term_key': self.term.key, 'submission_id': str(uuid4()), 'revision': policy['revision'],
                'action': 'save', 'weights': policy['weights'], **changes}

    def assert_business_error(self, code, fn):
        with self.assertRaises(BusinessError) as error:
            fn()
        self.assertEqual(error.exception.code, code)

    def test_old_destructive_preview_rejects_added_edited_and_deleted_facts(self):
        saved = records.save_record(self.request, self.c.code, self.record_payload())
        old = self.management_payload('clear_data')
        extra = records.save_record(self.request, self.c.code, self.record_payload('absent'))
        self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, old))
        self.assertEqual(Activity.objects.filter(inclass=self.c).count(), 2)
        old = self.management_payload('clear_data')
        records.save_record(self.request, self.c.code, self.record_payload('leave', revision=saved['revision']), saved['id'])
        self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, old))
        old = self.management_payload('clear_data')
        records.save_record(self.request, self.c.code, self.record_payload(action='delete', revision=extra['revision']), extra['id'])
        self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, old))
        self.assertEqual(Activity.objects.filter(inclass=self.c).count(), 1)

    def test_scope_version_is_required_and_rule_updates_invalidate_archive_preview(self):
        payload = self.management_payload()
        del payload['report_revision']
        self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, payload))
        old = self.management_payload()
        rules.change_rules(self.request, self.rule_payload(weights={**WEIGHT_DEFAULTS, 'late': '2.00'}))
        self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, old))
        self.c.refresh_from_db()
        self.assertFalse(self.c.archived)

    def test_midnight_preview_rejected_without_any_business_write(self):
        with business_day('2026-09-30'):
            old = self.management_payload()
        with business_day('2026-10-01'):
            self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, old))
        self.assertFalse(ClassTerm.objects.filter(inclass=self.c).exists())

    def test_archive_freezes_all_facts_months_policy_and_roster(self):
        saved = records.save_record(self.request, self.c.code, self.record_payload())
        payload = self.management_payload()
        result = class_management.manage_class(self.request, self.c.code, payload)
        self.c.refresh_from_db()
        self.assertTrue(self.c.archived)
        self.assertEqual(self.c.ended_term_key, self.term.key)
        frozen = deepcopy(scoring.class_report(self.c, self.term))
        self.assertEqual(frozen['month_keys'], ['2026-09', '2026-10'])
        self.assertEqual(frozen['rows'][0]['score'], '59.50')
        self.assertEqual(frozen['frozen_reason'], 'class-ended')
        self.assertTrue(frozen['frozen_at'].endswith('+08:00'))
        self.assertTrue(frozen['activities'][0]['time'].endswith('+08:00'))
        self.assertEqual(frozen['record_roster'][0]['name'], '合成学生')
        self.assertEqual(frozen['activities'][0]['id'], saved['id'])
        # Even out-of-band edits cannot cause a frozen read to consult mutable facts.
        Student.objects.filter(pk=self.student.pk).update(name='不可进入归档的新姓名')
        Report.objects.filter(activity_id=saved['id']).update(status='absent')
        rules.change_rules(self.request, self.rule_payload(weights={**WEIGHT_DEFAULTS, 'late': '8.00'}))
        self.assertFalse(AuditEvent.objects.filter(inclass=self.c, kind='rules_updated').exists())
        for day in ('2026-11-01', '2027-02-01'):
            with business_day(day):
                self.assertEqual(scoring.class_report(self.c, self.term), frozen)
                self.assertEqual(monthly.term_monthly_overview(self.c, self.term)['months'][1]['key'], '2026-10')
                self.assertEqual(len(monthly.term_monthly_overview(self.c, self.term)['months']), 2)
        with business_day('2027-02-01'):
            future = calendar.Term(2027, 'spring')
            self.assertEqual(scoring.roster_for(self.c, future), [])
            self.assertEqual(scoring.class_report(self.c, future)['rows'], [])
            self.assertFalse(ClassTerm.objects.filter(inclass=self.c, term_key=future.key).exists())
            replay = class_management.manage_class(self.request, self.c.code, payload)
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['ended_at'], result['ended_at'])
        self.assertEqual(AuditEvent.objects.filter(inclass=self.c, kind='class_archived').count(), 1)

    def test_calendar_boundary_during_freeze_rolls_back_the_unconfirmed_month(self):
        payload = self.management_payload()
        original = scoring.snapshot_activities
        with patch('manage.services.calendar.business_today', return_value=date(2026, 10, 5)) as clock:
            def change_day(classroom, term):
                result = original(classroom, term)
                clock.return_value = date(2026, 11, 1)
                return result
            with patch('manage.services.scoring.snapshot_activities', side_effect=change_day):
                self.assert_business_error('management_scope_changed', lambda: class_management.manage_class(self.request, self.c.code, payload))
        self.c.refresh_from_db()
        self.assertFalse(self.c.archived)
        self.assertFalse(ClassTerm.objects.filter(inclass=self.c).exists())

    def test_unknown_sex_is_frozen_without_guessing_or_blocking_class_ending(self):
        Student.objects.filter(pk=self.student.pk).update(sex='unknown')
        RosterVersion.objects.filter(student=self.student).update(sex='unknown')
        class_management.manage_class(self.request, self.c.code, self.management_payload())
        self.c.refresh_from_db()
        frozen = scoring.validate_ended_archive(self.c)
        self.assertEqual(frozen['rows'][0]['sex'], 'unknown')
        self.assertEqual(frozen['record_roster'][0]['sex'], 'unknown')
        self.assertEqual(scoring.class_report(self.c, self.term)['rows'][0]['sex'], 'unknown')

    def test_class_ending_keeps_existing_migration_provenance(self):
        origin = 'vps207:' + 'a' * 64
        ClassTerm.objects.create(inclass=self.c, owner=self.owner, term_key=self.term.key,
                                 baseline_source=origin)
        class_management.manage_class(self.request, self.c.code, self.management_payload())
        self.assertEqual(ClassTerm.objects.get(inclass=self.c).baseline_source, origin)

    def test_natural_term_end_keeps_existing_migration_provenance(self):
        origin = 'vps207:' + 'b' * 64
        ClassTerm.objects.create(inclass=self.c, owner=self.owner, term_key=self.term.key,
                                 baseline_source=origin)
        with business_day('2027-02-01'):
            scoring.class_report(self.c, self.term)
        self.assertEqual(ClassTerm.objects.get(inclass=self.c).baseline_source, origin)

    def test_archive_transaction_rolls_back_on_snapshot_failure(self):
        payload = self.management_payload()
        with patch('manage.services.scoring.snapshot_activities', side_effect=RuntimeError('synthetic freeze failure')):
            with self.assertRaises(RuntimeError):
                class_management.manage_class(self.request, self.c.code, payload)
        self.c.refresh_from_db()
        self.assertFalse(self.c.archived)
        self.assertIsNone(self.c.ended_at)
        self.assertFalse(ClassTerm.objects.filter(inclass=self.c).exists())
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_old_archive_route_cannot_bypass_confirmation_contract(self):
        self.assert_business_error('legacy_archive_retired', lambda: roster.change_roster(self.request, self.c.code,
                                                                                     self.management_payload()))
        self.c.refresh_from_db()
        self.assertFalse(self.c.archived)

    def test_archived_writes_and_wrong_owner_receipt_replay_are_rejected(self):
        payload = self.management_payload()
        class_management.manage_class(self.request, self.c.code, payload)
        self.assert_business_error('class_archived', lambda: records.save_record(self.request, self.c.code, self.record_payload()))
        self.assert_business_error('class_archived', lambda: roster.change_roster(self.request, self.c.code,
            {'action': 'add', 'term_key': self.term.key, 'revision': self.c.revision, 'submission_id': str(uuid4()),
             'student': {'number': 'SYN-002', 'name': '合成二号', 'sex': 'female'}}))
        other = SimpleNamespace(user=get_user_model().objects.create_user('foreign-synthetic-owner'), session={})
        self.assert_business_error('permission_denied', lambda: class_management.manage_class(other, self.c.code, payload))

    def test_unknown_old_archival_cannot_create_or_reuse_unverified_scores(self):
        refresh_report(self.c.pk)
        Class.objects.filter(pk=self.c.pk).update(archived=True)
        for read in (lambda: scoring.class_report(self.c, self.term), lambda: scoring.roster_for(self.c, self.term),
                     lambda: get_current_report(self.c), lambda: refresh_report(self.c.pk)):
            self.assert_business_error('class_archive_unverified', read)
        self.assertFalse(ClassTerm.objects.filter(inclass=self.c).exists())

    def test_public_report_keeps_frozen_month_range_and_never_exposes_past_term(self):
        class_management.manage_class(self.request, self.c.code, self.management_payload())
        self.c.refresh_from_db()
        payload = build_report_payload(self.c, date(2026, 11, 1))
        self.assertEqual([table['key'] for table in payload['tables']], ['term', '2026-09', '2026-10'])
        for day in (date(2027, 2, 1), date(2027, 8, 1)):
            payload = build_report_payload(self.c, day)
            self.assertEqual(payload['tables'], [])
            self.assertIsNone(payload['term'])

    def test_lost_final_snapshot_is_never_recomputed_even_from_valid_metadata(self):
        class_management.manage_class(self.request, self.c.code, self.management_payload())
        ClassTerm.objects.filter(inclass=self.c).update(archived_data=None)
        for term in (self.term, calendar.Term(2027, 'spring')):
            self.assert_business_error('class_archive_unverified', lambda: scoring.class_report(self.c, term))
        self.assertIsNone(ClassTerm.objects.get(inclass=self.c).archived_data)

    def test_corrupted_ended_snapshot_is_fail_closed_for_reads_roster_and_cache(self):
        records.save_record(self.request, self.c.code, self.record_payload())
        class_management.manage_class(self.request, self.c.code, self.management_payload())
        self.c.refresh_from_db()
        refresh_report(self.c.pk)
        baseline = deepcopy(scoring.validate_ended_archive(self.c))
        malformed = []
        for field in ('term_key', 'month_keys', 'policy', 'rows', 'activities', 'record_roster'):
            candidate = deepcopy(baseline)
            del candidate[field]
            malformed.append(candidate)
        candidate = deepcopy(baseline)
        candidate['month_keys'].append('2026-11')
        malformed.append(candidate)
        candidate = deepcopy(baseline)
        candidate['frozen_at'] = 'invalid'
        malformed.append(candidate)
        candidate = deepcopy(baseline)
        candidate['activities'][0]['student_values']['999999'] = 'late'
        malformed.append(candidate)
        candidate = deepcopy(baseline)
        candidate['policy']['weights']['late'] = 'NaN'
        malformed.append(candidate)
        candidate = deepcopy(baseline)
        candidate['rows'][0]['months'][0]['counts']['late'] = -1
        malformed.append(candidate)
        malformed.append({'frozen_at': 'x'})
        for document in malformed:
            ClassTerm.objects.filter(inclass=self.c).update(archived_data=document)
            for read in (lambda: scoring.validate_ended_archive(self.c),
                         lambda: scoring.class_report(self.c, self.term),
                         lambda: scoring.roster_for(self.c, self.term),
                         lambda: get_current_report(self.c), lambda: refresh_report(self.c.pk)):
                self.assert_business_error('class_archive_unverified', read)
            self.assertEqual(ClassTerm.objects.get(inclass=self.c).archived_data, document)

    def test_preview_compares_exact_months_even_when_display_masks_the_change(self):
        rules.change_rules(self.request, self.rule_payload(weights={**WEIGHT_DEFAULTS, 'late': '0.00'}, average_decimal_places=0))
        records.save_record(self.request, self.c.code, self.record_payload())
        result = rules.change_rules(self.request, self.rule_payload(action='preview', weights={**WEIGHT_DEFAULTS, 'late': '0.50'}))['preview']
        row = result['classes'][0]
        self.assertEqual((row['changed_count'], row['average_changed_count'], row['display_changed_count']), (1, 1, 0))
        self.assertEqual(row['max_absolute_change'], '0.25')
        self.assertFalse(result['display_changed'])

    def test_preview_counts_month_changes_that_cancel_in_term_average(self):
        rules.change_rules(self.request, self.rule_payload(weights={**WEIGHT_DEFAULTS, 'late': '0.00', 'low': '0.00'}))
        records.save_record(self.request, self.c.code, self.record_payload())
        activity = Activity.objects.create(inclass=self.c, name='合成活动', activity_type='activity', occurred_on=date(2026, 10, 1))
        Report.objects.create(activity=activity, student=self.student, level='low')
        row = rules.change_rules(self.request, self.rule_payload(action='preview', weights={**WEIGHT_DEFAULTS, 'late': '0.50', 'low': '0.50'}))['preview']['classes'][0]
        self.assertEqual((row['changed_count'], row['average_changed_count'], row['max_absolute_change']), (1, 0, '0.00'))

    def test_display_precision_is_distinct_from_score_changes(self):
        result = rules.change_rules(self.request, self.rule_payload(action='preview', average_decimal_places=0))['preview']
        self.assertTrue(result['display_changed'])
        row = result['classes'][0]
        self.assertEqual((row['changed_count'], row['average_changed_count']), (0, 0))
        self.assertEqual(row['display_changed_count'], 1)
