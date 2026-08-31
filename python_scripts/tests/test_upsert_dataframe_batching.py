"""upsert_dataframe(): must batch each chunk into a single multi-row
statement, not issue one round trip per row.

Row-by-row execution via psycopg2's default cursor.executemany() was
measured at ~2,070 rows/sec against silver.transaction_master_new (~62s
for a ~129k-row upsert) versus ~65,000 rows/sec for the bronze row_hash
indexed lookup doing comparable work (see
docs/superpowers/plans/2026-08-26-bronze-dedup-performance.md). Batching
each chunk into one psycopg2.extras.execute_values() call collapses that
to one round trip per chunk instead of one per row.

Correctness (conflict targets, created_at preservation, NaT handling,
etc.) is already covered exhaustively by test_upsert_dataframe.py and is
NOT re-tested here -- this file covers only the new round-trip-count
contract.
"""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine, upsert_dataframe

TEST_SCHEMA = "bronze"
TEST_TABLE = "_test_upsert_batching"
SENTINEL = "__TEST_UPSERT_BATCH__"


@pytest.fixture(autouse=True)
def temp_table():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{TEST_TABLE} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT _test_upsert_batching_uq UNIQUE (source, key_col)
            )
        """))
        conn.execute(text(
            f"DELETE FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL})
    yield
    with engine.begin() as conn:
        conn.execute(text(
            f"DELETE FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL})


def test_one_round_trip_per_chunk_not_per_row(monkeypatch):
    import utils.db as db_module

    calls = []
    real_execute_values = db_module.execute_values

    def spy(cursor, sql, argslist, *args, **kwargs):
        calls.append(len(argslist))
        return real_execute_values(cursor, sql, argslist, *args, **kwargs)

    monkeypatch.setattr(db_module, "execute_values", spy)

    df = pd.DataFrame([
        {"source": SENTINEL, "key_col": uuid.uuid4().hex[:8], "value_col": f"v{i}"}
        for i in range(250)
    ])

    n = upsert_dataframe(
        df, TEST_SCHEMA, TEST_TABLE,
        conflict_columns=["source", "key_col"],
        chunksize=100,
    )

    assert n["attempted"] == 250
    # 250 rows / chunksize 100 -> 3 chunks -> exactly 3 calls to
    # execute_values, one per chunk -- never one call per row.
    assert calls == [100, 100, 50]

    with engine.begin() as conn:
        count = conn.execute(text(
            f"SELECT count(*) FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL}).scalar()
    assert count == 250


def test_two_rows_sharing_a_conflict_key_in_one_call_do_not_error():
    # Regression: execute_values() puts every row of a chunk into ONE
    # multi-row INSERT statement. If two incoming rows share the same
    # conflict key but differ in some other column (e.g. a bronze resend
    # with a corrected status field, still within the same batch), Postgres
    # raises "ON CONFLICT DO UPDATE command cannot affect row a second
    # time" -- a single statement is never allowed to touch the same
    # conflict target twice. The old row-by-row executemany() never hit
    # this (each row was its own separate statement), so this must be
    # handled explicitly now: last row for a given key wins, same as the
    # old sequential behavior produced.
    key = uuid.uuid4().hex[:8]
    df = pd.DataFrame([
        {"source": SENTINEL, "key_col": key, "value_col": "first"},
        {"source": SENTINEL, "key_col": key, "value_col": "second"},
    ])

    n = upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    assert n["attempted"] == 2  # both rows processed, even though only one survives
    with engine.begin() as conn:
        rows = conn.execute(text(
            f"SELECT value_col FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s AND key_col = :k"
        ), {"s": SENTINEL, "k": key}).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "second"
