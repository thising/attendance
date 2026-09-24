"""Owner-authorized password retrieval, separate from hash-based authentication."""
import json
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .errors import BusinessError


def cipher():
    try:
        return Fernet(settings.DUXING_CREDENTIAL_KEY)
    except (ValueError, TypeError, AttributeError):
        raise BusinessError('credential_key_unavailable', '登录信息加密密钥未配置，请联系系统管理员。', 503) from None


@sensitive_variables()
def store_password(account, password):
    # Binding stops ciphertext transplanted between database rows from being
    # interpreted as another account's login credentials.
    document={'account_id':account.pk,'class_id':account.inclass_id,
              'username':account.username,'password':password}
    account.credential_ciphertext=cipher().encrypt(json.dumps(document,ensure_ascii=False).encode()).decode('ascii')
    account.save(update_fields=['credential_ciphertext'])


@sensitive_variables()
def read_password(account):
    if not account.credential_ciphertext:
        raise BusinessError('credential_unavailable','该账号尚无可复制密码，请先重置一次密码。',409)
    try:
        document=json.loads(cipher().decrypt(account.credential_ciphertext.encode()))
        if (document.get('account_id')!=account.pk or document.get('class_id')!=account.inclass_id or
                document.get('username')!=account.username or not account.check_password(document.get('password'))):
            raise ValueError
        return document['password']
    except (InvalidToken, ValueError, TypeError, KeyError, AttributeError):
        raise BusinessError('credential_unavailable','该账号登录信息无法读取，请核对加密密钥或重置密码。',409) from None
