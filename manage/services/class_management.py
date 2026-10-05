"""Owner-only destructive class maintenance with current-term boundaries."""
from django.db.models import Q
from django.utils import timezone

from manage.models import (
    Activity, AuditEvent, Class, ClassDailyUsage, ClassTerm, CommitteeAccount,
    RosterVersion, Student, Submission, SummaryCount,
)
from . import calendar
from .access import actor_for, get_class, owner_actor, require_ready
from .errors import BusinessError
from .report_snapshots import mark_report_dirty
from .scoring import roster_for, freeze_class_term, validate_ended_archive
from .timestamps import beijing_iso
from .writes import atomic_write, event, once, require_revision


def _current_records(classroom, term):
    return Activity.objects.filter(
        inclass=classroom, occurred_on__gte=term.start, occurred_on__lt=term.end,
    )


def _term_month_filter(term):
    query = Q(pk__in=[])
    cursor = term.start
    while cursor < term.end:
        query |= Q(year=cursor.year, month=cursor.month)
        cursor = cursor.replace(
            year=cursor.year + (cursor.month == 12), month=cursor.month % 12 + 1,
        )
    return query


def _has_history(classroom, term):
    term_months = _term_month_filter(term)
    return (
        ClassTerm.objects.filter(inclass=classroom, archived_data__isnull=False).exists()
        or Activity.objects.filter(inclass=classroom).exclude(
            occurred_on__gte=term.start, occurred_on__lt=term.end,
        ).exists()
        or RosterVersion.objects.filter(
            inclass=classroom, effective_term_start__lt=term.start,
        ).exists()
        or SummaryCount.objects.filter(student__inclass=classroom).exclude(term_months).exists()
    )


def management_status(classroom, term):
    student_count = len(roster_for(classroom, term))
    record_count = _current_records(classroom, term).count()
    has_history = _has_history(classroom, term)
    return {
        'class_id': classroom.pk, 'term_key': term.key,
        'class_revision': classroom.revision, 'report_revision': classroom.report_revision,
        'freeze_date': calendar.business_today().isoformat(),
        'freeze_months': [f'{year:04d}-{month:02d}' for year, month in term.months()],
        'can_archive': not classroom.archived and not classroom.legacy_pending,
        'student_count': student_count,
        'record_count': record_count,
        'has_history': has_history,
        'can_clear_students': not classroom.archived and not classroom.legacy_pending and student_count > 0 and record_count == 0,
        'can_delete': not classroom.archived and not classroom.legacy_pending and student_count == 0 and record_count == 0 and not has_history,
    }


def _confirm_name(classroom, supplied):
    if not isinstance(supplied, str) or supplied.strip() != classroom.classname:
        raise BusinessError('confirmation_mismatch', '班级名称不一致，操作未执行。', 409)


@atomic_write
def manage_class(request, code, payload):
    action = payload.get('action')
    try:
        classroom = get_class(code)
    except BusinessError as missing:
        # A network retry after a successful physical deletion can still recover
        # the original receipt stored without a class foreign key.
        if action != 'delete_class' or not str(code).isascii() or not str(code).isdigit():
            raise
        actor = owner_actor(request)
        return once(actor, None, f'class-management:{int(code)}:delete_class', payload,
                    lambda: (_ for _ in ()).throw(missing))
    actor = actor_for(request, classroom, owner_only=True)
    if action not in ('clear_data', 'clear_students', 'delete_class', 'archive'):
        raise BusinessError('invalid_action', '不支持此班级管理操作。')

    def apply():
        # Refetch after acquiring the serialized write boundary; an earlier
        # page's class object is never authorization or confirmation evidence.
        classroom.refresh_from_db()
        require_ready(classroom)
        term = calendar.writable_term(payload.get('term_key'))
        _confirm_name(classroom, payload.get('confirmation'))
        require_revision(classroom.revision, payload.get('revision'))
        if (type(payload.get('report_revision')) is not int or
                payload['report_revision'] != classroom.report_revision):
            raise BusinessError('management_scope_changed', '业务数据已变化，请重新预览并确认操作范围。', 409)
        status = management_status(classroom, term)

        if action == 'archive':
            if payload.get('freeze_date') != status['freeze_date']:
                raise BusinessError('management_scope_changed', '结束管理日期已变化，请重新预览冻结范围。', 409)
            freeze_day = calendar.business_today()
            if freeze_day.isoformat() != status['freeze_date']:
                raise BusinessError('management_scope_changed', '结束管理日期已变化，请重新预览冻结范围。', 409)
            freeze_class_term(classroom, term, today=freeze_day)
            if calendar.business_today() != freeze_day:
                raise BusinessError('management_scope_changed', '结束管理期间日期发生切换，请重新预览冻结范围。', 409)
            classroom.archived = True
            classroom.ended_at = timezone.now()
            classroom.ended_term_key = term.key
            classroom.revision += 1
            classroom.save(update_fields=['archived', 'ended_at', 'ended_term_key', 'revision'])
            validate_ended_archive(classroom)
            event(actor, classroom, 'class_archived',
                  f'结束班级管理，冻结{term.label}截至{status["freeze_date"]}的名单、记录与成绩',
                  status['student_count'], revision=classroom.revision)
            mark_report_dirty(classroom)
            return {'action': action, 'revision': classroom.revision,
                    'ended_at': beijing_iso(classroom.ended_at), 'ended_term_key': term.key,
                    'url': f'/classes/{code}/?term={term.key}'}

        if action == 'clear_data':
            if status['record_count'] == 0:
                raise BusinessError('nothing_to_clear', '当前学期没有可清空的业务记录。', 409)
            student_ids = Student.objects.filter(inclass=classroom).values_list('id', flat=True)
            deleted_count = status['record_count']
            _current_records(classroom, term).delete()
            SummaryCount.objects.filter(student_id__in=student_ids).filter(
                _term_month_filter(term),
            ).delete()
            classroom.revision += 1
            classroom.save(update_fields=['revision'])
            event(actor, classroom, 'class_data_cleared',
                  f'清空{term.label}业务数据，共{deleted_count}条记录；学生名单与历史学期保持不变',
                  deleted_count, revision=classroom.revision)
            mark_report_dirty(classroom)
            return {'action': action, 'record_count': deleted_count,
                    'revision': classroom.revision, 'url': f'/classes/{code}/roster/'}

        if action == 'clear_students':
            if status['record_count']:
                raise BusinessError('class_has_records', '请先清空当前学期数据，再清空学生。', 409,
                                    {'record_count': status['record_count']})
            if status['student_count'] == 0:
                raise BusinessError('nothing_to_clear', '当前学期没有可清空的学生。', 409)
            current_rows = roster_for(classroom, term)
            student_ids = [row['id'] for row in current_rows]
            if status['has_history']:
                students = list(Student.objects.filter(pk__in=student_ids))
                for student in students:
                    student.active = False
                    student.revision += 1
                Student.objects.bulk_update(students, ['active', 'revision'])
                for student in students:
                    RosterVersion.objects.update_or_create(
                        student=student, effective_term_start=term.start,
                        defaults={'inclass': classroom, 'number': student.number,
                                  'name': student.name, 'sex': student.sex, 'active': False},
                    )
            else:
                SummaryCount.objects.filter(student_id__in=student_ids).delete()
                RosterVersion.objects.filter(student_id__in=student_ids).delete()
                Student.objects.filter(pk__in=student_ids).delete()
            classroom.revision += 1
            classroom.save(update_fields=['revision'])
            event(actor, classroom, 'class_students_cleared',
                  f'清空{term.label}学生名单，共{status["student_count"]}人；历史学期保持不变',
                  status['student_count'], revision=classroom.revision)
            mark_report_dirty(classroom)
            return {'action': action, 'student_count': status['student_count'],
                    'revision': classroom.revision, 'url': f'/classes/{code}/roster/'}

        if status['record_count'] or status['student_count']:
            raise BusinessError('class_not_empty', '删除班级前需先清空当前学期数据和学生。', 409,
                                {'record_count': status['record_count'],
                                 'student_count': status['student_count']})
        if status['has_history']:
            raise BusinessError('class_has_history', '该班级已有历史学期资料，只能保留归档，不能删除。', 409)
        # An empty, history-free class can be removed completely. Supporting
        # access and audit rows are scoped to this class and have no business history.
        student_ids = Student.objects.filter(inclass=classroom).values_list('id', flat=True)
        SummaryCount.objects.filter(student_id__in=student_ids).delete()
        RosterVersion.objects.filter(student_id__in=student_ids).delete()
        Student.objects.filter(inclass=classroom).delete()
        Submission.objects.filter(inclass=classroom).delete()
        AuditEvent.objects.filter(inclass=classroom).delete()
        ClassDailyUsage.objects.filter(inclass=classroom).delete()
        CommitteeAccount.objects.filter(inclass=classroom).delete()
        ClassTerm.objects.filter(inclass=classroom).delete()
        classroom.delete()
        return {'action': action, 'deleted': True, 'url': '/'}

    scope = f'class-management:{classroom.pk}:{action}'
    return once(actor, None if action == 'delete_class' else classroom, scope, payload, apply)
