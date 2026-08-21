"""resolve_scheme_id() decides which scheme a gold.holdings row belongs to.

Each transaction row already carries its own scheme_id. mapped_scheme_id is
only a fallback, built by borrowing the scheme_id of the SAME investor's
single most-recent transaction (see extract_holdings()'s transaction_scheme
CTE) -- it is not scoped to any particular row's real scheme. Preferring it
unconditionally collapsed every transaction of a multi-scheme investor onto
one scheme, and the downstream drop_duplicates(subset=[rta, folio_number,
scheme_id]) then silently discarded that investor's other real holdings.
"""
import pandas as pd

from etl_gold_holdings import resolve_scheme_id


class TestResolveSchemeId:
    def test_prefers_the_row_own_scheme_id(self):
        """A transaction that already knows its scheme must keep it, even
        when a same-investor fallback value is also available."""
        raw = pd.Series(["B103192"])
        mapped = pd.Series(["B107745"])

        assert resolve_scheme_id(raw, mapped).tolist() == ["B103192"]

    def test_falls_back_only_when_the_row_own_scheme_id_is_missing(self):
        raw = pd.Series([None])
        mapped = pd.Series(["B107745"])

        assert resolve_scheme_id(raw, mapped).tolist() == ["B107745"]

    def test_blank_string_counts_as_missing(self):
        raw = pd.Series(["  "])
        mapped = pd.Series(["B107745"])

        assert resolve_scheme_id(raw, mapped).tolist() == ["B107745"]

    def test_missing_both_stays_missing(self):
        raw = pd.Series([None])
        mapped = pd.Series([None])

        assert resolve_scheme_id(raw, mapped).isna().tolist() == [True]

    def test_a_multi_scheme_investor_keeps_every_distinct_scheme(self):
        """Regression for the bug: one PAN trading 3 real schemes must
        resolve to 3 distinct scheme_ids, not collapse onto whichever
        scheme happened to be their latest trade.
        """
        raw = pd.Series(["B103174", "B103192", "B107745"])
        mapped = pd.Series(["B107745", "B107745", "B107745"])

        resolved = resolve_scheme_id(raw, mapped)

        assert resolved.tolist() == ["B103174", "B103192", "B107745"]
        assert resolved.nunique() == 3
