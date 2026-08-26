"""upsert_dataframe: INSERT ... ON CONFLICT ... DO UPDATE, generic over any
table -- replaces the repo-wide pattern of df.to_sql(if_exists="append"),
which has no protection against re-inserting the same natural key twice."""

import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine, upsert_dataframe

TEST_SCHEMA = "bronze"
TEST_TABLE = "_test_upsert_dataframe"
SENTINEL = "__TEST_UPSERT_DATAFRAME__"


@pytest.fixture(autouse=True)
def temp_table():
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{TEST_TABLE} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT _test_upsert_dataframe_uq UNIQUE (source, key_col)
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


def _read_all():
    with engine.begin() as conn:
        rows = conn.execute(text(
            f"SELECT source, key_col, value_col FROM {TEST_SCHEMA}.{TEST_TABLE} "
            f"WHERE source = :s ORDER BY key_col"
        ), {"s": SENTINEL}).fetchall()
    return [dict(r._mapping) for r in rows]


def test_first_insert_creates_the_row():
    key = uuid.uuid4().hex[:8]
    df = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])

    n = upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    assert n == 1
    assert _read_all() == [{"source": SENTINEL, "key_col": key, "value_col": "v1"}]


def test_conflicting_insert_updates_in_place_not_duplicates():
    key = uuid.uuid4().hex[:8]
    df1 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])
    df2 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v2"}])

    upsert_dataframe(df1, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])
    upsert_dataframe(df2, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    rows = _read_all()
    assert len(rows) == 1
    assert rows[0]["value_col"] == "v2"


def test_updated_at_is_bumped_on_conflict():
    key = uuid.uuid4().hex[:8]
    df = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])
    upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    with engine.begin() as conn:
        first_ts = conn.execute(text(
            f"SELECT updated_at FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).scalar()
        conn.execute(text(
            f"UPDATE {TEST_SCHEMA}.{TEST_TABLE} SET updated_at = updated_at - interval '1 day' "
            f"WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key})

    upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    with engine.begin() as conn:
        second_ts = conn.execute(text(
            f"SELECT updated_at FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).scalar()

    assert second_ts > first_ts


def test_updated_at_in_df_is_ignored_and_still_forced_to_now():
    """Even when the incoming DataFrame carries its own updated_at value
    (e.g. a stale value from a prior extract), the conflict path must still
    set updated_at = now() -- never EXCLUDED.updated_at -- per the
    function's documented contract that updated_at_column is *always* set
    to now() on conflict, whether or not it's a column in df."""
    key = uuid.uuid4().hex[:8]
    dummy_old = datetime(2000, 1, 1, tzinfo=timezone.utc)

    df1 = pd.DataFrame([{
        "source": SENTINEL, "key_col": key, "value_col": "v1", "updated_at": dummy_old,
    }])
    upsert_dataframe(df1, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    df2 = pd.DataFrame([{
        "source": SENTINEL, "key_col": key, "value_col": "v2", "updated_at": dummy_old,
    }])
    before = datetime.now(timezone.utc)
    upsert_dataframe(df2, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    with engine.begin() as conn:
        ts = conn.execute(text(
            f"SELECT updated_at FROM {TEST_SCHEMA}.{TEST_TABLE} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).scalar()

    assert ts > dummy_old + timedelta(days=1000)
    assert ts >= before - timedelta(seconds=5)


def test_updated_at_column_none_skips_the_clause_on_a_table_with_no_such_column():
    """Most gold tables in this project have NO updated_at (or equivalent)
    column at all -- only gold.holdings has last_synced_at. Calling
    upsert_dataframe on such a table with the default updated_at_column
    would raise psycopg2.errors.UndefinedColumn the first time a real
    conflict occurs (a fresh INSERT with no existing row never touches the
    SET clause, so the bug stays dormant until re-processing an existing
    natural key -- this is exactly what happened live against gold.holdings
    during the functional test). updated_at_column=None must skip the
    clause entirely and still upsert correctly."""
    table = "_test_upsert_dataframe_no_updated_at"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                CONSTRAINT _test_upsert_dataframe_no_updated_at_uq UNIQUE (source, key_col)
            )
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})

    key = uuid.uuid4().hex[:8]
    df1 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])
    df2 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v2"}])

    n1 = upsert_dataframe(df1, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column=None)
    n2 = upsert_dataframe(df2, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column=None)

    assert n1 == 1
    assert n2 == 1
    with engine.begin() as conn:
        rows = conn.execute(text(
            f"SELECT value_col FROM {TEST_SCHEMA}.{table} WHERE source = :s"
        ), {"s": SENTINEL}).fetchall()
    assert [r[0] for r in rows] == ["v2"]

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})


def test_updated_at_column_can_target_a_differently_named_column():
    """gold.holdings uses last_synced_at instead of updated_at -- the
    parameter must accept any column name, not just the literal string
    "updated_at"."""
    table = "_test_upsert_dataframe_last_synced"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT _test_upsert_dataframe_last_synced_uq UNIQUE (source, key_col)
            )
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})

    key = uuid.uuid4().hex[:8]
    df = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])
    upsert_dataframe(df, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column="last_synced_at")

    with engine.begin() as conn:
        first_ts = conn.execute(text(
            f"SELECT last_synced_at FROM {TEST_SCHEMA}.{table} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).scalar()
        conn.execute(text(
            f"UPDATE {TEST_SCHEMA}.{table} SET last_synced_at = last_synced_at - interval '1 day' "
            f"WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key})

    upsert_dataframe(df, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column="last_synced_at")

    with engine.begin() as conn:
        second_ts = conn.execute(text(
            f"SELECT last_synced_at FROM {TEST_SCHEMA}.{table} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).scalar()

    assert second_ts > first_ts

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})


def test_created_at_is_never_refreshed_from_excluded_on_conflict():
    """Every gold/silver loader sets created_at to "now" before calling
    upsert_dataframe(), and several extraction functions (e.g.
    get_last_gold_timestamp() in etl_gold_transaction.py) read
    MAX(created_at) as an incremental watermark -- the same column the
    dedup migration used to decide which duplicate row to keep. If a
    conflict-update refreshed created_at from EXCLUDED, a re-processed row
    would silently lose its original first-seen timestamp on every rerun.
    created_at must be excluded from the SET clause, mirroring how
    updated_at_column is already excluded."""
    table = "_test_upsert_dataframe_created_at"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT,
                key_col TEXT,
                value_col TEXT,
                created_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT _test_upsert_dataframe_created_at_uq UNIQUE (source, key_col)
            )
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})

    key = uuid.uuid4().hex[:8]
    original_created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    reprocess_created_at = datetime(2026, 8, 25, tzinfo=timezone.utc)

    df1 = pd.DataFrame([{
        "source": SENTINEL, "key_col": key, "value_col": "v1", "created_at": original_created_at,
    }])
    upsert_dataframe(df1, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column=None)

    df2 = pd.DataFrame([{
        "source": SENTINEL, "key_col": key, "value_col": "v2", "created_at": reprocess_created_at,
    }])
    n = upsert_dataframe(df2, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column=None)

    assert n == 1
    with engine.begin() as conn:
        row = conn.execute(text(
            f"SELECT value_col, created_at FROM {TEST_SCHEMA}.{table} WHERE source=:s AND key_col=:k"
        ), {"s": SENTINEL, "k": key}).fetchone()

    # value_col still gets refreshed (proves the conflict path ran and this
    # isn't just a DO NOTHING no-op) but created_at keeps its original value.
    assert row.value_col == "v2"
    assert row.created_at == original_created_at

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})


def test_nat_in_a_datetime_column_is_stored_as_null_not_the_string_nat():
    """pandas silently coerces None back into NaT when assigned into an
    already-datetime64-dtype column -- df.where(pd.notnull(df), None)
    alone does NOT turn a NaT into a real None for such a column, so the
    literal NaT reaches psycopg2 as an unparseable timestamp and raises
    psycopg2.errors.InvalidDatetimeFormat. Hit live against
    gold.folio_nominees.dob during Task 8's functional test."""
    table = "_test_upsert_dataframe_nat"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT,
                key_col TEXT,
                dob DATE,
                CONSTRAINT _test_upsert_dataframe_nat_uq UNIQUE (source, key_col)
            )
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})

    key = uuid.uuid4().hex[:8]
    # A real datetime64 column containing NaT, exactly as pd.to_datetime()
    # produces for a missing/unparseable source value -- NOT a plain Python
    # None, which is the scenario df.where(pd.notnull(df), None) fails on.
    df = pd.DataFrame([{"source": SENTINEL, "key_col": key, "dob": pd.NaT}])
    assert df["dob"].dtype.kind == "M"  # datetime64 -- confirms the real-world shape

    n = upsert_dataframe(df, TEST_SCHEMA, table, conflict_columns=["source", "key_col"], updated_at_column=None)

    assert n == 1
    with engine.begin() as conn:
        stored = conn.execute(text(
            f"SELECT dob FROM {TEST_SCHEMA}.{table} WHERE source = :s AND key_col = :k"
        ), {"s": SENTINEL, "k": key}).scalar()
    assert stored is None

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})


def test_conflict_constraint_updates_in_place_not_duplicates():
    """conflict_constraint targets a named table CONSTRAINT built on plain
    columns -- mirrors test_conflicting_insert_updates_in_place_not_duplicates
    but exercises the ON CONFLICT ON CONSTRAINT {name} code path end-to-end
    instead of only the ValueError branch of the mutual-exclusivity test."""
    key = uuid.uuid4().hex[:8]
    df1 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v1"}])
    df2 = pd.DataFrame([{"source": SENTINEL, "key_col": key, "value_col": "v2"}])

    n1 = upsert_dataframe(df1, TEST_SCHEMA, TEST_TABLE, conflict_constraint="_test_upsert_dataframe_uq")
    n2 = upsert_dataframe(df2, TEST_SCHEMA, TEST_TABLE, conflict_constraint="_test_upsert_dataframe_uq")

    assert n1 == 1
    assert n2 == 1
    rows = _read_all()
    assert len(rows) == 1
    assert rows[0]["value_col"] == "v2"


def test_two_different_keys_both_survive():
    k1, k2 = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    df = pd.DataFrame([
        {"source": SENTINEL, "key_col": k1, "value_col": "v1"},
        {"source": SENTINEL, "key_col": k2, "value_col": "v2"},
    ])

    upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])

    assert len(_read_all()) == 2


def test_empty_dataframe_is_a_noop():
    df = pd.DataFrame(columns=["source", "key_col", "value_col"])
    n = upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE, conflict_columns=["source", "key_col"])
    assert n == 0


def test_requires_exactly_one_of_the_three_conflict_targets():
    df = pd.DataFrame([{"source": SENTINEL, "key_col": "x", "value_col": "v"}])
    with pytest.raises(ValueError):
        upsert_dataframe(df, TEST_SCHEMA, TEST_TABLE)
    with pytest.raises(ValueError):
        upsert_dataframe(
            df, TEST_SCHEMA, TEST_TABLE,
            conflict_columns=["source", "key_col"],
            conflict_constraint="_test_upsert_dataframe_uq",
        )
    with pytest.raises(ValueError):
        upsert_dataframe(
            df, TEST_SCHEMA, TEST_TABLE,
            conflict_columns=["source", "key_col"],
            conflict_index_expr='"source", "key_col"',
        )


@pytest.fixture
def temp_table_with_expression_index():
    """A second temp table whose uniqueness lives on an expression index,
    the same situation as silver.sip_master_new / gold.sip -- Postgres
    cannot promote an expression index to a table CONSTRAINT (confirmed
    live: "Cannot create a primary key or unique constraint using such an
    index"), so conflict_index_expr must be able to target it directly."""
    table = "_test_upsert_dataframe_expr"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT,
                raw_code TEXT,
                value_col TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS _test_upsert_dataframe_expr_uq
                ON {TEST_SCHEMA}.{table} (source, (COALESCE(NULLIF(BTRIM(raw_code), ''), '')))
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})
    yield table
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})


def test_conflict_index_expr_targets_an_expression_index():
    """conflict_index_expr is a raw ON CONFLICT (...) target string, used
    when the unique index isn't a plain column list (and therefore can't be
    named via conflict_constraint either, since it can't become a table
    CONSTRAINT at all)."""
    table = "_test_upsert_dataframe_expr"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{table} (
                source TEXT, raw_code TEXT, value_col TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text(f"""
            CREATE UNIQUE INDEX IF NOT EXISTS _test_upsert_dataframe_expr_uq
                ON {TEST_SCHEMA}.{table} (source, (COALESCE(NULLIF(BTRIM(raw_code), ''), '')))
        """))
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})

    df1 = pd.DataFrame([{"source": SENTINEL, "raw_code": "0", "value_col": "v1"}])
    df2 = pd.DataFrame([{"source": SENTINEL, "raw_code": "", "value_col": "v2"}])

    n1 = upsert_dataframe(
        df1, TEST_SCHEMA, table,
        conflict_index_expr='"source", (COALESCE(NULLIF(BTRIM("raw_code"), \'\'), \'\'))',
    )
    # raw_code="0" -> COALESCE(NULLIF(BTRIM('0'),''),'') = '0' (NOT blank,
    # unlike the real ft_sip_regno case which also strips the literal '0'
    # placeholder -- this test table intentionally does NOT strip '0', to
    # prove the helper passes the expression through verbatim rather than
    # baking in that specific business rule).
    n2 = upsert_dataframe(
        df2, TEST_SCHEMA, table,
        conflict_index_expr='"source", (COALESCE(NULLIF(BTRIM("raw_code"), \'\'), \'\'))',
    )

    assert n1 == 1
    assert n2 == 1
    with engine.begin() as conn:
        rows = conn.execute(text(
            f"SELECT raw_code, value_col FROM {TEST_SCHEMA}.{table} WHERE source = :s ORDER BY raw_code"
        ), {"s": SENTINEL}).fetchall()
    # "0" and "" produce different COALESCE results here, so both rows
    # survive as distinct -- proves the expression, not some hardcoded
    # column list, is what's actually being matched.
    assert len(rows) == 2

    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {TEST_SCHEMA}.{table} WHERE source = :s"), {"s": SENTINEL})
