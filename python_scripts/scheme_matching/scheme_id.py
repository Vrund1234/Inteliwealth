"""scheme_id derivation, shared by the pipeline and the promotion step.

Lives here rather than in scheme_mapping.py so that promote_approved_mappings
can use it without importing scheme_mapping, which now imports the promotion
step in turn. scheme_mapping re-exports both names, so existing callers and
tests are unaffected.
"""

import pandas as pd


def derive_scheme_id(amc_code, amfi_scheme_code):
    """Identifier for the AMFI scheme a mapping resolved to, or None.

    An unmapped row resolved to nothing, so it has no scheme_id. Concatenating
    two blanks gave every unmapped row the same "" — indistinguishable from a
    real shared identifier. None says "unknown", which is what it is.
    """
    amc = "" if amc_code is None or pd.isna(amc_code) else str(amc_code).strip()
    code = (
        ""
        if amfi_scheme_code is None or pd.isna(amfi_scheme_code)
        else str(amfi_scheme_code).strip()
    )
    if not amc or not code:
        return None
    return amc + code


def build_scheme_id_column(df):
    """scheme_id for every row, as an object-dtype Series.

    dtype=object is required, not cosmetic: pandas coerces None to nan in a
    plain list assignment, the pipeline's later df.where(pd.notna(df), None)
    does not restore it, and psycopg2 then sends a float nan that Postgres
    stores as the literal string 'NaN' in this varchar column.
    """
    return pd.Series(
        [derive_scheme_id(r.amc_code, r.amfi_scheme_code) for r in df.itertuples()],
        index=df.index,
        dtype=object,
    )
