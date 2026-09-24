from dataclasses import dataclass
from datetime import date
from django.utils import timezone
from .errors import BusinessError


def business_today():
    now = timezone.now()
    return timezone.localtime(now).date() if timezone.is_aware(now) else now.date()


@dataclass(frozen=True)
class Term:
    year: int
    season: str

    @property
    def key(self):
        return f'{self.year}-{self.season}'

    @property
    def start(self):
        return date(self.year, 2 if self.season == 'spring' else 9, 1)

    @property
    def end(self):
        return date(self.year, 8, 1) if self.season == 'spring' else date(self.year + 1, 2, 1)

    @property
    def label(self):
        return f'{self.year} {"春季" if self.season == "spring" else "秋季"}'

    def contains(self, day):
        return self.start <= day < self.end

    def months(self, today=None):
        today = today or business_today()
        cursor, result = self.start, []
        while cursor < self.end and cursor <= today:
            result.append((cursor.year, cursor.month))
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        return result


def term_for_date(day):
    if day.month == 8:
        return None
    if day.month == 1:
        return Term(day.year - 1, 'autumn')
    return Term(day.year, 'spring' if day.month < 8 else 'autumn')


def previous_term(term):
    return Term(term.year, 'spring') if term.season == 'autumn' else Term(term.year - 1, 'autumn')


def display_term(today=None):
    today = today or business_today()
    return term_for_date(today) or Term(today.year, 'spring')


def parse_term(key):
    try:
        year, season = key.split('-', 1)
        year = int(year)
        if season not in ('spring', 'autumn') or not 1900 <= year <= 9998:
            raise ValueError
        return Term(year, season)
    except (ValueError, AttributeError):
        raise BusinessError('invalid_term', '学期格式无效。')


def writable_term(expected_key=None, occurred_on=None):
    today = business_today()
    current = term_for_date(today)
    if current is None:
        raise BusinessError('august_read_only', '8 月暂停业务录入，仅可查看历史数据。', 403)
    if expected_key != current.key:
        raise BusinessError('term_read_only', '学期已结束或页面已过期，请刷新后重试。', 409)
    if occurred_on is not None and (not current.contains(occurred_on) or occurred_on > today):
        raise BusinessError('invalid_record_date', '发生日期必须属于当前学期，且不能晚于今天。')
    return current
