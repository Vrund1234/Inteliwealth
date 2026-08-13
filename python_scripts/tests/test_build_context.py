"""build_context indexes the AMFI master by SchemeKey.

normalized_nav_name is a second rendering of scheme_nav_name with punctuation
flattened. It is indexed as an extra lookup key, but it must never be trusted
for plan/option/frequency: flattening removes the parentheses that
strip_parentheticals() keys on, so a bare "FORMERLY KNOWN AS ..." runs to the
end of the string and takes the plan and option with it. Those attributes
always come from scheme_nav_name.
"""
import pandas as pd

from scheme_mapping import build_context


def _amfi(rows):
    return pd.DataFrame(rows, columns=["amfi_scheme_code", "amc_code",
                                       "name_norm", "normalized_nav_name"])


class TestNormalizedNavNameIsIndexedSafely:
    def test_normalized_variant_is_an_additional_lookup_key(self):
        """"&" survives as "AND" in one rendering and vanishes in the other."""
        amfi = _amfi([("111", "B",
                       "Aditya Birla Sun Life Large & Midcap Fund - Regular Plan - Growth",
                       "ADITYA BIRLA SUN LIFE LARGE MIDCAP FUND REGULAR PLAN GROWTH")])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        cores = {k.core_name for k in ctx.amfi_by_key}
        assert "ADITYA BIRLA SUN LIFE LARGE AND MIDCAP" in cores
        assert "ADITYA BIRLA SUN LIFE LARGE MIDCAP" in cores
        for key in ctx.amfi_by_key:
            assert ctx.amfi_by_key[key] == ["111"]

    def test_normalized_variant_never_overrides_the_option(self):
        """The bare-FORMERLY case: normalized_nav_name loses the IDCW suffix."""
        amfi = _amfi([("222", "176",
                       "Sundaram Medium Duration Fund (Formerly Sundaram Medium Term Bond Fund) "
                       "Regular Plan-Income Distribution cum Capital Withdrawal(IDCW)",
                       "SUNDARAM MEDIUM DURATION FUND FORMERLY SUNDARAM MEDIUM TERM BOND FUND "
                       "REGULAR PLAN INCOME DISTRIBUTION CUM CAPITAL WITHDRAWAL IDCW")])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        assert ctx.amfi_by_key, "scheme should be indexed"
        # Every key for this scheme must carry the option parsed from
        # scheme_nav_name, never the GROWTH default the flattened name yields.
        assert {k.option for k in ctx.amfi_by_key} == {"IDCW"}

    def test_a_blank_normalized_name_is_simply_skipped(self):
        amfi = _amfi([("333", "H", "HDFC Flexi Cap Fund - Growth", None)])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        assert len(ctx.amfi_by_key) == 1
