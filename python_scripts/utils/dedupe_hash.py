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
    caller has already normalized into plain strings."""
    joined = normalized_df[compare_cols].astype(str).agg("|".join, axis=1)
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
