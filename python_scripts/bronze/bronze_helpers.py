"""
bronze/bronze_helpers.py
========================
Canonical shared helpers for the Bronze (raw ingestion) ETL pipeline.

Functions moved here during Phase A deduplication
--------------------------------------------------
Before Phase A the three flat modules `etl_investor_master.py`, `etl_trans.py`
and `etl_sip.py` each carried their own near-duplicate copy of these five
helpers.  A line-by-line diff of all three copies produced the following:

FULLY SHARED (functionally identical across all callers — moved verbatim):
  - clean_value(value)
      `etl_trans` tested membership against a list `[...]` and `etl_sip`
      against a tuple `(...)`; that is the only textual difference and it has
      no effect on the result.  `etl_investor_master` never defined it.
      NOTE: this function currently has no call sites anywhere in the tree —
      it was already dead code in both originals.  Preserved as-is rather
      than deleted, since removing it is a behaviour-neutral cleanup that
      belongs to a later phase, not to a relocation phase.

  - clean_identifier_columns(df, identifier_columns, *, guard_empty=False)
      The body was byte-identical in `etl_investor_master` and `etl_trans`.
      `etl_sip` differed ONLY in its empty-DataFrame guard (see below).

DIVERGED — parameterised to resolve divergence:
  Same approach Phase 2 used for `get_last_processed_time(gold_table)`: one
  shared implementation, with the varying behaviour passed in explicitly by
  the caller instead of being merged away or duplicated.

  - guard_empty (all four DataFrame helpers)
      `etl_sip` guarded with `if df is None or df.empty`, the other two with
      `if df is None`.  Returning early on an empty frame is a real
      behavioural difference (an empty frame otherwise falls through and gets
      `.copy()`-ed and column-renamed), so it is a parameter, not a merge.
      sip passes guard_empty=True; the other two leave it False.

  - clean_columns(df, *, quotes_before_whitespace=False, dedupe_columns=False)
      Two independent divergences, both flagged in the Phase 3 audit:
        * strip order — `etl_trans` strips quotes BEFORE whitespace; the other
          two strip whitespace BEFORE quotes.  This matters for headers such
          as `"' folio no '"`, where the two orders yield different names, so
          neither order could be imposed on the other file.  trans passes
          quotes_before_whitespace=True.
        * duplicate-column dedup — `etl_trans` and `etl_sip` drop duplicate
          columns (`~df.columns.duplicated(keep="first")`);
          `etl_investor_master` does not.  Those two pass dedupe_columns=True.

  - normalize(df, date_columns, *, numeric_columns=None, guard_empty=False)
      `etl_investor_master` and `etl_trans` were identical (modulo trailing
      whitespace on one blank line).  `etl_sip` additionally coerced its
      NUMERIC_COLUMNS via `pd.to_numeric(errors="coerce")` and skipped the
      string cleaning for them.  Passed in as `numeric_columns`; when it is
      None the numeric branch can never fire, so the other two callers keep
      their exact original behaviour.  The branch order is preserved exactly:
      date skip -> numeric coerce -> datetime skip -> string clean.

  - format_dates(df, date_columns, *, date_format=None, guard_empty=False)
      `etl_investor_master` called `pd.to_datetime(errors="coerce")` with
      `dayfirst=False` commented out; `etl_trans` passed `dayfirst=False`
      explicitly.  `dayfirst=False` is pandas' default, so the two were
      functionally identical and both now pass date_format=None, which omits
      the argument entirely and lets pandas infer the format as before.
      `etl_sip` is the meaningful divergence: it parses with an EXPLICIT
      per-source format string (CAMS `%m/%d/%Y %I:%M %p` vs KFIN
      `%d/%m/%Y`).  The format is therefore a parameter.

KEPT SEPARATE — NOT moved here (deliberately local to each domain module):
  - DATE_COLUMNS, IDENTIFIER_COLUMNS, NUMERIC_COLUMNS, PERIODICITY_MAPPING
      Every one of these is domain-specific: investor_master's DATE_COLUMNS
      are `dob`/`folio_date`/..., transaction's are `traddate`/`postdate`/...,
      sip's are `from_date`/`to_date`/...  Centralising them would either
      require a union (which would make each domain clean columns that do not
      belong to it) or a per-domain lookup dict, both of which are worse than
      a module-level list next to the code that uses it.  This mirrors how
      `silver/investor_master.py` etc. keep domain logic local and import only
      shared helpers.
  - The per-source date-format choice for SIP (CAMS vs KFIN) stays in
      `bronze/sip.py` as a thin wrapper — it is domain knowledge about file
      formats, not a generic helper concern.
  - apply_*_mapping() and process_*() — one per domain, never shared.
"""

import pandas as pd


# =====================================================
# CLEAN COLUMN NAMES
# =====================================================
# DIVERGED -> parameterised: `quotes_before_whitespace` (trans strips quotes
# first), `dedupe_columns` (trans + sip only), `guard_empty` (sip only).

def clean_columns(
    df,
    *,
    guard_empty=False,
    quotes_before_whitespace=False,
    dedupe_columns=False
):

    if df is None:
        return df

    if guard_empty and df.empty:
        return df

    df = df.copy()

    columns = df.columns.astype(str)

    if quotes_before_whitespace:

        # etl_trans order: quotes stripped BEFORE whitespace
        columns = (
            columns
            .str.strip("'")
            .str.strip('"')
            .str.strip()
        )

    else:

        # etl_investor_master / etl_sip order: whitespace stripped BEFORE quotes
        columns = (
            columns
            .str.strip()
            .str.strip("'")
            .str.strip('"')
        )

    df.columns = (
        columns
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("#", "", regex=False)
    )

    if dedupe_columns:

        # Keep first duplicate column
        df = df.loc[:, ~df.columns.duplicated(keep="first")]

    return df


# =====================================================
# NORMALIZE
# =====================================================
# DIVERGED -> parameterised: `date_columns` and `numeric_columns` are
# domain-specific; `numeric_columns` is used by sip only.

def normalize(
    df,
    date_columns,
    *,
    numeric_columns=None,
    guard_empty=False
):

    if df is None:
        return df

    if guard_empty and df.empty:
        return df

    df = df.copy()

    if numeric_columns is None:
        numeric_columns = ()

    for col in df.columns:

        # skip dates
        if col in date_columns:
            continue

        # handle numeric columns (sip only; empty tuple for other domains)
        if col in numeric_columns:

            df[col] = (
                pd.to_numeric(
                    df[col],
                    errors="coerce"
                )
            )

            continue

        # datetime skip
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.replace("'", "", regex=False)
            .str.replace('"', "", regex=False)
            .str.strip()
            .replace({
                "nan": "",
                "None": "",
                "<NA>": "",
                "NaT": ""
            })
        )

    return df


# =====================================================
# CLEAN IDENTIFIER COLUMNS
# (.0 SHOULD NEVER APPEAR)
# =====================================================
# Body was byte-identical across all three originals; only the column list
# and sip's empty-guard differed, so both are parameters.

def clean_identifier_columns(
    df,
    identifier_columns,
    *,
    guard_empty=False
):

    if df is None:
        return df

    if guard_empty and df.empty:
        return df

    df = df.copy()

    for col in identifier_columns:

        if col not in df.columns:
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .replace({
                "": None,
                "nan": None,
                "None": None,
                "<NA>": None
            })
        )

    return df


# =====================================================
# CLEAN SINGLE VALUE
# =====================================================
# FULLY SHARED — moved verbatim (list-vs-tuple membership was the only
# textual difference between the etl_trans and etl_sip copies).
# NOTE: currently has no call sites; see module docstring.

def clean_value(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    if value.lower() in (
        "",
        "nan",
        "none",
        "<na>",
        "nat"
    ):
        return None

    return value


# =====================================================
# FORMAT DATE COLUMNS
# =====================================================
# DIVERGED -> parameterised: `date_format`.
#   None            -> pandas infers (investor_master + transaction; matches
#                      the original `errors="coerce"` call, with the default
#                      dayfirst=False that etl_trans passed explicitly)
#   format string   -> explicit strptime format (sip, per source)

def format_dates(
    df,
    date_columns,
    *,
    date_format=None,
    guard_empty=False
):

    if df is None:
        return df

    if guard_empty and df.empty:
        return df

    df = df.copy()

    for col in date_columns:

        if col not in df.columns:
            continue

        if date_format is None:

            parsed = pd.to_datetime(
                df[col],
                errors="coerce"
            )

        else:

            parsed = pd.to_datetime(
                df[col],
                format=date_format,
                errors="coerce"
            )

        df[col] = parsed.dt.date

        df[col] = df[col].where(
            pd.notnull(df[col]),
            None
        )

    return df
