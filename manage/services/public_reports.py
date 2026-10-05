"""Revocable, versioned bearer links for current-term read-only reports."""
import hashlib
import io
import secrets

import segno
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from manage.models import PublicClassReportLink
from .access import actor_for, get_class
from .credential_store import cipher
from .errors import BusinessError
from .writes import atomic_write, event, once, require_revision
from .report_snapshots import refresh_report


def token_digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


@sensitive_variables()
def token_for(link):
    try:
        document = cipher().decrypt(link.token_ciphertext.encode()).decode('ascii')
        class_id, token = document.split(':', 1)
        if int(class_id) != link.inclass_id or token_digest(token) != link.token_digest:
            raise ValueError
        return token
    except (InvalidToken, ValueError, UnicodeError, AttributeError):
        raise BusinessError('public_link_unavailable', '公开链接无法读取，请检查密钥备份。', 503) from None


def link_details(request, classroom):
    actor = actor_for(request, classroom)
    link = PublicClassReportLink.objects.filter(inclass=classroom).first()
    active = bool(link and link.active)
    status = 'active' if active else 'owner_disabled' if link else 'never_enabled'
    details = {'active': active, 'status': status, 'revision': link.revision if link else 0,
               'can_enable': not active and (not link or actor.role == 'owner'),
               'can_rotate': active and actor.role == 'owner',
               'can_disable': active and actor.role == 'owner',
               'url': None, 'qr_svg': None, 'qr_data_uri': None}
    if not active:
        return details
    path = f'/report/{token_for(link)}/'
    url = settings.DUXING_PUBLIC_BASE_URL + path if settings.DUXING_PUBLIC_BASE_URL else request.build_absolute_uri(path)
    qr=segno.make(url, error='m')
    stream=io.BytesIO()
    qr.save(stream,kind='svg',scale=3,border=2,xmldecl=False)
    return {**details, 'url': url, 'qr_svg': stream.getvalue().decode('utf-8'),
            'qr_data_uri': qr.svg_data_uri(scale=3,border=2)}


@sensitive_variables()
@atomic_write
def change_link(request, classroom, payload):
    classroom = get_class(classroom.pk)
    actor = actor_for(request, classroom)
    action = payload.get('action')
    if action not in ('enable', 'rotate', 'disable'):
        raise BusinessError('invalid_action', '公开报告操作无效。')
    if action in ('rotate', 'disable') and actor.role != 'owner':
        raise BusinessError('forbidden', '仅班主任可更换或停用公开报告链接。', 403)
    link = PublicClassReportLink.objects.select_for_update().filter(inclass=classroom).first()
    if action == 'enable' and link and not link.active and actor.role != 'owner':
        raise BusinessError('forbidden', '班主任已停用公开报告，仅班主任可恢复。', 403)

    @sensitive_variables()
    def apply():
        nonlocal link
        require_revision(link.revision if link else 0, payload.get('revision'))
        if action in ('disable', 'rotate') and (not link or not link.active):
            raise BusinessError('public_link_state_conflict', '公开报告尚未启用，请刷新后核对。', 409)
        if action == 'disable':
            link.active = False
            link.revision += 1
            link.save(update_fields=['active', 'revision'])
            event(actor, classroom, 'public_report_disabled', '关闭本班公开报告链接', revision=link.revision)
        elif action == 'rotate' or not link or not link.active:
            # Make the snapshot ready before the bearer link becomes usable.
            refresh_report(classroom.pk)
            token = secrets.token_urlsafe(32)
            secret = cipher().encrypt(f'{classroom.pk}:{token}'.encode('ascii')).decode('ascii')
            if link:
                link.token_digest = token_digest(token)
                link.token_ciphertext = secret
                link.active = True
                link.revision += 1
                link.save(update_fields=['token_digest', 'token_ciphertext', 'active', 'revision'])
            else:
                link = PublicClassReportLink.objects.create(
                    inclass=classroom, token_digest=token_digest(token), token_ciphertext=secret)
            event(actor, classroom, 'public_report_rotated' if action == 'rotate' else 'public_report_enabled',
                  '更新本班公开报告链接' if action == 'rotate' else '启用本班公开报告链接', revision=link.revision)
        # A receipt must never retain bearer tokens or QR content.
        return {'receipt': {'action': action, 'revision': link.revision}}

    result = once(actor, classroom, f'public-report:{classroom.pk}', payload, apply)
    # Retry before stale-revision validation, but always return current authority.
    return {**result, **link_details(request, classroom)}
