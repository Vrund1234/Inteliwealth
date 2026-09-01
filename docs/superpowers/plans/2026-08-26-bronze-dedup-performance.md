# Bronze Duplicate-Flag Performance Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `O(entire bronze history)` Python read-and-compare that computes each bronze loader's `flag` column with an `O(new batch size)` hashed, indexed Postgres lookup — same full-row duplicate semantics, no behavior change, no schema constraint added.

**Architecture:** Add a `row_hash TEXT` column (SHA-256 of the same normalized, full-row comparison each loader already computes) to all three bronze tables, indexed but not unique. Each loader hashes only its incoming batch and asks Postgres which of those specific hashes already exist — never reads the whole table again. A one-time backfill populates `row_hash` for rows that predate this change.

**Tech Stack:** Python 3.13, pandas, SQLAlchemy + psycopg2, PostgreSQL 17.10, pytest.

**Spec:** [2026-08-26-bronze-dedup-performance-design.md](../specs/2026-08-26-bronze-dedup-performance-design.md)

## Global Constraints

- Bronze stays append-only: no unique constraint, no `ON CONFLICT`, no rejected inserts, no in-place updates (including for `bronze.investor_master` — confirmed decision, not revisited here).
- `flag` semantics must not change: `flag=1` means "this exact row (all columns except `flag`/`created_at`/`updated_at`/`source`) already exists in bronze," `flag=0` means "new" — silver's `WHERE flag = 0` depends on this.
- `created_at` must keep being set once per row, at insert time, and must never be rewritten on any later run (bronze never `UPDATE`s an existing row).
- All DB-hitting tests in this codebase run against the real live database via `utils.db.engine`/`master_engine` (no mocking) using dedicated `_test_*` throwaway tables or sentinel-scoped rows in real tables — follow `tests/test_upsert_dataframe.py`'s existing fixture style exactly.
- Every new index is built `CONCURRENTLY` (no write lock), matching `sql_scripts/dedup_constraints_migration_2026-08-25.sql`'s convention.
- Do not fix `etl_investor_master.py`'s own ambiguous-date bug in its duplicate-flag comparison (same bug class as the SIP fix, found but explicitly out of scope here) — preserve its current behavior verbatim when extracting it into a named function.

---

## Task 1: Shared row-hash utility

**Files:**
- Create: `python_scripts/utils/dedupe_hash.py`
- Test: `python_scripts/tests/test_dedupe_hash.py`

**Interfaces:**
- Produces: `hash_normalized_rows(normalized_df: pd.DataFrame, compare_cols: list[str]) -> pd.Series[str]` — one SHA-256 hex digest per row, computed from `compare_cols` values the caller has *already* normalized (this function does no normalization of its own).
- Produces: `compute_flag_via_row_hash(new_df: pd.DataFrame, compare_cols: list[str], schema: str, table: str, engine) -> tuple[pd.Series[str], pd.Series[int]]` — returns `(row_hash, flag)`; looks up only the new batch's hashes against `schema.table.row_hash` (indexed, never reads the whole table).

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_dedupe_hash.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_dedupe_hash.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.dedupe_hash'` (or `ImportError`).

- [ ] **Step 3: Write minimal implementation**

```python
# python_scripts/utils/dedupe_hash.py
"""Hashed, indexed replacement for the O(entire bronze history) Python
read-and-compare each bronze loader (etl_trans.py, etl_investor_master.py,
etl_sip.py) used to compute its `flag` column. See
docs/superpowers/specs/2026-08-26-bronze-dedup-performance-design.md.

This module deliberately does NOT normalize column values itself -- each
loader has its own established, slightly different per-column
normalization (e.g. etl_sip.py case-folds with .str.upper(), etl_trans.py
does not) and preserving those differences exactly is required (see the
spec's "What 'duplicate' means today" section). Callers must pass an
already-normalized DataFrame; this module only joins and hashes it.
"""

import hashlib

import pandas as pd
from sqlalchemy import bindparam, text


def hash_normalized_rows(normalized_df, compare_cols):
    """One SHA-256 hex digest per row, from `compare_cols` values the
    caller has already normalized into plain strings.

    Fields are length-prefixed (netstring-style: "<len>:<value>" per field,
    concatenated) rather than joined with a plain delimiter -- a bare
    "|".join is ambiguous across a column boundary (e.g. a="X|Y", b="Z" and
    a="X", b="Y|Z" would join to the identical string), which could
    silently violate full-row duplicate semantics if a "|" ever appears in
    a free-text bronze column (an investor name/address, for instance).
    Length-prefixing removes the ambiguity regardless of field content."""
    def _encode_row(values):
        return "".join(f"{len(v)}:{v}" for v in values)

    joined = normalized_df[compare_cols].astype(str).agg(_encode_row, axis=1)
    return joined.apply(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def compute_flag_via_row_hash(new_df, compare_cols, schema, table, engine):
    """Hash the incoming batch and check only those specific hashes against
    schema.table.row_hash (indexed lookup -- never reads the whole table).

    Returns (row_hash, flag): `row_hash` to store on the new rows being
    inserted, `flag` (0/1) with the same meaning bronze's flag has always
    had -- 1 if this exact row already exists in bronze, 0 if new.
    """
    row_hash = hash_normalized_rows(new_df, compare_cols)

    if row_hash.empty:
        return row_hash, row_hash.astype(int)

    query = text(
        f"SELECT row_hash FROM {schema}.{table} WHERE row_hash IN :hashes"
    ).bindparams(bindparam("hashes", expanding=True))

    with engine.begin() as conn:
        existing_hashes = {
            r[0] for r in conn.execute(query, {"hashes": list(row_hash.unique())})
        }

    flag = row_hash.isin(existing_hashes).astype(int)
    return row_hash, flag
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_dedupe_hash.py -v`
Expected: PASS, all 6 tests, no warnings.

- [ ] **Step 5: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add python_scripts/utils/dedupe_hash.py python_scripts/tests/test_dedupe_hash.py
git commit -m "Add hashed, indexed row-duplicate lookup (utils/dedupe_hash.py)

First piece of the bronze dedup performance fix: hash_normalized_rows()
+ compute_flag_via_row_hash() replace the O(entire table) Python
read-and-compare with an O(new batch) indexed lookup. Not yet wired
into any loader -- see docs/superpowers/specs/2026-08-26-bronze-dedup-performance-design.md.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Add `row_hash` column to the three bronze tables

**Files:**
- Create: `sql_scripts/bronze_row_hash_migration_2026-08-26.sql`
- Test: `python_scripts/tests/test_bronze_row_hash_migration.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: a live `row_hash TEXT` (nullable) column on `bronze.transaction_master_new`, `bronze.investor_master`, `bronze.sip_master_new`, which Task 3 (backfill) and Task 4 (index + NOT NULL) build on.

- [ ] **Step 1: Write the failing test**

```python
# python_scripts/tests/test_bronze_row_hash_migration.py
"""Post-migration state check for the 2026-08-26 bronze row_hash column,
mirroring tests/test_dedup_migration.py's pattern of asserting live schema
state after a migration runs."""

import pandas as pd

from utils.db import engine

TABLES = [
    "transaction_master_new",
    "investor_master",
    "sip_master_new",
]


def test_row_hash_column_exists_on_all_three_bronze_tables():
    columns = pd.read_sql(
        """
        SELECT table_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = ANY(%(tables)s)
          AND column_name = 'row_hash'
        """,
        engine,
        params={"tables": TABLES},
    )
    found = set(columns["table_name"])
    missing = set(TABLES) - found
    assert not missing, f"row_hash column missing on: {missing}"
    assert (columns["data_type"] == "text").all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_bronze_row_hash_migration.py -v`
Expected: FAIL — `missing = {'transaction_master_new', 'investor_master', 'sip_master_new'}` (column doesn't exist yet).

- [ ] **Step 3: Write and run the migration**

```sql
-- sql_scripts/bronze_row_hash_migration_2026-08-26.sql
-- ============================================================
-- Bronze row_hash column -- see
-- docs/superpowers/specs/2026-08-26-bronze-dedup-performance-design.md
--
-- Nullable for now: Task 3 backfills existing rows, Task 4 builds the
-- index CONCURRENTLY and only then sets NOT NULL.
-- ============================================================

ALTER TABLE bronze.transaction_master_new ADD COLUMN IF NOT EXISTS row_hash TEXT;
ALTER TABLE bronze.investor_master        ADD COLUMN IF NOT EXISTS row_hash TEXT;
ALTER TABLE bronze.sip_master_new         ADD COLUMN IF NOT EXISTS row_hash TEXT;
```

Run: `psql -h localhost -p 5433 -U postgres -d "25_08_2025_intelliwealth_layer_db" -f sql_scripts/bronze_row_hash_migration_2026-08-26.sql`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_bronze_row_hash_migration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add sql_scripts/bronze_row_hash_migration_2026-08-26.sql python_scripts/tests/test_bronze_row_hash_migration.py
git commit -m "Add nullable row_hash column to the three bronze tables

Migration applied live. Nullable until Task 3 backfills existing rows
and Task 4 sets NOT NULL after the index is built.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Backfill `row_hash` for existing bronze rows

**Files:**
- Create: `python_scripts/backfill_bronze_row_hash.py`
- Test: `python_scripts/tests/test_backfill_bronze_row_hash.py`

**Interfaces:**
- Consumes: `hash_normalized_rows` from `utils/dedupe_hash.py` (Task 1); the `row_hash` column from Task 2.
- Produces: every existing row in all three bronze tables gets a populated `row_hash`. This is a one-time script — its own per-table normalization is intentionally a frozen, verbatim copy of each loader's current inline comparison normalization (not imported from the loaders), so this migration stays reproducible and auditable independent of later loader refactors (Tasks 5-7 extract and reuse the same logic going forward, but do not change what this backfill already committed to bronze).

- [ ] **Step 1: Write the failing test**

```python
# python_scripts/tests/test_backfill_bronze_row_hash.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_backfill_bronze_row_hash.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backfill_bronze_row_hash'`.

- [ ] **Step 3: Write minimal implementation**

```python
# python_scripts/backfill_bronze_row_hash.py
"""One-time backfill: populate row_hash for every bronze row that predates
the 2026-08-26 dedup-performance migration. Run once, after Task 2's
ALTER TABLE and before Task 4's index + NOT NULL.

Each table's normalization below DIRECTLY IMPORTS that loader's own real,
current comparison logic (functions and constants) rather than hand-copying
it -- `etl_trans.prepare_for_comparison` already exists standalone and is
reused as-is; `etl_investor_master`/`etl_sip`'s `clean_identifier_columns`
and `DATE_COLUMNS` are imported directly rather than re-listed, so this
script cannot silently drift from what those modules actually do today.
This includes reproducing `etl_investor_master.py`'s own currently-unfixed
ambiguous `pd.to_datetime` date handling verbatim (via that exact call,
inline below, matching its ONLY date branch) -- deliberately not fixed
here, per the Global Constraints -- and `etl_sip.py`'s case-insensitive
`.str.upper()` convention on non-date columns.
"""

import sys

import pandas as pd
from sqlalchemy import text

from utils.db import engine
from utils.dedupe_hash import hash_normalized_rows

import etl_trans
import etl_investor_master
import etl_sip


def _normalize_transaction(df, compare_cols):
    """Reuses etl_trans.py's own comparison normalizer directly -- this IS
    the function etl_trans.process_transactions() already calls."""
    return etl_trans.prepare_for_comparison(df, compare_cols)


def _normalize_investor(df, compare_cols):
    """Matches etl_investor_master.py's current inline comparison
    normalization exactly, replicating its full layered pipeline:
    normalize() FIRST (quote-stripping and null-token cleanup -- both the
    new batch, via process_investor_master() calling normalize() right
    after mapping, and the existing-read path already apply this before
    any comparison ever happens; normalize() itself skips DATE_COLUMNS),
    then clean_identifier_columns, then a final per-column pass: bare
    pd.to_datetime for its DATE_COLUMNS (verbatim, including the
    ambiguous-date behavior -- out of scope to fix here), else
    fillna("").astype(str).str.strip() again (redundant with normalize()
    but matches production's actual, harmless double-cleaning) -- no
    case-fold, this loader's comparison is case-sensitive."""
    df = etl_investor_master.normalize(df[compare_cols].copy())
    df = etl_investor_master.clean_identifier_columns(df)
    for col in compare_cols:
        if col in etl_investor_master.DATE_COLUMNS:
            df[col] = (
                pd.to_datetime(df[col], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("")
            )
        else:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def _normalize_sip(df, compare_cols):
    """Matches etl_sip.py's current inline comparison normalization
    exactly: normalize() FIRST (numeric coercion via pd.to_numeric for
    auto_amount/no_of_installments/top_up_amt/top_up_perc -- both the new
    batch, via apply_sip_mapping(), and the existing-read path already
    apply this before any comparison ever happens), then
    clean_identifier_columns, then dedupe_compare_date (the already-fixed
    deterministic parser) for its DATE_COLUMNS, else fillna("").astype(str)
    .str.strip().str.upper() -- this loader's comparison IS
    case-insensitive, unlike the other two. Skipping the normalize() step
    would hash "1000.00" and "1000.0" as different values in
    auto_amount/etc. even though production treats them as the same
    number."""
    df = etl_sip.normalize(df[compare_cols].copy())
    df = etl_sip.clean_identifier_columns(df)
    for col in compare_cols:
        if col in etl_sip.DATE_COLUMNS:
            df[col] = df[col].apply(etl_sip.dedupe_compare_date)
        else:
            df[col] = df[col].fillna("").astype(str).str.strip().str.upper()
    return df


def backfill_table(schema, table, compare_cols, normalize_fn, sentinel_source=None):
    """Compute and store row_hash for every existing row in schema.table.
    `normalize_fn` is one of `_normalize_transaction`/`_normalize_investor`/
    `_normalize_sip` above (tests may pass any callable with the same
    `(df, compare_cols) -> df` shape against a throwaway table).
    `sentinel_source`, when given, restricts the backfill to that source
    value -- used only by tests against throwaway tables; production calls
    (see __main__ below) omit it to cover every row."""
    where_clause = ""
    params = {}
    if sentinel_source is not None:
        where_clause = "WHERE source = :sentinel_source"
        params["sentinel_source"] = sentinel_source

    df = pd.read_sql(
        text(f"SELECT ctid::text AS _ctid, * FROM {schema}.{table} {where_clause}"),
        engine,
        params=params,
    )
    if df.empty:
        return 0

    normalized = normalize_fn(df, compare_cols)
    row_hash = hash_normalized_rows(normalized, compare_cols)

    with engine.begin() as conn:
        for ctid, h in zip(df["_ctid"], row_hash):
            conn.execute(
                text(f"UPDATE {schema}.{table} SET row_hash = :h WHERE ctid = :c"),
                {"h": h, "c": ctid},
            )
    return len(df)


TRANSACTION_COMPARE_COLS_QUERY = """
    SELECT column_name FROM information_schema.columns
    WHERE table_schema = 'bronze' AND table_name = %(table)s
      AND column_name NOT IN ('flag', 'created_at', 'updated_at', 'source', 'row_hash')
    ORDER BY ordinal_position
"""


def _compare_cols_for(table):
    return pd.read_sql(
        TRANSACTION_COMPARE_COLS_QUERY, engine, params={"table": table}
    )["column_name"].tolist()


if __name__ == "__main__":
    for table, normalize_fn in (
        ("transaction_master_new", _normalize_transaction),
        ("investor_master", _normalize_investor),
        ("sip_master_new", _normalize_sip),
    ):
        n = backfill_table(
            "bronze", table, _compare_cols_for(table), normalize_fn
        )
        print(f"Backfilled row_hash for {n} rows in bronze.{table}")
        sys.stdout.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_backfill_bronze_row_hash.py -v`
Expected: PASS, all 3 tests.

- [ ] **Step 5: Run the backfill against the three live bronze tables**

Run: `cd python_scripts && source venv/bin/activate && python backfill_bronze_row_hash.py`
Expected output: three `Backfilled row_hash for N rows in bronze.<table>` lines (N matching each table's current row count — 129,568 / 4,376 / 1,398 as of 2026-08-26, plus anything ingested since).

- [ ] **Step 6: Verify live state — zero NULL row_hash remaining**

Run:
```bash
psql -h localhost -p 5433 -U postgres -d "25_08_2025_intelliwealth_layer_db" -c "
SELECT 'transaction_master_new' AS tbl, COUNT(*) FILTER (WHERE row_hash IS NULL) AS nulls FROM bronze.transaction_master_new
UNION ALL SELECT 'investor_master', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.investor_master
UNION ALL SELECT 'sip_master_new', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.sip_master_new;
"
```
Expected: `nulls = 0` for all three rows. If not, re-run Step 5 (the script is idempotent — it recomputes and overwrites `row_hash` for every row it selects).

- [ ] **Step 7: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add python_scripts/backfill_bronze_row_hash.py python_scripts/tests/test_backfill_bronze_row_hash.py
git commit -m "Backfill row_hash for existing bronze rows

One-time migration script, run live against all three bronze tables.
Verified zero NULL row_hash remaining afterward.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Index `row_hash` and enforce `NOT NULL`

**Files:**
- Create: `sql_scripts/bronze_row_hash_index_and_notnull_2026-08-26.sql`
- Modify: `python_scripts/tests/test_bronze_row_hash_migration.py`

**Interfaces:**
- Consumes: the fully-backfilled `row_hash` column from Task 3 (must run after Task 3, or the `NOT NULL` step fails on live NULLs).
- Produces: an indexed, mandatory `row_hash` column that Tasks 5-7's loader code can rely on for every row, old and new.

- [ ] **Step 1: Extend the migration test (still failing)**

```python
# Append to python_scripts/tests/test_bronze_row_hash_migration.py

def test_row_hash_is_indexed_on_all_three_bronze_tables():
    indexes = pd.read_sql(
        """
        SELECT tablename, indexdef FROM pg_indexes
        WHERE schemaname = 'bronze' AND tablename = ANY(%(tables)s)
          AND indexdef ILIKE '%%row_hash%%'
        """,
        engine,
        params={"tables": TABLES},
    )
    found = set(indexes["tablename"])
    missing = set(TABLES) - found
    assert not missing, f"row_hash index missing on: {missing}"


def test_row_hash_is_not_null_on_all_three_bronze_tables():
    columns = pd.read_sql(
        """
        SELECT table_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = ANY(%(tables)s)
          AND column_name = 'row_hash'
        """,
        engine,
        params={"tables": TABLES},
    )
    still_nullable = columns.loc[columns["is_nullable"] == "YES", "table_name"].tolist()
    assert not still_nullable, f"row_hash still nullable on: {still_nullable}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_bronze_row_hash_migration.py -v`
Expected: FAIL — no index found, column still nullable.

- [ ] **Step 3: Write and run the migration**

```sql
-- sql_scripts/bronze_row_hash_index_and_notnull_2026-08-26.sql
-- ============================================================
-- Run only after backfill_bronze_row_hash.py has been run and confirmed
-- to leave zero NULL row_hash values (Task 3, Step 6). CREATE INDEX
-- CONCURRENTLY cannot run inside a transaction block -- this file has no
-- explicit BEGIN/COMMIT, matching sql_scripts/dedup_constraints_migration_2026-08-25.sql.
-- ============================================================

-- --- Preflight: expect 0 for all three ---
SELECT 'transaction_master_new' AS tbl, COUNT(*) FILTER (WHERE row_hash IS NULL) AS nulls FROM bronze.transaction_master_new
UNION ALL SELECT 'investor_master', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.investor_master
UNION ALL SELECT 'sip_master_new', COUNT(*) FILTER (WHERE row_hash IS NULL) FROM bronze.sip_master_new;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_txn_row_hash
    ON bronze.transaction_master_new (row_hash);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_investor_row_hash
    ON bronze.investor_master (row_hash);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_bronze_sip_row_hash
    ON bronze.sip_master_new (row_hash);

ALTER TABLE bronze.transaction_master_new ALTER COLUMN row_hash SET NOT NULL;
ALTER TABLE bronze.investor_master        ALTER COLUMN row_hash SET NOT NULL;
ALTER TABLE bronze.sip_master_new         ALTER COLUMN row_hash SET NOT NULL;
```

Run:
1. Run the preflight `SELECT` first (copy just that statement) and confirm all three `nulls` are 0. If not, stop and re-run Task 3.
2. `psql -h localhost -p 5433 -U postgres -d "25_08_2025_intelliwealth_layer_db" -f sql_scripts/bronze_row_hash_index_and_notnull_2026-08-26.sql`

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_bronze_row_hash_migration.py -v`
Expected: PASS, all 4 tests in the file.

- [ ] **Step 5: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add sql_scripts/bronze_row_hash_index_and_notnull_2026-08-26.sql python_scripts/tests/test_bronze_row_hash_migration.py
git commit -m "Index bronze.row_hash and enforce NOT NULL

Migration applied live, CONCURRENTLY, after confirming zero NULLs from
the Task 3 backfill. row_hash is now indexed and mandatory going forward.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Switch `etl_trans.py` to the hashed, indexed flag check

**Files:**
- Modify: `python_scripts/etl_trans.py` (the `# READ EXISTING BRONZE TABLE` / `# DUPLICATE FLAG` / `# GET DATABASE COLUMN ORDER` sections inside `process_transactions()`)
- Test: `python_scripts/tests/test_etl_trans_row_hash_flag.py`

**Interfaces:**
- Consumes: `compute_flag_via_row_hash` from `utils/dedupe_hash.py` (Task 1); the indexed, `NOT NULL` `row_hash` column (Tasks 2-4); `prepare_for_comparison` (already exists in `etl_trans.py`, unchanged).
- Produces: `process_transactions()` no longer reads the whole `bronze.transaction_master_new` table; `df["row_hash"]` is now set before insert alongside `df["flag"]`.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_etl_trans_row_hash_flag.py
"""process_transactions() must keep computing `flag` with full-row
duplicate semantics (see the 2026-08-26 spec) but without ever reading the
whole bronze.transaction_master_new table. Uses sentinel-scoped rows in
the real table (it's hardcoded to that table name, same as every other
bronze loader in this codebase)."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_trans import process_transactions

# process_transactions(cams=...) hardcodes source="CAMS" internally (not
# caller-controllable) -- these tests scope cleanup/assertions by a random
# trxnno per test instead of a sentinel source value.


@pytest.fixture
def trxnno():
    t = uuid.uuid4().hex[:10]
    yield t
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.transaction_master_new WHERE trxnno = :t"
        ), {"t": t})


def _cams_row(trxnno, folio_no, amount, units, td_ptrno=""):
    return {
        "TRXNNO": trxnno, "FOLIO_NO": folio_no, "AMOUNT": amount, "UNITS": units,
        "TD_PTRNO": td_ptrno, "PRODCODE": "P1", "SCHEME": "S1",
    }


def test_exact_duplicate_of_an_existing_row_is_flagged_1(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)  # first insert: flag=0

    cams_again = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams_again)  # exact resend: flag=1

    result = pd.read_sql(
        "SELECT flag FROM bronze.transaction_master_new WHERE trxnno = :t ORDER BY created_at",
        engine, params={"t": trxnno},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_natural_key_but_different_non_key_column_is_flagged_0(trxnno):
    """The real bronze pattern (td_ptrno/rep_date changing across resends)
    must still reach flag=0, exactly as it does today -- proves this
    change preserves full-row semantics, not just the natural key."""
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)

    cams_resend = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="222")])
    process_transactions(cams=cams_resend)

    result = pd.read_sql(
        "SELECT flag FROM bronze.transaction_master_new WHERE trxnno = :t AND td_ptrno = '222'",
        engine, params={"t": trxnno},
    )
    assert (result["flag"] == 0).all()


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000")])
    process_transactions(cams=cams)

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.transaction_master_new WHERE trxnno = :t",
        engine, params={"t": trxnno},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_created_at_is_never_rewritten_on_a_later_run(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)

    first = pd.read_sql(
        "SELECT created_at FROM bronze.transaction_master_new WHERE trxnno = :t AND td_ptrno = '111'",
        engine, params={"t": trxnno},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams_again)

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.transaction_master_new "
        "WHERE trxnno = :t AND td_ptrno = '111' ORDER BY created_at ASC LIMIT 1",
        engine, params={"t": trxnno},
    )["created_at"].iloc[0]
    assert unchanged == first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_trans_row_hash_flag.py -v`
Expected: FAIL — current code still does the full-table read, but `row_hash` is never set on `df`, so `test_brand_new_row_is_flagged_0_and_gets_a_row_hash` fails on `result["row_hash"].notna().all()` (bronze's `NOT NULL` constraint from Task 4 actually makes the *insert itself* fail here with a `psycopg2.errors.NotNullViolation`, which is also an acceptable, expected failure — confirms the old code path is what's running).

- [ ] **Step 3: Modify `etl_trans.py`**

Find this block inside `process_transactions()` (currently reads the whole table, then builds `old_keys`/`new_keys` from it):

```python
    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.transaction_master_new",
            engine,
        )

        existing = clean_columns(existing)

        existing = normalize(existing)

        existing = clean_identifier_columns(existing)

        # Existing database DATE values are converted using the
        # same deterministic parser.
        existing = format_dates(existing)

        validate_date_columns(
            existing,
            "EXISTING BRONZE DATA",
        )

    except Exception as exc:

        print(
            "Could not read existing bronze.transaction_master_new."
        )
        print("Reason:", exc)

        existing = pd.DataFrame()

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
    }

    if existing.empty:

        df["flag"] = 0

    else:

        compare_cols = [
            c
            for c in df.columns
            if c in existing.columns
            and c not in ignore_cols
        ]

        if not compare_cols:
            raise ValueError(
                "No columns available for duplicate comparison."
            )

        # Prepare both sides using deterministic normalization.
        new_df = prepare_for_comparison(
            df,
            compare_cols,
        )

        old_df = prepare_for_comparison(
            existing,
            compare_cols,
        )

        # Complete-row comparison.
        new_keys = (
            new_df
            .astype(str)
            .agg("|".join, axis=1)
        )

        old_keys = set(
            old_df
            .astype(str)
            .agg("|".join, axis=1)
        )

        df["flag"] = (
            new_keys
            .isin(old_keys)
            .astype(int)
        )

        print("=" * 80)
        print("DUPLICATE CHECK")
        print(f"Rows checked : {len(df)}")
        print(f"Already seen : {(df['flag'] == 1).sum()}")
        print(f"New rows     : {(df['flag'] == 0).sum()}")
        print("=" * 80)

    # =====================================================
    # GET DATABASE COLUMN ORDER
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = 'transaction_master_new'
        ORDER BY ordinal_position
        """,
        engine,
    )["column_name"].tolist()

    if not db_columns:
        raise ValueError(
            "Could not find bronze.transaction_master_new columns."
        )
```

Replace it with:

```python
    # =====================================================
    # GET DATABASE COLUMN ORDER
    # (moved earlier: also used below to derive compare_cols, now that
    # duplicate detection no longer reads the whole existing table)
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = 'transaction_master_new'
        ORDER BY ordinal_position
        """,
        engine,
    )["column_name"].tolist()

    if not db_columns:
        raise ValueError(
            "Could not find bronze.transaction_master_new columns."
        )

    # =====================================================
    # DUPLICATE FLAG -- hashed, indexed lookup (2026-08-26 spec).
    # Same full-row semantics as before: compares every column except
    # flag/created_at/updated_at/source/row_hash. Only the new batch is
    # ever read or normalized -- bronze.transaction_master_new itself is
    # never read in full.
    # =====================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
        "row_hash",
    }

    compare_cols = [
        c
        for c in df.columns
        if c in db_columns
        and c not in ignore_cols
    ]

    if not compare_cols:
        raise ValueError(
            "No columns available for duplicate comparison."
        )

    new_df = prepare_for_comparison(
        df,
        compare_cols,
    )

    df["row_hash"], df["flag"] = compute_flag_via_row_hash(
        new_df,
        compare_cols,
        "bronze",
        "transaction_master_new",
        engine,
    )

    print("=" * 80)
    print("DUPLICATE CHECK")
    print(f"Rows checked : {len(df)}")
    print(f"Already seen : {(df['flag'] == 1).sum()}")
    print(f"New rows     : {(df['flag'] == 0).sum()}")
    print("=" * 80)
```

Add the import near the top of the file, alongside the existing imports:

```python
from utils.db import engine
from utils.dedupe_hash import compute_flag_via_row_hash
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_trans_row_hash_flag.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/ -q`
Expected: same pass count as before this task, plus the 4 new tests — no new failures. (Three pre-existing, unrelated scheme-mapping/AMFI-code test failures are expected and out of scope; confirm no *new* failures appear beyond those.)

- [ ] **Step 6: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add python_scripts/etl_trans.py python_scripts/tests/test_etl_trans_row_hash_flag.py
git commit -m "etl_trans.py: compute bronze flag via hashed indexed lookup

process_transactions() no longer reads the whole
bronze.transaction_master_new table on every run. Same full-row
duplicate semantics, verified: exact duplicate -> flag=1, same natural
key but a different non-key column (the real td_ptrno/rep_date
resend pattern) -> flag=0, unchanged. created_at still never rewritten.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Switch `etl_investor_master.py` to the hashed, indexed flag check

**Files:**
- Modify: `python_scripts/etl_investor_master.py` (extract its inline comparison normalization into `normalize_for_hash`, then the `# READ EXISTING BRONZE TABLE` / `# DUPLICATE FLAG` / `# GET DATABASE COLUMN ORDER` sections inside `process_investor_master()`)
- Test: `python_scripts/tests/test_etl_investor_master_row_hash_flag.py`

**Interfaces:**
- Consumes: `compute_flag_via_row_hash` from `utils/dedupe_hash.py` (Task 1); indexed `NOT NULL` `row_hash` column (Tasks 2-4).
- Produces: a new `normalize_for_hash(df, compare_cols)` function in `etl_investor_master.py` — a verbatim extraction of the module's existing inline per-column normalization (including its current, **unfixed**, ambiguous `pd.to_datetime` date handling on `dob` — out of scope here per the Global Constraints).

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_etl_investor_master_row_hash_flag.py
"""Mirrors test_etl_trans_row_hash_flag.py for etl_investor_master.py."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_investor_master import process_investor_master

# process_investor_master(cams=...) hardcodes source="CAMS" internally (not
# caller-controllable) -- these tests scope cleanup/assertions by a random
# folio_no per test instead of a sentinel source value.


@pytest.fixture
def folio():
    f = uuid.uuid4().hex[:10]
    yield f
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.investor_master WHERE folio_no = :f"
        ), {"f": f})


def _cams_row(folio, product_code, investor_name):
    # Raw CAMS R9 headers: FOLIO_NO/PRODUCT_CODE/INV_NAME (all valid source
    # aliases in INVESTOR_MASTER_MAPPING for folio_no/product_code/investor_name).
    return {"FOLIO_NO": folio, "PRODUCT_CODE": product_code, "INV_NAME": investor_name}


def test_exact_duplicate_of_an_existing_row_is_flagged_1(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)  # first insert: flag=0

    cams_again = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams_again)  # exact resend: flag=1

    result = pd.read_sql(
        "SELECT flag FROM bronze.investor_master WHERE folio_no = :f ORDER BY created_at",
        engine, params={"f": folio},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_folio_and_product_but_changed_investor_name_is_flagged_0_and_both_rows_survive(folio):
    """Investor attributes legitimately change (address, name spelling
    corrections, etc.) -- must still reach flag=0 and get APPENDED, never
    updated in place: both the original and the changed row must survive,
    per the confirmed decision that bronze never updates investor data."""
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)

    cams_updated = pd.DataFrame([_cams_row(folio, "P1", "Jane A. Doe")])
    process_investor_master(cams=cams_updated)

    result = pd.read_sql(
        "SELECT flag, investor_name FROM bronze.investor_master "
        "WHERE folio_no = :f ORDER BY investor_name",
        engine, params={"f": folio},
    )
    assert result["investor_name"].tolist() == ["Jane A. Doe", "Jane Doe"]
    assert result.loc[result["investor_name"] == "Jane Doe", "flag"].iloc[0] == 0
    assert result.loc[result["investor_name"] == "Jane A. Doe", "flag"].iloc[0] == 0


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "New Investor")])
    process_investor_master(cams=cams)

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.investor_master WHERE folio_no = :f",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_created_at_is_never_rewritten_on_a_later_run(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)
    first = pd.read_sql(
        "SELECT created_at FROM bronze.investor_master WHERE folio_no = :f",
        engine, params={"f": folio},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams_again)

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.investor_master "
        "WHERE folio_no = :f ORDER BY created_at ASC LIMIT 1",
        engine, params={"f": folio},
    )["created_at"].iloc[0]
    assert unchanged == first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_investor_master_row_hash_flag.py -v`
Expected: FAIL — `row_hash` never set, `NOT NULL` violation on insert (confirms the old full-table-read path is still active).

- [ ] **Step 3: Modify `etl_investor_master.py`**

Add near the top of the file, alongside the existing imports:

```python
from utils.db import engine
from utils.dedupe_hash import compute_flag_via_row_hash
```

Add a new function (verbatim extraction of the existing inline normalization loop from the `# NORMALIZE VALUES` section — no behavior change, including its current ambiguous `pd.to_datetime` handling for `dob`):

```python
def normalize_for_hash(df, compare_cols):
    """Verbatim extraction of this module's existing per-column comparison
    normalization (previously inline in process_investor_master()'s
    DUPLICATE FLAG section). Deliberately unchanged, including its current
    ambiguous pd.to_datetime handling of `dob` -- see the 2026-08-26 spec's
    Global Constraints; that bug is the same class already fixed for SIP,
    but fixing it here is out of scope for this performance change."""
    df = df[compare_cols].copy()
    for col in compare_cols:
        if col in DATE_COLUMNS:
            df[col] = (
                pd.to_datetime(df[col], errors="coerce")
                .dt.strftime("%Y-%m-%d")
                .fillna("")
            )
        else:
            df[col] = df[col].fillna("").astype(str).str.strip()
    return df
```

Find this block inside `process_investor_master()`:

```python
    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.investor_master",
            engine,
        )

        existing = normalize(existing)

        existing = clean_identifier_columns(existing)

        existing = format_dates(existing)

        print(f"Existing Bronze Rows : {len(existing)}")

    except Exception:

        existing = pd.DataFrame()

        print("Bronze table not found. Initial Load.")

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================
    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source"
    }

    if existing.empty:

        df["flag"] = 0

    else:

        compare_cols = [

            c

            for c in df.columns

            if c in existing.columns

            and c not in ignore_cols

        ]

        new_df = df[compare_cols].copy()

        old_df = existing[compare_cols].copy()

        # =====================================================
        # CLEAN IDENTIFIER COLUMNS BEFORE COMPARISON
        # =====================================================

        new_df = clean_identifier_columns(new_df)
        old_df = clean_identifier_columns(old_df)

        # =====================================================
        # NORMALIZE VALUES
        # =====================================================

        for col in compare_cols:

            if col in DATE_COLUMNS:

                new_df[col] = (
                    pd.to_datetime(
                        new_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

                old_df[col] = (
                    pd.to_datetime(
                        old_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

            else:

                new_df[col] = (
                    new_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

        # =====================================================
        # COMPARE COMPLETE ROW
        # =====================================================

        new_keys = new_df.astype(str).agg("|".join, axis=1)

        old_keys = set(
            old_df.astype(str).agg("|".join, axis=1)
        )

        df["flag"] = new_keys.isin(old_keys).astype(int)
```

Replace it with:

```python
    # =====================================================
    # DUPLICATE FLAG -- hashed, indexed lookup (2026-08-26 spec).
    # Same full-row semantics as before (including clean_identifier_columns
    # + normalize_for_hash's own normalization). Only the new batch is
    # read/normalized -- bronze.investor_master is never read in full.
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = 'investor_master'
        ORDER BY ordinal_position
        """,
        engine,
    )["column_name"].tolist()

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
        "row_hash",
    }

    compare_cols = [
        c
        for c in df.columns
        if c in db_columns
        and c not in ignore_cols
    ]

    new_df = clean_identifier_columns(df[compare_cols].copy())
    new_df = normalize_for_hash(new_df, compare_cols)

    df["row_hash"], df["flag"] = compute_flag_via_row_hash(
        new_df,
        compare_cols,
        "bronze",
        "investor_master",
        engine,
    )
```

Below this block, the existing `# GET DATABASE COLUMN ORDER` section re-queries `information_schema.columns` for the same table — leave that code as-is (it is used again afterward for final column ordering); this task only removes the full-table `existing` read and the inline `old_df` normalization, and hoists one copy of the schema-only query above the duplicate-flag block to derive `compare_cols`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_investor_master_row_hash_flag.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/ -q`
Expected: no new failures beyond the pre-existing, unrelated 3 scheme-mapping/AMFI-code failures.

- [ ] **Step 6: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add python_scripts/etl_investor_master.py python_scripts/tests/test_etl_investor_master_row_hash_flag.py
git commit -m "etl_investor_master.py: compute bronze flag via hashed indexed lookup

Extracted normalize_for_hash() from the previously-inline comparison
normalization (verbatim, including its existing unfixed ambiguous-date
handling on dob -- out of scope here). process_investor_master() no
longer reads the whole bronze.investor_master table on every run.
Investor duplicates are still appended and flagged, never updated in
place, per the confirmed decision.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Switch `etl_sip.py` to the hashed, indexed flag check

**Files:**
- Modify: `python_scripts/etl_sip.py` (extract its inline non-date comparison normalization into `normalize_for_hash`, reusing the already-existing `dedupe_compare_date` for date columns; then the `# READ EXISTING BRONZE TABLE` / `# DUPLICATE FLAG` / `db_columns` sections inside `process_sip()`)
- Test: `python_scripts/tests/test_etl_sip_row_hash_flag.py`

**Interfaces:**
- Consumes: `compute_flag_via_row_hash` from `utils/dedupe_hash.py` (Task 1); `dedupe_compare_date` (already exists in `etl_sip.py` from the prior date-parsing fix); indexed `NOT NULL` `row_hash` column (Tasks 2-4).
- Produces: a new `normalize_for_hash(df, compare_cols)` function in `etl_sip.py`, preserving its existing case-insensitive (`.str.upper()`) comparison convention verbatim.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_etl_sip_row_hash_flag.py
"""Mirrors test_etl_trans_row_hash_flag.py for etl_sip.py. process_sip()
hardcodes source="CAMS"/"KFIN" internally (not sentinel-controllable), so
these tests scope cleanup/assertions by a random folio_no per test instead
of a sentinel source value."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_sip import process_sip


@pytest.fixture
def folio():
    f = uuid.uuid4().hex[:10]
    yield f
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.sip_master_new WHERE folio_no = :f"
        ), {"f": f})


def _cams_row(folio, scheme_code, reg_date, amount, ft_sip_regno):
    return {
        "FOLIO_NO": folio, "SCHEME_CODE": scheme_code, "REG_DATE": reg_date,
        "AUTO_AMOUNT": amount, "FT_SIP_REGNO": ft_sip_regno,
    }


def test_exact_duplicate_of_an_existing_row_is_flagged_1(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    cams_again = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams_again, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag FROM bronze.sip_master_new WHERE folio_no = :f ORDER BY created_at",
        engine, params={"f": folio},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_registration_but_different_amount_top_up_is_flagged_0(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    cams_topup = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1500", "REG1")])
    process_sip(cams=cams_topup, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag FROM bronze.sip_master_new WHERE folio_no = :f AND auto_amount = '1500'",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.sip_master_new WHERE folio_no = :f",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_created_at_is_never_rewritten_on_a_later_run(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")
    first = pd.read_sql(
        "SELECT created_at FROM bronze.sip_master_new WHERE folio_no = :f",
        engine, params={"f": folio},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams_again, cams_source="CAMS")

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.sip_master_new WHERE folio_no = :f "
        "ORDER BY created_at ASC LIMIT 1",
        engine, params={"f": folio},
    )["created_at"].iloc[0]
    assert unchanged == first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_sip_row_hash_flag.py -v`
Expected: FAIL — `row_hash` never set, `NOT NULL` violation on insert.

- [ ] **Step 3: Modify `etl_sip.py`**

Add near the top of the file, alongside the existing imports:

```python
from utils.db import engine
from utils.dedupe_hash import compute_flag_via_row_hash
```

Add a new function, placed after `dedupe_compare_date` (verbatim extraction of the existing inline non-date normalization — note the `.str.upper()`, which is this module's own established case-insensitive convention and must not be changed):

```python
def normalize_for_hash(df, compare_cols):
    """Verbatim extraction of this module's existing per-column comparison
    normalization (previously inline in process_sip()'s DUPLICATE FLAG
    section). Deliberately unchanged, including .str.upper() -- this
    module's comparison has always been case-insensitive, unlike
    etl_trans.py's; that difference is preserved on purpose."""
    df = df[compare_cols].copy()
    for col in compare_cols:
        if col in DATE_COLUMNS:
            df[col] = df[col].apply(dedupe_compare_date)
        else:
            df[col] = df[col].fillna("").astype(str).str.strip().str.upper()
    return df
```

Find this block inside `process_sip()`:

```python
    # =====================================================
    # READ EXISTING BRONZE TABLE
    # =====================================================

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.sip_master_new",
            engine
        )

        if not existing.empty:

            existing = normalize(existing)

            existing = clean_identifier_columns(existing)
            # existing = format_dates(existing)

        print(f"Existing Bronze Rows : {len(existing)}")

    except Exception:

        existing = pd.DataFrame()

        print("Bronze table not found. Initial Load.")

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================

    # =====================================================
    # DUPLICATE FLAG
    # COMPARE ALL COLUMNS EXCEPT ignore_cols
    # =====================================================

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source"
    }

    if existing.empty:

        df["flag"] = 0

    else:

        existing = clean_columns(existing)
        existing = normalize(existing)
        existing = clean_identifier_columns(existing)

        # -------------------------------------------------
        # TAKE COMMON COLUMNS EXCEPT IGNORE COLUMNS
        # -------------------------------------------------

        compare_cols = [
            col
            for col in df.columns
            if col in existing.columns
            and col not in ignore_cols
        ]

        print("Columns used for duplicate check:")
        print(compare_cols)


        new_df = df[compare_cols].copy()
        old_df = existing[compare_cols].copy()


        # -------------------------------------------------
        # NORMALIZE BOTH DATASETS SAME WAY
        # -------------------------------------------------

        for col in compare_cols:

            if col in DATE_COLUMNS:

                new_df[col] = new_df[col].apply(dedupe_compare_date)

                old_df[col] = old_df[col].apply(dedupe_compare_date)

            else:

                new_df[col] = (
                    new_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .str.upper()
                )


        # -------------------------------------------------
        # CREATE COMPLETE ROW KEY
        # -------------------------------------------------

        new_keys = new_df.astype(str).agg("|".join, axis=1)

        old_keys = set(
            old_df.astype(str).agg("|".join, axis=1)
        )


        # -------------------------------------------------
        # FLAG
        # -------------------------------------------------

        df["flag"] = (
            new_keys.isin(old_keys)
            .astype(int)
        )


        print("=" * 80)
        print("Duplicate Check Result")
        print(df["flag"].value_counts(dropna=False))
        print("=" * 80)
```

Replace it with:

```python
    # =====================================================
    # DUPLICATE FLAG -- hashed, indexed lookup (2026-08-26 spec).
    # Same full-row, case-insensitive semantics as before. Only the new
    # batch is read/normalized -- bronze.sip_master_new is never read in
    # full.
    # =====================================================

    db_columns_preflight = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='bronze'
        AND table_name='sip_master_new'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
        "row_hash",
    }

    compare_cols = [
        col
        for col in df.columns
        if col in db_columns_preflight
        and col not in ignore_cols
    ]

    print("Columns used for duplicate check:")
    print(compare_cols)

    new_df = normalize_for_hash(df, compare_cols)

    df["row_hash"], df["flag"] = compute_flag_via_row_hash(
        new_df,
        compare_cols,
        "bronze",
        "sip_master_new",
        engine,
    )

    print("=" * 80)
    print("Duplicate Check Result")
    print(df["flag"].value_counts(dropna=False))
    print("=" * 80)
```

The existing `db_columns` query further down (in the `# MATCH DATABASE COLUMN ORDER` section) is unchanged and still runs again for final column ordering — leave it as-is; `db_columns_preflight` above is a separate, earlier lookup used only to derive `compare_cols`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/test_etl_sip_row_hash_flag.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && python -m pytest tests/ -q`
Expected: no new failures beyond the pre-existing, unrelated 3 scheme-mapping/AMFI-code failures.

- [ ] **Step 6: Commit**

```bash
cd /var/www/html/intelliwealth_layer_old_code
git add python_scripts/etl_sip.py python_scripts/tests/test_etl_sip_row_hash_flag.py
git commit -m "etl_sip.py: compute bronze flag via hashed indexed lookup

Extracted normalize_for_hash() from the previously-inline comparison
normalization (verbatim, including its existing case-insensitive
.str.upper() convention). process_sip() no longer reads the whole
bronze.sip_master_new table on every run. Same-registration top-up
rows (differing only in amount) still correctly flag 0, proving
full-row semantics are preserved. created_at still never rewritten.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Post-implementation check

After all 7 tasks are committed:

- [ ] Run `cd python_scripts && source venv/bin/activate && python -m pytest tests/ -q` once more — full suite green except the 3 pre-existing, unrelated scheme-mapping/AMFI-code failures.
- [ ] Confirm no bronze loader anywhere still calls `pd.read_sql("SELECT * FROM bronze.<table>"` for a duplicate check: `grep -n "SELECT \* FROM bronze" python_scripts/etl_trans.py python_scripts/etl_investor_master.py python_scripts/etl_sip.py` should return nothing.
