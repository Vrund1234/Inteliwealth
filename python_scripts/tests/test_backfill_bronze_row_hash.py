"""backfill_bronze_row_hash.py must reproduce the exact full-row duplicate
semantics bronze's loaders already use: two byte-identical rows get the
SAME row_hash, and two rows sharing a natural key but differing in some
other column (the real td_ptrno/rep_date pattern found live in bronze) get
DIFFERENT row_hashes -- proving this hashes the full row, not a narrower
natural key."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

import backfill_bronze_row_hash
from utils.db import engine
from backfill_bronze_row_hash import backfill_table, _normalize_transaction

TEST_SCHEMA = "bronze"
TEST_TABLE = "_test_backfill_row_hash"
SENTINEL = "__TEST_BACKFILL_ROW_HASH__"


@pytest.fixture(autouse=True)
def temp_table():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{TEST_TABLE} (
                source TEXT,
                trxnno TEXT,
                td_ptrno TEXT,
                row_hash TEXT,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
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


def _insert(trxnno, td_ptrno):
    with engine.begin() as conn:
        conn.execute(text(
            f"INSERT INTO {TEST_SCHEMA}.{TEST_TABLE} (source, trxnno, td_ptrno) "
            f"VALUES (:s, :t, :p)"
        ), {"s": SENTINEL, "t": trxnno, "p": td_ptrno})


def test_backfill_populates_row_hash_for_every_row():
    txn = uuid.uuid4().hex[:8]
    _insert(txn, "111")
    backfill_table(TEST_SCHEMA, TEST_TABLE, compare_cols=["trxnno", "td_ptrno"], normalize_fn=_normalize_transaction, sentinel_source=SENTINEL)

    with engine.begin() as conn:
        row_hash = conn.execute(text(
            f"SELECT row_hash FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL}).scalar()
    assert row_hash is not None


def test_byte_identical_rows_get_the_same_hash():
    txn = uuid.uuid4().hex[:8]
    _insert(txn, "111")
    _insert(txn, "111")
    backfill_table(TEST_SCHEMA, TEST_TABLE, compare_cols=["trxnno", "td_ptrno"], normalize_fn=_normalize_transaction, sentinel_source=SENTINEL)

    with engine.begin() as conn:
        hashes = [r[0] for r in conn.execute(text(
            f"SELECT row_hash FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s AND trxnno = :t"
        ), {"s": SENTINEL, "t": txn})]
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]


def test_backfill_issues_one_update_per_chunk_not_per_row(monkeypatch):
    """Regression guard: a naive backfill that issues one UPDATE per row
    doesn't scale as bronze grows into millions of rows, and per the
    2026-08-26 dedup-performance spec this backfill must be re-run after
    every future bronze schema change -- so its own cost must stay
    O(batches), not O(rows). Writes go through psycopg2.extras.execute_values
    (see backfill_table's docstring), which SQLAlchemy's own statement
    events can't see since it runs on the raw DBAPI cursor -- so this counts
    calls to execute_values itself instead."""
    for i in range(7):
        _insert(f"t{i}", str(i))

    calls = []
    real_execute_values = backfill_bronze_row_hash.execute_values

    def _counting_execute_values(cursor, sql, argslist, **kwargs):
        calls.append(argslist)
        return real_execute_values(cursor, sql, argslist, **kwargs)

    monkeypatch.setattr(backfill_bronze_row_hash, "execute_values", _counting_execute_values)

    backfill_table(
        TEST_SCHEMA, TEST_TABLE,
        compare_cols=["trxnno", "td_ptrno"],
        normalize_fn=_normalize_transaction,
        sentinel_source=SENTINEL,
        chunk_size=3,
    )

    # 7 rows at chunk_size=3 -> ceil(7/3) = 3 execute_values calls, not 7.
    assert len(calls) == 3
    assert [len(c) for c in calls] == [3, 3, 1]

    with engine.begin() as conn:
        hashes = [r[0] for r in conn.execute(text(
            f"SELECT row_hash FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL})]
    assert len(hashes) == 7
    assert all(h is not None for h in hashes)


def test_rows_sharing_a_natural_key_but_differing_in_another_column_get_different_hashes():
    """The real bronze pattern: same trxnno, different td_ptrno -- must NOT
    collapse to the same hash (that would silently narrow bronze's flag
    from full-row to natural-key semantics)."""
    txn = uuid.uuid4().hex[:8]
    _insert(txn, "111")
    _insert(txn, "222")
    backfill_table(TEST_SCHEMA, TEST_TABLE, compare_cols=["trxnno", "td_ptrno"], normalize_fn=_normalize_transaction, sentinel_source=SENTINEL)

    with engine.begin() as conn:
        hashes = [r[0] for r in conn.execute(text(
            f"SELECT row_hash FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s AND trxnno = :t "
            f"ORDER BY td_ptrno"
        ), {"s": SENTINEL, "t": txn})]
    assert hashes[0] != hashes[1]
