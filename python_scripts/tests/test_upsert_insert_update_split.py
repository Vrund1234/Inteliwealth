"""upsert_dataframe() must report how many rows it INSERTed versus how many
it UPDATEd through the ON CONFLICT path -- the silver and gold "total
duplicate" numbers are exactly this split. Postgres sets xmax = 0 on a
freshly inserted tuple and non-zero on a conflict-updated one, so
RETURNING (xmax = 0) partitions the affected rows."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import collect_upserts, engine, upsert_dataframe

TEST_SCHEMA = "bronze"
TEST_TABLE = "_test_upsert_split"
SENTINEL = "__TEST_UPSERT_SPLIT__"
CONFLICT = ["source", "key_col"]


@pytest.fixture(autouse=True)
def temp_table():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{TEST_TABLE} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT _test_upsert_split_uq UNIQUE (source, key_col)
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


def _frame(keys, value):
    return pd.DataFrame(
        [{"source": SENTINEL, "key_col": k, "value_col": value} for k in keys]
    )


def test_all_new_rows_count_as_inserted():
    keys = [uuid.uuid4().hex[:8] for _ in range(3)]

    result = upsert_dataframe(_frame(keys, "v1"), TEST_SCHEMA, TEST_TABLE,
                              conflict_columns=CONFLICT)

    assert result == {"attempted": 3, "affected": 3, "inserted": 3, "updated": 0}


def test_a_resend_counts_as_updated_not_inserted():
    keys = [uuid.uuid4().hex[:8] for _ in range(3)]
    upsert_dataframe(_frame(keys, "v1"), TEST_SCHEMA, TEST_TABLE, conflict_columns=CONFLICT)

    result = upsert_dataframe(_frame(keys, "v2"), TEST_SCHEMA, TEST_TABLE,
                              conflict_columns=CONFLICT)

    assert result == {"attempted": 3, "affected": 3, "inserted": 0, "updated": 3}


def test_a_mixed_batch_splits_correctly():
    old = [uuid.uuid4().hex[:8] for _ in range(2)]
    new = [uuid.uuid4().hex[:8] for _ in range(3)]
    upsert_dataframe(_frame(old, "v1"), TEST_SCHEMA, TEST_TABLE, conflict_columns=CONFLICT)

    result = upsert_dataframe(_frame(old + new, "v2"), TEST_SCHEMA, TEST_TABLE,
                              conflict_columns=CONFLICT)

    assert result["inserted"] == 3
    assert result["updated"] == 2
    assert result["affected"] == 5
    assert result["attempted"] == 5


def test_same_key_twice_in_one_batch_is_attempted_twice_but_affects_one_row():
    # The in-chunk ROW_NUMBER() pre-filter collapses same-key rows before the
    # INSERT sees them. `attempted` still counts both -- that is exactly the
    # over-count the docstring conceded, and why `affected` exists.
    key = uuid.uuid4().hex[:8]
    df = pd.DataFrame([
        {"source": SENTINEL, "key_col": key, "value_col": "v1"},
        {"source": SENTINEL, "key_col": key, "value_col": "v2"},
    ])

    result = upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=CONFLICT)

    assert result["attempted"] == 2
    assert result["affected"] == 1
    assert result["inserted"] == 1
    assert result["updated"] == 0


def test_the_split_is_correct_across_a_chunk_boundary():
    # 250 rows at chunksize=100 is three execute_values() round trips; the
    # counts must accumulate across all of them, not report only the last.
    old = [uuid.uuid4().hex[:8] for _ in range(100)]
    new = [uuid.uuid4().hex[:8] for _ in range(150)]
    upsert_dataframe(_frame(old, "v1"), TEST_SCHEMA, TEST_TABLE,
                     conflict_columns=CONFLICT, chunksize=100)

    result = upsert_dataframe(_frame(old + new, "v2"), TEST_SCHEMA, TEST_TABLE,
                              conflict_columns=CONFLICT, chunksize=100)

    assert result == {"attempted": 250, "affected": 250, "inserted": 150, "updated": 100}


def test_empty_dataframe_returns_an_all_zero_result():
    result = upsert_dataframe(pd.DataFrame(), TEST_SCHEMA, TEST_TABLE,
                              conflict_columns=CONFLICT)

    assert result == {"attempted": 0, "affected": 0, "inserted": 0, "updated": 0}


def test_collect_upserts_captures_every_call_in_the_block():
    keys = [uuid.uuid4().hex[:8] for _ in range(2)]

    with collect_upserts() as collected:
        upsert_dataframe(_frame(keys, "v1"), TEST_SCHEMA, TEST_TABLE,
                         conflict_columns=CONFLICT)
        upsert_dataframe(_frame(keys, "v2"), TEST_SCHEMA, TEST_TABLE,
                         conflict_columns=CONFLICT)

    assert len(collected) == 2
    assert collected[0]["table"] == TEST_TABLE
    assert collected[0]["schema"] == TEST_SCHEMA
    assert collected[0]["inserted"] == 2
    assert collected[1]["updated"] == 2


def test_collect_upserts_is_inert_outside_the_block():
    # A collector left armed would accumulate forever inside the long-lived
    # Streamlit process. Leaving the block must restore the previous state.
    keys = [uuid.uuid4().hex[:8]]
    with collect_upserts() as collected:
        pass
    upsert_dataframe(_frame(keys, "v1"), TEST_SCHEMA, TEST_TABLE, conflict_columns=CONFLICT)

    assert collected == []


def test_collect_upserts_restores_an_outer_collector_after_an_inner_block():
    keys = [uuid.uuid4().hex[:8]]
    with collect_upserts() as outer:
        with collect_upserts() as inner:
            upsert_dataframe(_frame(keys, "v1"), TEST_SCHEMA, TEST_TABLE,
                             conflict_columns=CONFLICT)
        upsert_dataframe(_frame(keys, "v2"), TEST_SCHEMA, TEST_TABLE,
                         conflict_columns=CONFLICT)

    assert len(inner) == 1
    assert len(outer) == 1
    assert outer[0]["updated"] == 1
