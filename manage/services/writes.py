import hashlib
import json
import time
import uuid
from django.db import OperationalError, transaction
from django.utils.crypto import salted_hmac
from manage.models import Submission, Activity, AuditEvent
from .errors import BusinessError


def atomic_write(fn):
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                with transaction.atomic():
                    return fn(*args, **kwargs)
            except OperationalError as exc:
                if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
                    raise
                if attempt == 2:
                    raise BusinessError('database_busy', '系统正在处理其他提交，请保留输入并重试。', 503)
                time.sleep(0.05 * (attempt + 1))
    return wrapped


def once(actor, classroom, scope, payload, apply):
    context = payload.get('actor_context')
    if context is not None and context != {'role':actor.role, 'id':actor.user_id}:
        raise BusinessError('actor_changed', '登录账号已变化，请使用原账号恢复提交。', 409)
    try:
        key = uuid.UUID(str(payload.get('submission_id', '')))
    except (ValueError, AttributeError):
        raise BusinessError('invalid_submission_id', '提交编号无效，请刷新页面后重试。')
    digest = salted_hmac('duxing.submission', json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False), algorithm='sha256').hexdigest()
    original = Submission.objects.filter(actor_key=actor.key, scope=scope, key=key).first()
    if original:
        if original.digest != digest:
            raise BusinessError('idempotency_conflict', '同一提交编号的内容已改变，请核对后新建提交。', 409)
        result = {**original.result, 'replayed': True}
        if scope.startswith('record:') and result.get('id'):
            result['deleted'] = not Activity.objects.filter(pk=result['id'], inclass=classroom).exists()
        return result
    result = apply()
    Submission.objects.create(actor_key=actor.key, inclass=classroom, scope=scope, key=key, digest=digest, result=result)
    return {**result, 'replayed': False}


def event(actor, classroom, kind, summary, count=0, object_id=None, revision=None):
    AuditEvent.objects.create(inclass=classroom, actor_role=actor.role, actor_id=actor.user_id,
        actor_label=(actor.display_name + ' · ' if actor.display_name else '') + actor.username,
        kind=kind, summary=summary, affected_count=count, object_id=object_id, revision=revision)


def require_revision(actual, supplied):
    if isinstance(supplied, bool) or supplied != actual:
        raise BusinessError('revision_conflict', '数据已被其他操作更新，请重新加载后核对。', 409, {'current_revision': actual})
