"""Ranking semantics for the selected class term."""
from django.test import SimpleTestCase

from manage.services.scoring import class_top_three


class ClassTopThreeTests(SimpleTestCase):
    def test_five_rankings_count_people_not_scores_and_exclude_zero(self):
        rows = [
            {'id': 2, 'name': '乙', 'number': '002', 'sex': 'female',
             'counts': {'absent': 1, 'late': 0, 'leave': 0, 'low': 1, 'mid': 0, 'high': 1,
                        'dlow': 0, 'dmid': 0, 'dhigh': 1}},
            {'id': 1, 'name': '甲', 'number': '001', 'sex': 'male',
             'counts': {'absent': 2, 'late': 1, 'leave': 0, 'low': 0, 'mid': 1, 'high': 0,
                        'dlow': 1, 'dmid': 1, 'dhigh': 0}},
            {'id': 3, 'name': '丙', 'number': '003', 'sex': 'female',
             'counts': {'absent': 2, 'late': 0, 'leave': 1, 'low': 1, 'mid': 0, 'high': 0,
                        'dlow': 0, 'dmid': 0, 'dhigh': 0}},
            {'id': 4, 'name': '丁', 'number': '004', 'sex': 'male',
             'counts': {'absent': 0, 'late': 0, 'leave': 0, 'low': 0, 'mid': 0, 'high': 0,
                        'dlow': 0, 'dmid': 0, 'dhigh': 0}},
        ]
        groups = {item['key']: [(person['number'], person['count']) for person in item['people']]
                  for item in class_top_three(rows)}
        self.assertEqual(groups, {
            'absent': [('001', 2), ('003', 2), ('002', 1)],
            'late': [('001', 1)],
            'leave': [('003', 1)],
            'activity': [('002', 2), ('001', 1), ('003', 1)],
            'discipline': [('001', 2), ('002', 1)],
        })

    def test_equal_counts_follow_natural_student_number_order(self):
        rows = [{'id': index, 'number': number, 'name': number,
                 'counts': {'late': 1}} for index, number in enumerate(('10', '2', '1'), 1)]
        late = next(item for item in class_top_three(rows) if item['key'] == 'late')
        self.assertEqual([person['number'] for person in late['people']], ['1', '2', '10'])
