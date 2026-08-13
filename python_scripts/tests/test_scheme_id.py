"""scheme_id identifies the AMFI scheme a mapping resolved to.

An unmapped row has not resolved to anything, so it has no scheme_id. Building
it by concatenating two empty strings gave every unmapped row the same ""
value, which reads as a real shared identifier rather than "unknown".
"""
from scheme_mapping import derive_scheme_id


class TestDeriveSchemeId:
    def test_concatenates_amc_and_amfi_code(self):
        assert derive_scheme_id("B", "101314") == "B101314"

    def test_unmapped_row_has_no_scheme_id(self):
        assert derive_scheme_id(None, None) is None

    def test_missing_amfi_code_yields_none_even_with_an_amc(self):
        """The AMC alone does not identify a scheme."""
        assert derive_scheme_id("B", None) is None

    def test_blank_strings_are_treated_as_missing(self):
        assert derive_scheme_id("", "") is None

    def test_nan_is_treated_as_missing(self):
        import numpy as np
        assert derive_scheme_id(np.nan, np.nan) is None


class TestSchemeIdColumnSurvivesToTheDatabase:
    def test_unmapped_rows_stay_none_and_do_not_become_nan(self):
        """pandas 3 coerces None to nan on assignment, and the pipeline's
        df.where(pd.notna(df), None) does not restore it. A float nan reaches
        psycopg2 and Postgres stores the literal string 'NaN' in the varchar
        column — worse than the "" it replaced, because it looks like a value.
        """
        import pandas as pd
        from scheme_mapping import build_scheme_id_column

        df = pd.DataFrame({
            "amc_code": ["B", None],
            "amfi_scheme_code": ["101314", None],
        })
        df["scheme_id"] = build_scheme_id_column(df)
        df = df.where(pd.notna(df), None)

        records = df.to_dict(orient="records")
        assert records[0]["scheme_id"] == "B101314"
        assert records[1]["scheme_id"] is None
