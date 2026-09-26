"""Owner-only destructive class maintenance with current-term boundaries."""
from django.db.models import Q

from manage.models import (
    Activity, AuditEvent, ClassDailyUsage, ClassTerm, CommitteeAccount,
    RosterVersion, Student, Submission, SummaryCount,
)
from . import calendar
from .access import actor_for, get_class, owner_actor, require_ready
from .errors import BusinessError
from .report_snapshots import mark_report_dirty
from .scoring import roster_for
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
        'student_count': student_count,
        'record_count': record_count,
        'has_history': has_history,
        'can_clear_students': student_count > 0 and record_count == 0,
        'can_delete': student_count == 0 and record_count == 0 and not has_history,
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
    require_ready(classroom)
    term = calendar.writable_term(payload.get('term_key'))
    _confirm_name(classroom, payload.get('confirmation'))

    if action not in ('clear_data', 'clear_students', 'delete_class'):
        raise BusinessError('invalid_action', '不支持此班级管理操作。')

    def apply():
        require_revision(classroom.revision, payload.get('revision'))
        status = management_status(classroom, term)

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
