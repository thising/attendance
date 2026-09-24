"""Tracked runtime configuration; never imports the ignored legacy settings."""
import os
import base64
import tempfile
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

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

# The VPS 2026 initial migration already contains discipline columns. The
# development 0002 would rebuild those tables and can erase nonzero values.
# Inspect the existing ledger read-only before Django is allowed to load a graph.
DUXING_MIGRATION_LINEAGE = os.environ.get('DUXING_MIGRATION_LINEAGE', 'development')
if DUXING_MIGRATION_LINEAGE not in ('development', 'production-2026'):
    raise RuntimeError('Unknown DUXING_MIGRATION_LINEAGE; expected development or production-2026.')
_db_path = str(DATABASES['default']['NAME'])
_db_file = Path(unquote(urlparse(_db_path).path)) if _db_path.startswith('file:') else Path(_db_path)
if _db_path != ':memory:' and _db_file.is_file():
    _db = sqlite3.connect(_db_file.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        _tables = {row[0] for row in _db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        _ledger = ({row[0] for row in _db.execute("SELECT name FROM django_migrations WHERE app='manage'")}
                   if 'django_migrations' in _tables else set())
        _columns = {table: {row[1] for row in _db.execute(f'PRAGMA table_info({table})')}
                    for table in ('manage_class', 'manage_student', 'manage_activity',
                                  'manage_report', 'manage_summarycount') if table in _tables}
    finally:
        _db.close()
    _required = {
        'manage_class': {'id', 'classname', 'sharecode', 'managecode', 'owner_id'},
        'manage_student': {'id', 'inclass_id', 'number', 'name', 'sex'},
        'manage_activity': {'id', 'inclass_id', 'time', 'activity_type', 'name', 'status'},
        'manage_report': {'id', 'activity_id', 'student_id', 'status', 'level', 'discipline'},
        'manage_summarycount': {'id', 'student_id', 'year', 'month', 'absent_count',
            'late_count', 'leave_count', 'low_count', 'mid_count', 'high_count',
            'discipline_low_count', 'discipline_mid_count', 'discipline_high_count'},
    }
    _production_shape = (all(fields.issubset(_columns.get(table, set())) for table, fields in _required.items())
                         and '0001_initial' in _ledger and '0002_auto_20200927_0028' not in _ledger)
    if DUXING_MIGRATION_LINEAGE == 'development' and _production_shape:
        raise RuntimeError('2026 production migration lineage detected. Refusing development 0002; set DUXING_MIGRATION_LINEAGE=production-2026 after verified backup and preflight.')
    if DUXING_MIGRATION_LINEAGE == 'production-2026':
        _known = {'0001_initial','0003_duxing_foundation','0004_named_committee_accounts',
                  '0005_configurable_scores_and_account_credentials','0006_audit_model_state',
                  '0007_average_decimal_places','0008_activity_details',
                  '0009_public_reports_login_guard','0010_current_class_report'}
        if not _production_shape or not _ledger.issubset(_known):
            raise RuntimeError('Production migration lineage/schema is unrecognized; migration blocked for manual review.')
elif DUXING_MIGRATION_LINEAGE == 'production-2026':
    raise RuntimeError('Production migration mode requires an existing, verified 2026 database.')
if DUXING_MIGRATION_LINEAGE == 'production-2026':
    MIGRATION_MODULES = {'manage': 'manage.production_migrations'}
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
