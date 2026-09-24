"""Read monthly details from each term's own roster and scoring report.

Archived reports are evidence: a missing monthly breakdown stays unknown.
This adapter never rebuilds a score or extends a student's historical roster.
"""
import re
from datetime import date, timedelta

from django.db import transaction

from manage.models import WEIGHT_DEFAULTS
from . import calendar
from .errors import BusinessError
from .scoring import class_report


def month_key(start):
    return f'{start.year:04d}-{start.month:02d}'


def month_label(start):
    return f'{start.year}年{start.month}月'


def next_month(start):
    return date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def previous_month(start):
    return (start - timedelta(days=1)).replace(day=1)


def selected_month(value, term, today=None):
    """Validate an explicitly selected calendar month before reading its data."""
    today = today or calendar.business_today()
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', value):
        raise BusinessError('invalid_month', '月份格式需为 YYYY-MM。')
    try:
        start = date(int(value[:4]), int(value[5:]), 1)
    except ValueError:
        raise BusinessError('invalid_month', '月份格式无效。') from None
    if not term.contains(start):
        raise BusinessError('month_outside_term', '所选月份不属于该学期。')
    if start > today:
        raise BusinessError('future_month', '该月份尚未开始，不能提前查看分数。')
    return start


def score_base(report):
    # The original archived algorithm had a fixed monthly base of 60.
    return report.get('policy', {}).get('monthly', {}).get('base', '60.00')


def month_cell(classroom, student_id, start, report=None, today=None):
    today = today or calendar.business_today()
    term = calendar.term_for_date(start)
    result = {'key': month_key(start), 'score': None, 'counts': None,
              'score_base': None, 'url': None, 'reason': ''}
    if term is None:
        result['reason'] = '8月不计分'
        return result
    if start > today:
        result['reason'] = '月份尚未开始'
        return result
    report = report if report is not None else class_report(classroom, term, today=today)
    student = next((row for row in report.get('rows', []) if row['id'] == student_id), None)
    if student is None:
        result['reason'] = '该学期未在册'
        return result
    detail = next((item for item in student.get('months', []) if item.get('key') == result['key']), None)
    if (not detail or detail.get('score') is None or not isinstance(detail.get('counts'), dict)
            or not set(WEIGHT_DEFAULTS).issubset(detail['counts'])):
        result['reason'] = '历史月份明细待核实' if term.end <= today else '月份明细待核实'
        return result
    result.update(score=detail['score'], counts={key: detail['counts'][key] for key in WEIGHT_DEFAULTS},
                  score_base=score_base(report),
                  url=f'/classes/{classroom.code}/students/{student_id}/?term={term.key}&month={result["key"]}')
    return result


@transaction.atomic
def monthly_overview(classroom, term, report=None, today=None):
    """Return two preloaded month columns aligned to the selected term's roster."""
    today = today or calendar.business_today()
    report = report if report is not None else class_report(classroom, term, today=today)
    if term == calendar.display_term(today):
        anchor = today.replace(day=1)
        labels = ('本月', '上月')
        caption = '按所选学期名单查看本月与上月；跨学期月份使用当时名单和评分依据。'
    else:
        anchor = previous_month(term.end)
        labels = ('期末月', '前一月')
        caption = ('所选学期尚未开始，月份不提前计分。' if term.start > today else
                   '按所选学期名单查看期末两个月；历史月份沿用已保存的评分依据。')
    starts = (anchor, previous_month(anchor))
    reports = {term.key: report}
    descriptors = []
    for start, label in zip(starts, labels):
        month_term = calendar.term_for_date(start)
        available = month_term is not None and start <= today
        descriptors.append({'key': month_key(start), 'label': f'{label} · {month_label(start)}',
                            'term_key': month_term.key if month_term else None, 'available': available})
        if available and month_term.key not in reports:
            reports[month_term.key] = class_report(classroom, month_term, today=today)
    rows = []
    for identity in report.get('rows', []):
        months = []
        for start in starts:
            month_term = calendar.term_for_date(start)
            months.append(month_cell(classroom, identity['id'], start,
                report=reports.get(month_term.key) if month_term else None, today=today))
        rows.append({'id': identity['id'], 'number': identity['number'], 'name': identity['name'], 'months': months})
    return {'caption': caption, 'months': descriptors, 'rows': rows}


def term_monthly_overview(classroom, term, report=None, today=None):
    """Every elapsed month in the selected term, without crossing term boundaries."""
    today = today or calendar.business_today()
    report = report if report is not None else class_report(classroom, term, today=today)
    starts = [date(year, month, 1) for year, month in term.months(today)]
    months = [{'key': month_key(start), 'label': month_label(start), 'term_key': term.key,
               'available': True} for start in starts]
    rows = [{'id': row['id'], 'number': row['number'], 'name': row['name'],
             'sex': row.get('sex'),
             'months': [month_cell(classroom, row['id'], start, report=report, today=today)
                        for start in starts]} for row in report.get('rows', [])]
    return {'caption': '按所选学期逐月查看；无活动的自然月也按当期规则计分。',
            'months': months, 'rows': rows}


def student_month(classroom, student_id, start, report, today=None):
    result = month_cell(classroom, student_id, start, report=report, today=today)
    return {**result, 'label': month_label(start), 'available': result['score'] is not None,
            'term_key': calendar.term_for_date(start).key}
