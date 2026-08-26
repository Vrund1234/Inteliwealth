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

SCHEMA-CHANGE CONTRACT: the hash is computed positionally over
`compare_cols`, and every bronze loader derives that list the same way
backfill_bronze_row_hash.py does -- every column of the bronze table, in
`ordinal_position` order, minus flag/created_at/updated_at/source/row_hash.
Adding, removing, or renaming a column on any bronze table therefore
changes the hashed tuple for the loaders and the backfill together, and
`backfill_bronze_row_hash.py` MUST be re-run after any such schema change.
Skipping that re-run leaves historical rows carrying a stored `row_hash`
that the loaders can no longer reproduce for the same logical row, so a
resend of an old row is silently mis-flagged as new -- this is exactly the
bug fixed in the 2026-08-26 commit that introduced this paragraph, where
the loaders derived `compare_cols` from their own mapped DataFrame instead
of from the table's full column list.
"""

import hashlib

import pandas as pd
from sqlalchemy import text


def hash_normalized_rows(
    normalized_df: pd.DataFrame, compare_cols: list[str]
) -> pd.Series:
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

    if normalized_df.empty:
        # .agg(..., axis=1) on an empty frame returns an empty DataFrame,
        # not a Series -- which silently violates this function's documented
        # (and type-hinted) contract for every caller of an empty batch.
        return pd.Series([], dtype=object, index=normalized_df.index)

    joined = normalized_df[compare_cols].astype(str).agg(_encode_row, axis=1)
    return joined.apply(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def compute_flag_via_row_hash(
    new_df: pd.DataFrame,
    compare_cols: list[str],
    schema: str,
    table: str,
    engine,
) -> tuple[pd.Series, pd.Series]:
    """Hash the incoming batch and check only those specific hashes against
    schema.table.row_hash (indexed lookup -- never reads the whole table).

    Returns (row_hash, flag): `row_hash` to store on the new rows being
    inserted, `flag` (0/1) with the same meaning bronze's flag has always
    had -- 1 if this exact row already exists in bronze, 0 if new.
    """
    row_hash = hash_normalized_rows(new_df, compare_cols)

    if row_hash.empty:
        return row_hash, row_hash.astype(int)

    # `= ANY(:hashes)` binds the batch as a single array parameter (adapted
    # natively from a Python list by psycopg2) instead of expanding into N
    # literal IN elements -- one stable plan regardless of batch size.
    query = text(
        f"SELECT row_hash FROM {schema}.{table} WHERE row_hash = ANY(:hashes)"
    )

    # Pure SELECT -- no transaction/write needed, so connect() not begin().
    with engine.connect() as conn:
        existing_hashes = {
            r[0] for r in conn.execute(query, {"hashes": list(row_hash.unique())})
        }

    flag = row_hash.isin(existing_hashes).astype(int)
    return row_hash, flag
