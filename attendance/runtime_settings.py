"""Tracked runtime configuration; never imports the ignored legacy settings."""
import os
import base64
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DUXING_SECRET_KEY', 'development-only-change-before-deployment')
DEBUG = os.environ.get('DUXING_DEBUG', '1') == '1'
DUXING_CREDENTIAL_KEY = os.environ.get('DUXING_CREDENTIAL_KEY', '')
DUXING_PUBLIC_BASE_URL = os.environ.get('DUXING_PUBLIC_BASE_URL', '').rstrip('/')
if not DUXING_CREDENTIAL_KEY and DEBUG:
    # Separate encryption key from session signing. This file is local-only and
    # must accompany a database backup whenever password retrieval is needed.
    credential_key_path = BASE_DIR / '.local' / 'credential.key'
    credential_key_path.parent.mkdir(parents=True, exist_ok=True)
    if not credential_key_path.exists():
        descriptor, candidate_path = tempfile.mkstemp(prefix='.credential-', dir=credential_key_path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as keyfile:
                keyfile.write(base64.urlsafe_b64encode(os.urandom(32)))
                keyfile.flush()
                os.fsync(keyfile.fileno())
            try:
                # Publish only complete key bytes; concurrent starts cannot
                # overwrite a key or observe a newly created empty key file.
                os.link(candidate_path, credential_key_path)
            except FileExistsError:
                pass
        finally:
            os.unlink(candidate_path)
    DUXING_CREDENTIAL_KEY = credential_key_path.read_text().strip()
    try:
        if len(base64.b64decode(DUXING_CREDENTIAL_KEY, altchars=b'-_', validate=True)) != 32:
            raise ValueError
    except (ValueError, TypeError):
        raise RuntimeError('Local credential key is invalid; restore the matching key backup.') from None
ALLOWED_HOSTS = os.environ.get('DUXING_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'manage',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'attendance.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': ['django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'attendance.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
    'NAME': os.environ.get('DUXING_DATABASE', str(BASE_DIR / '.local' / 'development.sqlite3')),
    'OPTIONS': {'timeout': 5, 'transaction_mode': 'IMMEDIATE'},
    'TEST': {'NAME': str(BASE_DIR / '.local' / 'test.sqlite3')}}}
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
# Keep legacy local wall-clock timestamps unchanged during schema migration.
USE_TZ = False
USE_I18N = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / '.local' / 'staticfiles'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
LOGIN_URL = '/login/'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
if not DEBUG and SECRET_KEY == 'development-only-change-before-deployment':
    raise RuntimeError('Production requires DUXING_SECRET_KEY.')
if not DEBUG and not DUXING_CREDENTIAL_KEY:
    raise RuntimeError('Production requires a separate DUXING_CREDENTIAL_KEY.')
