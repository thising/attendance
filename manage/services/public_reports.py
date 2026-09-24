"""Owner-managed bearer links for a class's current-term read-only report."""
import hashlib
import io
import secrets

import segno
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.db import transaction
from django.views.decorators.debug import sensitive_variables

from manage.models import PublicClassReportLink
from .access import actor_for
from .credential_store import cipher
from .errors import BusinessError
from .writes import event
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
    link = PublicClassReportLink.objects.filter(inclass=classroom).first()
    if not link or not link.active:
        return {'active': False, 'url': None, 'qr_svg': None}
    path = f'/report/{token_for(link)}/'
    url = settings.DUXING_PUBLIC_BASE_URL + path if settings.DUXING_PUBLIC_BASE_URL else request.build_absolute_uri(path)
    qr=segno.make(url, error='m')
    stream=io.BytesIO()
    qr.save(stream,kind='svg',scale=3,border=2,xmldecl=False)
    return {'active': True, 'url': url, 'qr_svg': stream.getvalue().decode('utf-8'),
            'qr_data_uri': qr.svg_data_uri(scale=3,border=2)}


@sensitive_variables()
@transaction.atomic
def change_link(request, classroom, action):
    actor = actor_for(request, classroom)
    if action not in ('enable', 'rotate', 'disable'):
        raise BusinessError('invalid_action', '公开报告操作无效。')
    if action in ('rotate', 'disable') and actor.role != 'owner':
        raise BusinessError('forbidden', '仅班主任可更换或停用公开报告链接。', 403)
    link = PublicClassReportLink.objects.select_for_update().filter(inclass=classroom).first()
    if action == 'disable':
        if link and link.active:
            link.active = False
            link.save(update_fields=['active'])
            event(actor, classroom, 'public_report_disabled', '关闭本班公开报告链接')
    else:
        if action == 'rotate' or not link or not link.active:
            # Make the snapshot ready before the bearer link becomes usable.
            refresh_report(classroom.pk)
            token = secrets.token_urlsafe(32)
            secret = cipher().encrypt(f'{classroom.pk}:{token}'.encode('ascii')).decode('ascii')
            if link:
                link.token_digest = token_digest(token)
                link.token_ciphertext = secret
                link.active = True
                link.save(update_fields=['token_digest', 'token_ciphertext', 'active'])
            else:
                link = PublicClassReportLink.objects.create(
                    inclass=classroom, token_digest=token_digest(token), token_ciphertext=secret)
            event(actor, classroom, 'public_report_rotated' if action == 'rotate' else 'public_report_enabled',
                  '更新本班公开报告链接' if action == 'rotate' else '启用本班公开报告链接')
    return link_details(request, classroom)
