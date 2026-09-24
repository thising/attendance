"""Business facts, immutable scoring versions and temporal roster identities."""
import secrets
import hashlib
import re
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction, IntegrityError
from django.utils import timezone


def class_hash():
    return secrets.token_hex(5)


def committee_prefix():
    return hashlib.sha256(secrets.token_bytes(16)).hexdigest()[:4]


def local_date():
    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


class Class(models.Model):
    classname = models.CharField('班级名称', max_length=30)
    sharecode = models.CharField('访问码', max_length=10, default=class_hash, unique=True)
    # Legacy migration evidence only; never used by active authentication.
    managecode = models.CharField('旧班级密码（停用）', max_length=128, default='!')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    grant_version = models.PositiveIntegerField(default=1)
    committee_prefix = models.CharField(max_length=4, unique=True, editable=False, default=committee_prefix)
    revision = models.PositiveIntegerField(default=1)
    report_revision = models.PositiveIntegerField(default=0)
    archived = models.BooleanField(default=False)
    started_on = models.DateField(default=local_date)
    legacy_pending = models.BooleanField(default=False)

    def __str__(self):
        return self.classname

    @property
    def code(self):
        """Non-secret URL identity; shared class codes are retired."""
        return str(self.pk)

    def set_password(self, raw):
        self.managecode = make_password(raw or None)

    def check_password(self, raw):
        return bool(raw) and check_password(raw, self.managecode)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values('owner_id','committee_prefix').first()
            if original and original['owner_id'] != self.owner_id:
                raise ValidationError('本期不支持转移班级负责人。')
            if original and original['committee_prefix'] != self.committee_prefix:
                raise ValidationError('班委账号前缀建立后不可修改。')
            return super().save(*args, **kwargs)
        for attempt in range(32):
            if not re.fullmatch(r'[0-9a-f]{4}', self.committee_prefix):
                raise ValidationError('班委账号前缀需为4位小写十六进制字符。')
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if not type(self).objects.filter(committee_prefix=self.committee_prefix).exists():
                    raise
                self.committee_prefix = committee_prefix()
        raise ValidationError('暂时无法分配班委账号前缀，请稍后重试。')


class Student(models.Model):
    enum_sex = (('male', '男'), ('female', '女'))
    number = models.CharField('学号', max_length=20)
    name = models.CharField('姓名', max_length=20)
    sex = models.CharField('性别', choices=enum_sex, max_length=8, default='male')
    inclass = models.ForeignKey(Class, models.PROTECT)
    active = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['number', 'id']
        constraints = [models.UniqueConstraint(fields=['inclass', 'number'], name='student_class_number_unique')]

    def __str__(self):
        return f'{self.number} | {self.name}'


class Activity(models.Model):
    enum_activity_type = (('class', '考勤'), ('activity', '活动'), ('discipline', '违纪'))
    enum_status_type = (('preview', '预览'), ('release', '发布'))
    time = models.DateTimeField('创建时间', auto_now_add=True)
    occurred_on = models.DateField('发生日期', default=local_date)
    activity_type = models.CharField('活动类型', choices=enum_activity_type, max_length=20, default='class')
    name = models.CharField('活动名称', max_length=64)
    details = models.TextField('详情', blank=True, default='', max_length=500)
    status = models.CharField('活动状态', choices=enum_status_type, max_length=20, default='preview')
    inclass = models.ForeignKey(Class, models.PROTECT)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-occurred_on', '-id']
        indexes = [models.Index(fields=['inclass', 'occurred_on'], name='activity_class_date_idx')]

    def __str__(self):
        return f'{self.occurred_on} | {self.name}'


class Report(models.Model):
    enum_student_status = (('absent', '缺席'), ('late', '迟到'), ('leave', '请假'), ('present', '正常'))
    enum_activity_level = (('none', '未参加'), ('low', '班级'), ('mid', '院级'), ('high', '校级'))
    enum_discipline_level = (('none', '正常'), ('low', '轻度违纪'), ('mid', '中度违纪'), ('high', '严重违纪'))
    activity = models.ForeignKey(Activity, models.CASCADE)
    student = models.ForeignKey(Student, models.PROTECT)
    status = models.CharField('考勤状态', choices=enum_student_status, max_length=20, default='present')
    level = models.CharField('参与情况', choices=enum_activity_level, max_length=20, default='none')
    discipline = models.CharField('违纪情况', choices=enum_discipline_level, max_length=20, default='none')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['activity', 'student'], name='report_activity_student_unique')]


WEIGHT_DEFAULTS = {'absent': '2', 'late': '1', 'leave': '0', 'low': '1', 'mid': '3',
                   'high': '5', 'dlow': '5', 'dmid': '8', 'dhigh': '12'}
COUNT_FIELDS = {'absent': 'absent_count', 'late': 'late_count', 'leave': 'leave_count',
                'low': 'low_count', 'mid': 'mid_count', 'high': 'high_count',
                'dlow': 'discipline_low_count', 'dmid': 'discipline_mid_count', 'dhigh': 'discipline_high_count'}


class SummaryCount(models.Model):
    """Derived counts only. Calendar/report selectors are the scoring authority."""
    student = models.ForeignKey(Student, models.PROTECT)
    year = models.IntegerField('年', default=2020)
    month = models.IntegerField('月', default=9)
    absent_count = models.IntegerField(default=0)
    late_count = models.IntegerField(default=0)
    leave_count = models.IntegerField(default=0)
    low_count = models.IntegerField(default=0)
    mid_count = models.IntegerField(default=0)
    high_count = models.IntegerField(default=0)
    discipline_low_count = models.IntegerField(default=0)
    discipline_mid_count = models.IntegerField(default=0)
    discipline_high_count = models.IntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['student', 'year', 'month'], name='summary_student_month_unique')]

    def score(self):
        total = Decimal('60')
        for key, field in COUNT_FIELDS.items():
            total += getattr(self, field) * Decimal(WEIGHT_DEFAULTS[key]) * (1 if key in ('low', 'mid', 'high') else -1)
        return max(Decimal('0'), total)


class ScoringPolicyVersion(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    effective_term_start = models.DateField()
    created_at = models.DateTimeField(default=timezone.now)
    algorithm = models.CharField(max_length=30, default='calendar-month-v1')
    average_decimal_places = models.PositiveSmallIntegerField(default=2,
        validators=[MinValueValidator(0), MaxValueValidator(4)])
    base_score = models.DecimalField(max_digits=8, decimal_places=2, default=60, validators=[MinValueValidator(0)])
    minimum_score = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    maximum_score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, default=None,
                                       validators=[MinValueValidator(0)])
    absent = models.DecimalField(max_digits=8, decimal_places=2, default=2, validators=[MinValueValidator(0)])
    late = models.DecimalField(max_digits=8, decimal_places=2, default=1, validators=[MinValueValidator(0)])
    leave = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    low = models.DecimalField(max_digits=8, decimal_places=2, default=1, validators=[MinValueValidator(0)])
    mid = models.DecimalField(max_digits=8, decimal_places=2, default=3, validators=[MinValueValidator(0)])
    high = models.DecimalField(max_digits=8, decimal_places=2, default=5, validators=[MinValueValidator(0)])
    dlow = models.DecimalField(max_digits=8, decimal_places=2, default=5, validators=[MinValueValidator(0)])
    dmid = models.DecimalField(max_digits=8, decimal_places=2, default=8, validators=[MinValueValidator(0)])
    dhigh = models.DecimalField(max_digits=8, decimal_places=2, default=12, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(average_decimal_places__gte=0, average_decimal_places__lte=4),
                name='policy_average_decimal_places_range'),
            models.CheckConstraint(condition=models.Q(base_score__gte=0, base_score__lte=Decimal('999999.99'),
                minimum_score__gte=0, minimum_score__lte=Decimal('999999.99')) &
                (models.Q(maximum_score__isnull=True) | models.Q(maximum_score__gte=0, maximum_score__lte=Decimal('999999.99'))),
                name='policy_monthly_range'),
            models.CheckConstraint(condition=models.Q(minimum_score__lte=models.F('base_score')) &
                (models.Q(maximum_score__isnull=True) | models.Q(base_score__lte=models.F('maximum_score'))),
                name='policy_monthly_order'),
        ]

    @property
    def weights(self):
        return {key: format(Decimal(getattr(self, key)), '.2f') for key in WEIGHT_DEFAULTS}

    @property
    def monthly(self):
        return {'base': format(Decimal(self.base_score), '.2f'),
                'minimum': format(Decimal(self.minimum_score), '.2f'),
                'maximum': None if self.maximum_score is None else format(Decimal(self.maximum_score), '.2f')}

    def clean(self):
        super().clean()
        try:
            base, minimum = Decimal(str(self.base_score)), Decimal(str(self.minimum_score))
            maximum = None if self.maximum_score is None else Decimal(str(self.maximum_score))
        except (ArithmeticError, ValueError, TypeError):
            return  # DecimalField validation supplies the invalid-field error.
        if not all(value.is_finite() for value in (base, minimum) + (() if maximum is None else (maximum,))):
            return
        if minimum > base or (maximum is not None and base > maximum):
            raise ValidationError('月度最低分不能高于基础分；最高分不能低于基础分。')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('评分版本不可覆盖。')
        self.full_clean()
        super().save(*args, **kwargs)


class OwnerScoringSettings(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, models.PROTECT)
    default_policy = models.ForeignKey(ScoringPolicyVersion, models.PROTECT, null=True)
    revision = models.PositiveIntegerField(default=0)


class OwnerTermPolicy(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    term_key = models.CharField(max_length=20)
    policy = models.ForeignKey(ScoringPolicyVersion, models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'term_key'], name='owner_term_policy_unique')]


class RosterVersion(models.Model):
    """A change is effective for its whole term, inherited until another change."""
    student = models.ForeignKey(Student, models.PROTECT)
    inclass = models.ForeignKey(Class, models.PROTECT)
    effective_term_start = models.DateField()
    number = models.CharField(max_length=20)
    name = models.CharField(max_length=20)
    sex = models.CharField(max_length=8)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['student', 'effective_term_start'], name='roster_student_term_unique')]
        indexes = [models.Index(fields=['inclass', 'effective_term_start'], name='roster_class_term_idx')]


class ClassTerm(models.Model):
    inclass = models.ForeignKey(Class, models.PROTECT)
    term_key = models.CharField(max_length=20)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT)
    policy = models.ForeignKey(ScoringPolicyVersion, models.PROTECT, null=True)
    archived_data = models.JSONField(null=True)
    baseline_source = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inclass', 'term_key'], name='class_term_unique')]


class Submission(models.Model):
    actor_key = models.CharField(max_length=64)
    inclass = models.ForeignKey(Class, models.PROTECT, null=True)
    scope = models.CharField(max_length=80)
    key = models.UUIDField()
    digest = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor_key', 'scope', 'key'], name='submission_identity_scope_key_unique')]


class AuditEvent(models.Model):
    inclass = models.ForeignKey(Class, models.PROTECT)
    actor_role = models.CharField(max_length=20)
    actor_id = models.PositiveIntegerField(null=True)
    actor_label = models.CharField(max_length=100, blank=True)
    kind = models.CharField(max_length=40)
    summary = models.CharField(max_length=240)
    affected_count = models.PositiveIntegerField(default=0)
    object_id = models.PositiveIntegerField(null=True)
    revision = models.PositiveIntegerField(null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['inclass', 'created_at'], name='event_class_time_idx')]


class ClassDailyUsage(models.Model):
    inclass = models.ForeignKey(Class, models.PROTECT)
    day = models.DateField()
    created_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inclass', 'day'], name='class_daily_usage_unique')]


class CommitteeAccount(models.Model):
    inclass = models.ForeignKey(Class, models.PROTECT, related_name='committee_accounts')
    username = models.CharField(max_length=45, unique=True)
    display_name = models.CharField(max_length=40, blank=True)
    password = models.CharField(max_length=128)
    credential_ciphertext = models.TextField(blank=True, default='', editable=False)
    active = models.BooleanField(default=True)
    auth_version = models.PositiveIntegerField(default=1)
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)

    def set_password(self, raw):
        self.password = make_password(raw)

    def check_password(self, raw):
        return isinstance(raw, str) and check_password(raw, self.password)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values('inclass_id','username').first()
            if original and original['inclass_id'] != self.inclass_id:
                raise ValidationError('班委账号不能转移班级。')
            if original and original['username'] != self.username:
                raise ValidationError('班委用户名建立后不可修改。')
        else:
            prefix = self.inclass.committee_prefix + '.'
            if not self.username.startswith(prefix):
                self.username = prefix + self.username.lower()
            if not re.fullmatch(re.escape(prefix) + r'[a-z0-9._-]{3,40}', self.username):
                raise ValidationError('班委用户名格式无效。')
        super().save(*args, **kwargs)


class CommitteeLoginGuard(models.Model):
    # Account-global throttle; clearing cookies does not reset failed attempts.
    username_digest = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    first_failure_at = models.FloatField(default=0)
    blocked_until = models.FloatField(default=0)


class OwnerLoginGuard(models.Model):
    # Per-name throttle survives sessions and applies to unknown names too.
    username_digest = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    first_failure_at = models.FloatField(default=0)
    blocked_until = models.FloatField(default=0)


class PublicClassReportLink(models.Model):
    inclass = models.OneToOneField(Class, models.CASCADE, related_name='public_report_link')
    token_digest = models.CharField(max_length=64, unique=True)
    token_ciphertext = models.TextField(editable=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)


class CurrentClassReport(models.Model):
    """One atomically replaced public snapshot per class; never served as a static file."""
    inclass = models.OneToOneField(Class, models.CASCADE, related_name='current_public_report')
    term_key = models.CharField(max_length=16, blank=True)
    month_key = models.CharField(max_length=7)
    source_revision = models.PositiveIntegerField()
    schema_version = models.PositiveIntegerField(default=1)
    payload_digest = models.CharField(max_length=64)
    payload = models.JSONField()
    generated_at = models.DateTimeField(default=timezone.now)
