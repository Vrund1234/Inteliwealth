"""utils/dedupe_hash.py: the hashed, indexed replacement for each bronze
loader's O(entire bronze history) full-table read-and-compare duplicate
check. hash_normalized_rows() does no normalization itself -- each loader
keeps its own existing per-column normalization (they differ slightly
between loaders on purpose) and only the final join-then-hash step is
shared."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from utils.dedupe_hash import hash_normalized_rows, compute_flag_via_row_hash

TEST_SCHEMA = "bronze"
TEST_TABLE = "_test_dedupe_hash"
SENTINEL = "__TEST_DEDUPE_HASH__"


@pytest.fixture(autouse=True)
def temp_table():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{TEST_TABLE} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                row_hash TEXT
            )
        """))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS _test_dedupe_hash_row_hash_idx "
            f"ON {TEST_SCHEMA}.{TEST_TABLE} (row_hash)"
        ))
        conn.execute(text(
            f"DELETE FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL})
    yield
    with engine.begin() as conn:
        conn.execute(text(
            f"DELETE FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source = :s"
        ), {"s": SENTINEL})


def test_identical_normalized_rows_get_identical_hash():
    df = pd.DataFrame([
        {"key_col": "A", "value_col": "1"},
        {"key_col": "A", "value_col": "1"},
    ])
    hashes = hash_normalized_rows(df, ["key_col", "value_col"])
    assert hashes.iloc[0] == hashes.iloc[1]


def test_rows_differing_in_any_compared_column_get_different_hash():
    df = pd.DataFrame([
        {"key_col": "A", "value_col": "1"},
        {"key_col": "A", "value_col": "2"},
    ])
    hashes = hash_normalized_rows(df, ["key_col", "value_col"])
    assert hashes.iloc[0] != hashes.iloc[1]


def test_hash_ignores_columns_not_in_compare_cols():
    df = pd.DataFrame([
        {"key_col": "A", "value_col": "1", "ignored": "x"},
        {"key_col": "A", "value_col": "1", "ignored": "y"},
    ])
    hashes = hash_normalized_rows(df, ["key_col", "value_col"])
    assert hashes.iloc[0] == hashes.iloc[1]


def test_compute_flag_via_row_hash_flags_a_hash_already_in_the_table():
    key = uuid.uuid4().hex[:8]
    seed_df = pd.DataFrame([{"key_col": key, "value_col": "1"}])
    seed_hash = hash_normalized_rows(seed_df, ["key_col", "value_col"]).iloc[0]
    with engine.begin() as conn:
        conn.execute(text(
            f"INSERT INTO {TEST_SCHEMA}.{TEST_TABLE} (source, key_col, value_col, row_hash) "
            f"VALUES (:s, :k, :v, :h)"
        ), {"s": SENTINEL, "k": key, "v": "1", "h": seed_hash})

    new_df = pd.DataFrame([{"key_col": key, "value_col": "1"}])  # exact duplicate
    row_hash, flag = compute_flag_via_row_hash(
        new_df, ["key_col", "value_col"], TEST_SCHEMA, TEST_TABLE, engine
    )
    assert flag.iloc[0] == 1
    assert row_hash.iloc[0] == seed_hash


def test_compute_flag_via_row_hash_does_not_flag_a_new_row():
    new_df = pd.DataFrame([{"key_col": uuid.uuid4().hex[:8], "value_col": "brand-new"}])
    _, flag = compute_flag_via_row_hash(
        new_df, ["key_col", "value_col"], TEST_SCHEMA, TEST_TABLE, engine
    )
    assert flag.iloc[0] == 0


def test_compute_flag_via_row_hash_handles_empty_dataframe():
    empty_df = pd.DataFrame(columns=["key_col", "value_col"])
    row_hash, flag = compute_flag_via_row_hash(
        empty_df, ["key_col", "value_col"], TEST_SCHEMA, TEST_TABLE, engine
    )
    assert len(row_hash) == 0
    assert len(flag) == 0
