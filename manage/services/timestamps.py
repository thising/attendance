"""Serialize legacy Shanghai wall-clock datetimes without changing stored facts."""
from datetime import datetime
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')


def beijing_iso(value, timespec='seconds'):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING)
    return value.astimezone(BEIJING).isoformat(timespec=timespec)
