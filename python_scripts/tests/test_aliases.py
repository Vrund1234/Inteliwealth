import pytest

from scheme_matching.aliases import build_alias_fn, load_aliases
from scheme_matching.scheme_key import parse_scheme_key
from utils.db import master_engine

TOKEN_ROWS = [
    {"raw_term": "GR", "normalized_term": "GROWTH", "alias_type": "TOKEN", "amc_code": None},
    {"raw_term": "FTP", "normalized_term": "FIXED TERM PLAN", "alias_type": "TOKEN", "amc_code": None},
    {"raw_term": "MIP", "normalized_term": "MONTHLY INCOME PLAN", "alias_type": "TOKEN", "amc_code": None},
]

RENAME_ROWS = [
    {
        "raw_term": "LONG TERM EQUITY",
        "normalized_term": "ELSS TAX SAVER",
        "alias_type": "FUND_RENAME",
        "amc_code": "FTI",
    },
]


class TestTokenAliases:
    def test_gr_expands_to_growth(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "GROWTH" in fn("ABSL CREDIT RISK FUND GR REGULAR", "B")

    def test_ftp_expands(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "FIXED TERM PLAN" in fn("ABSL FTP RETAIL SERIES AF GROWTH", "B")

    def test_token_alias_only_matches_whole_words(self):
        """GR must not rewrite the GR inside GROWTH or GREEN."""
        fn = build_alias_fn(TOKEN_ROWS)
        assert fn("SOME GREEN ENERGY FUND", "B") == "SOME GREEN ENERGY FUND"

    def test_token_aliases_apply_to_every_amc(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "FIXED TERM PLAN" in fn("DSP FTP SERIES 41", "D")


class TestFundRenameAliases:
    def test_rename_applies_within_its_amc(self):
        fn = build_alias_fn(RENAME_ROWS)
        assert "ELSS TAX SAVER" in fn("FRANKLIN INDIA LONG TERM EQUITY FUND", "FTI")

    def test_rename_does_not_leak_to_other_amcs(self):
        """An AMC-scoped rename must never rewrite another AMC's fund."""
        fn = build_alias_fn(RENAME_ROWS)
        out = fn("HDFC LONG TERM EQUITY FUND", "H")
        assert "LONG TERM EQUITY" in out
        assert "ELSS TAX SAVER" not in out


class TestAliasIntegrationWithParser:
    def test_gr_abbreviation_matches_spelled_out_growth(self):
        fn = build_alias_fn(TOKEN_ROWS)
        rta = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund - Gr. REGULAR",
            amc_code="B",
            alias_fn=fn,
        )
        amfi = parse_scheme_key(
            "ADITYA BIRLA SUN LIFE CREDIT RISK FUND REGULAR PLAN GROWTH",
            amc_code="B",
            alias_fn=fn,
        )
        assert rta == amfi

    def test_no_aliases_is_a_no_op(self):
        fn = build_alias_fn([])
        assert fn("HDFC FLEXI CAP FUND", "H") == "HDFC FLEXI CAP FUND"


@pytest.fixture(scope="module")
def live_alias_fn():
    """The alias set actually configured in public.scheme_name_alias."""
    return build_alias_fn(load_aliases(master_engine))


class TestConfiguredAliases:
    """Guards on the alias rows the pipeline depends on, not on the machinery."""

    def test_reliance_rebrand_matches_nippon_india_schemes(self, live_alias_fn):
        """Reliance MF became Nippon India in 2019. The RTA uses the new name,
        the AMFI master still carries the old one for pre-rebrand schemes."""
        rta = parse_scheme_key(
            "NIPPON INDIA FIXED HORIZON FUND - XXV - SERIES 23 - GROWTH PLAN",
            amc_code="RMF",
            alias_fn=live_alias_fn,
        )
        amfi = parse_scheme_key(
            "Reliance Fixed Horizon Fund - XXV - Series 23 - Growth Option",
            amc_code="RMF",
            alias_fn=live_alias_fn,
        )
        assert rta == amfi

    def test_reliance_rebrand_does_not_leak_to_other_amcs(self, live_alias_fn):
        out = live_alias_fn("RELIANCE SOMETHING FUND", "P")
        assert "RELIANCE" in out
        assert "NIPPON" not in out

    def test_reliance_series_numbers_still_distinguish_schemes(self, live_alias_fn):
        """The rebrand must not collapse two different series onto one key."""
        s23 = parse_scheme_key(
            "NIPPON INDIA FIXED HORIZON FUND - XXV - SERIES 23 - GROWTH PLAN",
            amc_code="RMF", alias_fn=live_alias_fn,
        )
        s20 = parse_scheme_key(
            "NIPPON INDIA FIXED HORIZON FUND - XXV - SERIES 20 - GROWTH PLAN",
            amc_code="RMF", alias_fn=live_alias_fn,
        )
        assert s23 != s20

    def test_mid_cap_spelling_variants_produce_one_key(self, live_alias_fn):
        """"Axis Mid Cap" (RTA) and "Axis Midcap" (AMFI) are one fund."""
        rta = parse_scheme_key(
            "Axis Mid Cap Fund - Regular Growth", amc_code="128", alias_fn=live_alias_fn
        )
        amfi = parse_scheme_key(
            "Axis Midcap Fund - Regular Plan - Growth",
            amc_code="128", alias_fn=live_alias_fn,
        )
        assert rta == amfi

    def test_regular_savings_is_a_fund_name_not_a_plan_marker(self, live_alias_fn):
        """"Regular" is filler when it marks the plan, but "Regular Savings
        Fund" is a distinct fund. Stripping the word collapses it onto the
        unrelated "Savings Fund" and produced a confidently wrong mapping."""
        regular_savings = parse_scheme_key(
            "Aditya Birla Sun Life Regular Savings Fund - Growth-Regular Plan",
            amc_code="B", alias_fn=live_alias_fn,
        )
        savings = parse_scheme_key(
            "Aditya Birla Sun Life Savings Fund - Growth-Regular Plan",
            amc_code="B", alias_fn=live_alias_fn,
        )
        assert regular_savings != savings

    def test_regular_savings_still_reads_the_plan_from_the_suffix(self, live_alias_fn):
        """Protecting the fund name must not stop "Regular Plan" being parsed."""
        k = parse_scheme_key(
            "Aditya Birla Sun Life Regular Savings Fund - Growth-Regular Plan",
            amc_code="B", alias_fn=live_alias_fn,
        )
        assert k.plan == "REGULAR"
        direct = parse_scheme_key(
            "Aditya Birla Sun Life Regular Savings Fund - Growth - Direct Plan",
            amc_code="B", alias_fn=live_alias_fn,
        )
        assert direct.plan == "DIRECT"
        assert k != direct

    def test_mid_cap_normalisation_does_not_merge_large_and_mid_cap(self, live_alias_fn):
        """"Large & Mid Cap" is a different category from "Mid Cap"."""
        mid = parse_scheme_key(
            "Axis Mid Cap Fund - Regular Growth", amc_code="128", alias_fn=live_alias_fn
        )
        large_mid = parse_scheme_key(
            "Axis Large & Mid Cap Fund - Regular Plan - Growth",
            amc_code="128", alias_fn=live_alias_fn,
        )
        assert mid != large_mid
