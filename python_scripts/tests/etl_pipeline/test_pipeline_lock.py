"""A Postgres session-level advisory lock, held on a dedicated Connection for
the run's duration. Session-level (not transaction-level) is required: the run
spans many independent transactions."""

from sqlalchemy import text

from etl_pipeline.pipeline_lock import LOCK_KEY, release, try_acquire
from utils.db import engine


def _advisory_lock_count():
    with engine.begin() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        )).scalar()


def test_acquiring_returns_a_connection():
    connection = try_acquire()
    try:
        assert connection is not None
    finally:
        release(connection)


def test_a_second_acquire_while_held_returns_none():
    first = try_acquire()
    try:
        assert try_acquire() is None
    finally:
        release(first)


def test_releasing_lets_the_next_run_acquire():
    first = try_acquire()
    release(first)

    second = try_acquire()
    try:
        assert second is not None
    finally:
        release(second)


def test_releasing_none_is_safe():
    release(None)


def test_releasing_twice_is_safe():
    connection = try_acquire()
    release(connection)
    release(connection)


def test_the_lock_is_actually_registered_in_pg_locks():
    before = _advisory_lock_count()
    connection = try_acquire()
    try:
        assert _advisory_lock_count() == before + 1
    finally:
        release(connection)
    assert _advisory_lock_count() == before
