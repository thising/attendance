"""Consistent SQLite reads without reserving the only writer slot."""
from contextlib import contextmanager
from functools import wraps

from django.db import connection, transaction


@contextmanager
def read_snapshot():
    if connection.in_atomic_block:
        yield
        return
    connection.ensure_connection()
    original = connection.transaction_mode
    try:
        connection.transaction_mode = 'DEFERRED'
        with transaction.atomic():
            # Restore before user code; nested write services still request
            # IMMEDIATE for independent outer transactions.
            connection.transaction_mode = original
            yield
    finally:
        connection.transaction_mode = original


def read_snapshot_view(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        if request.method != 'GET':
            return fn(request, *args, **kwargs)
        with read_snapshot():
            return fn(request, *args, **kwargs)
    return wrapped


def consistent_read(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with read_snapshot():
            return fn(*args, **kwargs)
    return wrapped
