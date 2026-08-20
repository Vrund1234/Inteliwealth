from sqlalchemy import text

from utils.db import engine

# Arbitrary fixed bigint identifying this pipeline's run lock — must stay
# constant across processes/runs for pg_try_advisory_lock to serialize them.
_LOCK_KEY = 872234561


def try_acquire():
    conn = engine.connect()
    acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}).scalar()
    if not acquired:
        conn.close()
        return None
    return conn


def release(conn):
    if conn is None:
        return
    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
    conn.close()
