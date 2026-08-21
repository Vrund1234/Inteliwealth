"""Tests for plan_option_mismatch: the one place a parsed SchemeKey is
compared against a candidate's structured plan_type/option_type."""

from scheme_matching.scheme_key import parse_scheme_key
from scheme_matching.structured_signals import plan_option_mismatch


class TestPlanOptionMismatch:
    def test_agreeing_plan_and_option_is_not_a_mismatch(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        assert plan_option_mismatch(key, "DIRECT", "GROWTH") is None

    def test_disagreeing_plan_is_flagged(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        reason = plan_option_mismatch(key, "REGULAR", "GROWTH")
        assert reason == "PLAN_MISMATCH(rta=DIRECT,sm=REGULAR)"

    def test_disagreeing_option_is_flagged(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        reason = plan_option_mismatch(key, "DIRECT", "IDCW")
        assert reason == "OPTION_MISMATCH(rta=GROWTH,sm=IDCW)"

    def test_both_disagreeing_are_both_reported(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        reason = plan_option_mismatch(key, "REGULAR", "IDCW")
        assert reason == (
            "PLAN_MISMATCH(rta=DIRECT,sm=REGULAR); "
            "OPTION_MISMATCH(rta=GROWTH,sm=IDCW)"
        )

    def test_blank_structured_values_are_not_a_mismatch(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        assert plan_option_mismatch(key, None, None) is None
        assert plan_option_mismatch(key, "", "") is None
        assert plan_option_mismatch(key, "   ", None) is None

    def test_none_key_is_not_a_mismatch(self):
        # parse_scheme_key returns None for an empty/unparseable name; there
        # is nothing to corroborate against, so it must not be flagged.
        assert plan_option_mismatch(None, "DIRECT", "GROWTH") is None

    def test_structured_value_is_case_insensitive(self):
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        assert plan_option_mismatch(key, "direct", "growth") is None

    def test_pandas_nan_is_not_a_mismatch(self):
        # Reproduces a real bug: a missing SQL value read through pandas
        # comes back as float('nan'), which is truthy in plain Python, so
        # `if plan_type:` let it through and str()'d into the literal "NAN".
        key = parse_scheme_key("Axis Large Cap Fund - Direct Plan - Growth")
        nan = float("nan")
        assert plan_option_mismatch(key, nan, nan) is None
