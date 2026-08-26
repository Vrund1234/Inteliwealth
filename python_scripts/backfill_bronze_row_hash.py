"""One-time backfill: populate row_hash for every bronze row that predates
the 2026-08-26 dedup-performance migration. Run once, after Task 2's
ALTER TABLE and before Task 4's index + NOT NULL.

Each table's `compare_cols` normalization below is a deliberately frozen,
verbatim copy of that table's loader's current inline comparison logic at
the time of this migration (etl_trans.py's compare block; the equivalent
inline blocks in etl_investor_master.py / etl_sip.py) -- not imported live,
so this script stays reproducible even as the loaders are refactored in
later tasks.
"""

import sys

import pandas as pd
from sqlalchemy import text

from utils.db import engine
from utils.dedupe_hash import hash_normalized_rows


def _normalize_generic(df, compare_cols, date_columns=()):
    """Frozen copy of the non-transaction loaders' inline compare-column
    normalization: blank/NaN -> "", everything else -> str(value).strip().
    Date columns get "" instead of NaT/None (matching the loaders' own
    `.fillna("")` on the date branch)."""
    df = df[compare_cols].copy()
    for col in compare_cols:
        if col in date_columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def backfill_table(schema, table, compare_cols, sentinel_source=None, date_columns=()):
    """Compute and store row_hash for every existing row in schema.table.
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

    normalized = _normalize_generic(df, compare_cols, date_columns=date_columns)
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


TRANSACTION_DATE_COLUMNS = [
    "traddate", "postdate", "rep_date", "ticob_posted_date",
    "sys_regn_date", "ca_initiated_date",
]
INVESTOR_DATE_COLUMNS = ["dob"]
SIP_DATE_COLUMNS = [
    "from_date", "to_date", "cease_date", "reg_date",
    "pause_from_date", "pause_to_date",
]


if __name__ == "__main__":
    for table, date_cols in (
        ("transaction_master_new", TRANSACTION_DATE_COLUMNS),
        ("investor_master", INVESTOR_DATE_COLUMNS),
        ("sip_master_new", SIP_DATE_COLUMNS),
    ):
        n = backfill_table(
            "bronze", table, _compare_cols_for(table), date_columns=date_cols
        )
        print(f"Backfilled row_hash for {n} rows in bronze.{table}")
        sys.stdout.flush()
