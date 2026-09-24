"""Current-term public reports, refreshed after commits and at calendar boundaries."""
import hashlib
import json
import time

from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from manage.models import Class, CurrentClassReport, WEIGHT_DEFAULTS
from . import calendar
from .errors import BusinessError
from .scoring import class_report


SCHEMA_VERSION = 1


def report_identity(today):
    term = calendar.term_for_date(today)
    return (term.key if term else '', today.strftime('%Y-%m'))


def public_row(row, score=None, counts=None):
    return {'name': row['name'], 'number': row['number'], 'sex': row.get('sex'),
            'score': row['score'] if score is None else score,
            'counts': {key: (row['counts'] if counts is None else counts)[key]
                       for key in WEIGHT_DEFAULTS}}


def build_report_payload(classroom, today):
    """Project only the public score table; exclude internal IDs and activity facts."""
    term = calendar.term_for_date(today)
    payload = {'class_name': classroom.classname, 'term': None, 'tables': []}
    if term is None:
        return payload
    payload['term'] = {'key': term.key, 'label': term.label}
    try:
        report = class_report(classroom, term, today=today)
    except BusinessError as exc:
        if exc.code != 'legacy_baseline_pending':
            raise
        report = {'rows': []}
    rows = report['rows']
    payload['tables'].append({'key': 'term', 'label': '学期汇总',
                              'score_label': '学期分数',
                              'rows': [public_row(row) for row in rows]})
    for year, month in term.months(today):
        key = f'{year:04d}-{month:02d}'
        monthly_rows = []
        for row in rows:
            cell = next((item for item in row['months'] if item['key'] == key), None)
            if cell:
                monthly_rows.append(public_row(row, cell['score'], cell['counts']))
        payload['tables'].append({'key': key, 'label': f'{year}年{month}月',
                                  'score_label': '月分', 'rows': monthly_rows})
    return payload


def payload_digest(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def is_current(snapshot, classroom, today):
    term_key, month_key = report_identity(today)
    return bool(snapshot and snapshot.term_key == term_key and snapshot.month_key == month_key
                and snapshot.source_revision == classroom.report_revision
                and snapshot.schema_version == SCHEMA_VERSION
                and snapshot.payload_digest == payload_digest(snapshot.payload))


def _refresh_once(class_id, today, verify_content):
    # SQLite's IMMEDIATE transaction serializes competing generators before
    # either can read a stale snapshot. The replacement commits atomically.
    with transaction.atomic():
        classroom = Class.objects.get(pk=class_id)
        snapshot = CurrentClassReport.objects.filter(inclass_id=class_id).first()
        current = is_current(snapshot, classroom, today)
        if current and not verify_content:
            return snapshot, False
        payload = build_report_payload(classroom, today)
        digest = payload_digest(payload)
        if current and snapshot.payload_digest == digest:
            return snapshot, False
        term_key, month_key = report_identity(today)
        if snapshot is None:
            snapshot = CurrentClassReport(inclass=classroom)
        snapshot.term_key = term_key
        snapshot.month_key = month_key
        snapshot.source_revision = classroom.report_revision
        snapshot.schema_version = SCHEMA_VERSION
        snapshot.payload_digest = digest
        snapshot.payload = payload
        snapshot.generated_at = timezone.now()
        snapshot.save()
        return snapshot, True


def refresh_report(class_id, today=None, verify_content=False):
    today = today or calendar.business_today()
    for attempt in range(3):
        try:
            return _refresh_once(class_id, today, verify_content)
        except OperationalError as exc:
            if not any(word in str(exc).lower() for word in ('locked', 'busy')):
                raise
            if attempt == 2:
                raise BusinessError('report_busy', '报告正在更新，请稍后再试。', 503) from exc
            time.sleep(0.05 * (attempt + 1))


def get_current_report(classroom, today=None):
    today = today or calendar.business_today()
    classroom = Class.objects.only('id', 'report_revision').get(pk=classroom.pk)
    snapshot = CurrentClassReport.objects.filter(inclass=classroom).first()
    if is_current(snapshot, classroom, today):
        return snapshot
    return refresh_report(classroom.pk, today)[0]


def mark_report_dirty(classroom):
    """Call inside the successful write transaction, never on preview/replay."""
    Class.objects.filter(pk=classroom.pk).update(report_revision=F('report_revision') + 1)
    transaction.on_commit(lambda class_id=classroom.pk: refresh_report(class_id), robust=True)
