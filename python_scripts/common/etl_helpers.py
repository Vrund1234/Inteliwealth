"""
common/etl_helpers.py
=====================
Canonical shared helpers for the Gold ETL pipeline.

Functions moved here during Phase 2 deduplication
--------------------------------------------------

FULLY SHARED (identical across all callers — moved verbatim):
  - safe_read(query)
  - normalize_for_compare(df)

Phase B change
--------------
  - safe_read(query) no longer returns an empty DataFrame on error.  It logs
    the failure (with the offending query) and re-raises, so a real database
    error is no longer indistinguishable from "no new rows".  See the
    function docstring for the call-site audit.

DIVERGED — parameterised to resolve divergence:
  - get_last_processed_time(gold_table)
      Each ETL file originally had its own copy that queried a different
      gold.* table.  The logic is identical; only the table name differed.
      Resolved by accepting the target table name as a parameter.

DIVERGED — NOT moved (each caller uses different key columns):
  - create_row_key(df)
      folio_nominees : keys on [holding_id, seq]
      scheme_nav     : keys on [scheme_id, nav_date]
      transaction    : keys on [rta, rta_txn_no]
      amc            : did not define this function at all
      A single shared signature is not possible without either breaking
      the call sites or silently using the wrong columns.  Each file
      retains its own copy.  A future phase may introduce a generic
      create_row_key(df, key_cols) factory here.
"""

import pandas as pd

from utils.db import engine


# =====================================================
# SAFE READ
# =====================================================
# Source: identical across etl_gold_amc, etl_gold_folio_nominees,
#         etl_gold_scheme_nav, etl_gold_transaction.

def safe_read(query: str) -> pd.DataFrame:
    """Execute *query* against the project engine and return a DataFrame.

    Raises
    ------
    Exception
        Whatever the driver raised, re-raised after logging.

    Phase B note — this function used to swallow every exception and return an
    empty DataFrame, which made a real database error (missing table, bad
    credentials, dead connection) completely indistinguishable from a
    legitimate "no new rows" result.  Callers branch on ``df.empty``, so a
    connection failure silently looked like a successful no-op run.

    It now logs the error with its query and RE-RAISES.  Callers that
    genuinely want empty-on-error must say so explicitly with their own
    try/except (``gold/transaction.py`` already does exactly this around its
    ``existing`` lookup), which keeps the intent visible at the call site.
    Gold domain extracts need no such handling: ``run_gold_pipeline`` catches
    per-domain and reports ``status="failed"``, so one broken domain is
    reported instead of being mistaken for an empty one.
    """
    try:
        return pd.read_sql(query, engine)

    except Exception as exc:
        # Phase D will replace this with:
        #     logger.error("safe_read failed | query=%s", flat_query, exc_info=True)
        flat_query = " ".join(query.split())

        print(
            f"[ERROR] safe_read failed: {type(exc).__name__}: {exc}"
        )
        print(
            f"[ERROR] safe_read query: {flat_query[:300]}"
        )

        raise


# =====================================================
# GET LAST PROCESSED TIME
# =====================================================
# Source: etl_gold_amc / etl_gold_folio_nominees / etl_gold_scheme_nav /
#         etl_gold_transaction — logic identical; table name differed.
# Resolution: accept gold_table as a parameter.

def get_last_processed_time(gold_table: str) -> pd.Timestamp:
    """Return the MAX(created_at) already written to *gold_table*.

    Parameters
    ----------
    gold_table : str
        Fully-qualified table name in the form ``schema.table``,
        e.g. ``"gold.amc"`` or ``"gold.transactions"``.

    Returns
    -------
    pd.Timestamp
        The latest timestamp found, or ``1900-01-01`` when the table is
        empty or unreachable.
    """
    try:
        result = pd.read_sql(
            f"""
            SELECT
                MAX(created_at) AS last_time
            FROM {gold_table}
            """,
            engine,
        )

        last_time = result.iloc[0]["last_time"]

        if pd.isna(last_time):
            return pd.Timestamp("1900-01-01")

        return pd.to_datetime(last_time)

    except Exception:
        return pd.Timestamp("1900-01-01")


# =====================================================
# NORMALIZE FOR COMPARE
# =====================================================
# Source: etl_gold_folio_nominees, etl_gold_scheme_nav,
#         etl_gold_transaction — byte-for-byte identical.
# Note: etl_gold_amc did not define this function.

def normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise *df* for deduplication comparison.

    * Drops ``created_at`` (audit timestamp, not part of the natural key).
    * Formats all datetime columns as ``YYYY-MM-DD`` strings.
    * Casts every other column to stripped strings.

    Returns a copy — the original DataFrame is never mutated.
    """
    df = df.copy()

    df = df.drop(
        columns=["created_at"],
        errors="ignore",
    )

    for col in df.columns:

        if pd.api.types.is_datetime64_any_dtype(df[col]):

            df[col] = (
                pd.to_datetime(df[col], errors="coerce")
                .dt.strftime("%Y-%m-%d")
            )

        else:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )

    return df


# =====================================================
# create_row_key — NOT MOVED (key columns diverge per ETL file)
# =====================================================
# See module docstring for the full explanation.
# Each ETL file keeps its own local create_row_key that calls
# normalize_for_compare (from this module) and then selects the
# columns appropriate for its natural key.
