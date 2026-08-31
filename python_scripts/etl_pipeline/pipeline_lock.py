"""A Postgres session-level advisory lock, so only one runner is ever mid-run.

Two reasons it exists, and both matter:

  1. Overlapping cron invocations are safe. A second invocation that cannot
     acquire the lock logs "another run in progress" and exits 0 -- not an
     error condition, and cron must not treat it as one. This removes any need
     to tune the 15-minute cadence against expected run duration.
  2. It makes the RETURNING (xmax = 0) reading in utils/db.upsert_dataframe
     exact, by making this process the only writer for the run's duration.

Session-level (pg_try_advisory_lock), not transaction-level: the run spans
many independent transactions, so a transaction-scoped lock would be released
by the first commit. The lock is therefore held on its own dedicated
Connection, which must stay open for the whole run -- not one borrowed from
and returned to the pool.
"""

from sqlalchemy import text

from utils.db import engine

# Arbitrary but FIXED: pg_try_advisory_lock only serializes callers that pass
# the same key, so this must never change between releases.
LOCK_KEY = 872234561


def try_acquire():
    """The Connection holding the lock, or None if another run holds it."""
    connection = engine.connect()
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}
    ).scalar()
    if not acquired:
        connection.close()
        return None
    return connection


def release(connection):
    """Release and close. Safe to call with None, and safe to call twice."""
    if connection is None:
        return
    try:
        if not connection.closed:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY}
            )
    finally:
        connection.close()
