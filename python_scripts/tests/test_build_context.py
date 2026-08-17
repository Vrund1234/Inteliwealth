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


def _amfi_raw(rows):
    """As the pipeline supplies it: name_raw is scheme_nav_name verbatim,
    name_norm is that same name after normalize_scheme_name()."""
    return pd.DataFrame(
        rows, columns=["amfi_scheme_code", "amc_code", "name_raw", "name_norm",
                       "normalized_nav_name"],
    )


class TestIndexingUsesTheUnflattenedName:
    """scheme_mapping.py normalizes name_norm before calling build_context, so
    by the time the parser sees it the parentheses are already gone. That makes
    strip_parentheticals() a no-op and hands the name to the bare
    "FORMERLY KNOWN AS ...$" rule, which deletes the plan and option with it.
    222 of the 237 FORMERLY names collapse onto 27 shared keys that way."""

    def test_formerly_clause_does_not_take_the_plan_with_it(self):
        amfi = _amfi_raw([(
            "444", "176",
            "Sundaram Low Duration Fund (Formerly Known as Principal Low "
            "Duration Fund) - Direct Plan - Growth Option",
            "SUNDARAM LOW DURATION FUND FORMERLY KNOWN AS PRINCIPAL LOW "
            "DURATION FUND DIRECT PLAN GROWTH OPTION",
            None,
        )])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        assert {k.plan for k in ctx.amfi_by_key} == {"DIRECT"}

    def test_growth_and_idcw_siblings_keep_separate_keys(self):
        amfi = _amfi_raw([
            ("445", "176",
             "Sundaram Low Duration Fund (Formerly Known as Principal Low "
             "Duration Fund)- Growth Option",
             "SUNDARAM LOW DURATION FUND FORMERLY KNOWN AS PRINCIPAL LOW "
             "DURATION FUND GROWTH OPTION", None),
            ("446", "176",
             "Sundaram Low Duration Fund (Formerly Known as Principal Low "
             "Duration Fund) Regular Plan IDCW",
             "SUNDARAM LOW DURATION FUND FORMERLY KNOWN AS PRINCIPAL LOW "
             "DURATION FUND REGULAR PLAN IDCW", None),
        ])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        for codes in ctx.amfi_by_key.values():
            assert len(codes) == 1, "the two share classes must not share a key"

    def test_ampersand_is_read_from_the_raw_name(self):
        """normalize_scheme_name() turns "&" into a space, so the flattened
        name yields LARGE MIDCAP where the RTA name yields LARGE AND MIDCAP."""
        amfi = _amfi_raw([(
            "447", "RMF",
            "Nippon India Vision Large & Midcap Fund-GROWTH PLAN-Growth Option",
            "NIPPON INDIA VISION LARGE MIDCAP FUND GROWTH PLAN GROWTH OPTION",
            None,
        )])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        cores = {k.core_name for k in ctx.amfi_by_key}
        assert "NIPPON INDIA VISION LARGE AND MIDCAP" in cores

    def test_falls_back_to_name_norm_when_no_raw_name_is_supplied(self):
        amfi = _amfi([("448", "H", "HDFC Flexi Cap Fund - Growth", None)])
        ctx = build_context(pd.DataFrame(), amfi, None, {})
        assert {k.core_name for k in ctx.amfi_by_key} == {"HDFC FLEXI CAP"}
