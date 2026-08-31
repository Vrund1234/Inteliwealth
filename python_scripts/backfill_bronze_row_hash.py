"""One-time backfill: populate row_hash for every bronze row that predates
the 2026-08-26 dedup-performance migration. Run once, after Task 2's
ALTER TABLE and before Task 4's index + NOT NULL.

Each table's normalization below DIRECTLY IMPORTS that loader's own real,
current comparison logic (functions and constants) rather than hand-copying
it -- etl_trans.prepare_for_comparison already exists standalone and is
reused as-is; etl_investor_master/etl_sip's clean_identifier_columns and
DATE_COLUMNS are imported directly rather than re-listed, so this script
cannot silently drift from what those modules actually do today. This
includes reproducing etl_investor_master.py's own currently-unfixed
ambiguous pd.to_datetime date handling verbatim (via that exact call,
inline below, matching its ONLY date branch) -- deliberately not fixed
here, per the Global Constraints -- and etl_sip.py's case-insensitive
.str.upper() convention on non-date columns.

NOT ACTUALLY ONE-TIME -- RE-RUN AFTER ANY BRONZE SCHEMA CHANGE: the hash
is computed positionally over `_compare_cols_for(table)` (every column of
the bronze table in `ordinal_position` order, minus flag/created_at/
updated_at/source/row_hash), and each bronze loader derives its own
`compare_cols` identically. Adding, removing, or renaming a column on any
bronze table therefore changes the hashed tuple for the loaders and this
backfill together, so this script MUST be re-run after any such schema
change. Skipping that re-run leaves historical rows carrying a stored
`row_hash` the loaders can no longer reproduce for the same logical row,
so a resend of an old row is silently mis-flagged as new (flag=0) -- the
exact bug fixed in the 2026-08-26 commit that added this paragraph, where
the loaders derived `compare_cols` from their own mapped DataFrame's
columns rather than from the table's full column list.
"""

import sys

import pandas as pd
from psycopg2.extras import execute_values
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
                pd.to_datetime(df[col], errors="coerce", dayfirst=True)
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


def backfill_table(schema, table, compare_cols, normalize_fn, sentinel_source=None, chunk_size=5000):
    """Compute and store row_hash for every existing row in schema.table.
    `normalize_fn` is one of _normalize_transaction/_normalize_investor/
    _normalize_sip above (tests may pass any callable with the same
    (df, compare_cols) -> df shape against a throwaway table).
    `sentinel_source`, when given, restricts the backfill to that source
    value -- used only by tests against throwaway tables; production calls
    (see __main__ below) omit it to cover every row.

    Writes go through psycopg2.extras.execute_values in chunks of
    `chunk_size`, not one UPDATE per row: a plain per-row
    conn.execute(text(...)) loop is one round trip PER ROW, which is fine
    at today's ~130k rows but does not scale as bronze grows into millions
    -- and per dedupe_hash.py's SCHEMA-CHANGE CONTRACT, this backfill must
    be re-run in full after every future bronze schema change, not just
    once. execute_values rewrites one UPDATE ... FROM (VALUES %s) per
    chunk into a single statement, the same technique (and the same
    ~2,070 -> ~65,000 rows/sec win) upsert_dataframe() already uses in
    utils/db.py."""
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

    sql = f"""
        UPDATE {schema}.{table} AS t
        SET row_hash = v.row_hash
        FROM (VALUES %s) AS v (ctid, row_hash)
        WHERE t.ctid = v.ctid::tid
    """

    pairs = list(zip(df["_ctid"], row_hash))

    with engine.begin() as conn:
        cursor = conn.connection.cursor()
        for start in range(0, len(pairs), chunk_size):
            batch = pairs[start:start + chunk_size]
            execute_values(cursor, sql, batch, page_size=len(batch))

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
