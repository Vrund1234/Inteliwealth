"""Guards on the AMFI naming conventions the parser has to survive.

Every case here was taken from a real line of
https://portal.amfiindia.com/spages/NAVAll.txt, and each one currently
produces a wrong key or a wrong option.
"""
import pytest

from scheme_matching.rules import numbers_conflict
from scheme_matching.scheme_key import (
    OPTION_GROWTH,
    OPTION_IDCW,
    parse_scheme_key,
)


class TestSpelledOutIdcw:
    """SEBI's 2021 circular replaced "Dividend" with "Income Distribution cum
    Capital Withdrawal". 654 live AMFI schemes are named that way and 350 of
    them carry no IDCW/DIVIDEND token at all."""

    def test_payout_of_income_distribution_is_an_idcw_share_class(self):
        key = parse_scheme_key(
            "TATA Arbitrage Fund Regular Plan - Monthly Payout of Income "
            "Distribution cum capital withdrawal option"
        )
        assert key.option == OPTION_IDCW

    def test_reinvestment_of_income_distribution_is_an_idcw_share_class(self):
        key = parse_scheme_key(
            "TATA Arbitrage Fund Regular Plan - Monthly Reinvestment of Income "
            "Distribution cum capital withdrawal option"
        )
        assert key.option == OPTION_IDCW

    def test_idcw_phrase_is_not_left_in_the_core_name(self):
        key = parse_scheme_key(
            "SBI Flexicap Fund - Regular Plan - Income Distribution cum "
            "Capital Withdrawal Option (IDCW)"
        )
        assert key.core_name == "SBI FLEXICAP"

    def test_rta_idcw_matches_the_amfi_spelled_out_form(self):
        rta = parse_scheme_key("SBI Flexicap Fund - Regular Plan - IDCW", amc_code="L")
        amfi = parse_scheme_key(
            "SBI Flexicap Fund - Regular Plan - Income Distribution cum "
            "Capital Withdrawal Option (IDCW)",
            amc_code="L",
        )
        assert rta == amfi

    def test_capital_is_kept_when_it_is_part_of_the_fund_name(self):
        """397 live names use CAPITAL outside the IDCW phrase, so the phrase
        must be removed as a phrase and never word by word."""
        key = parse_scheme_key("HDFC Capital Builder Value Fund - Growth Plan")
        assert "CAPITAL" in key.core_name


class TestPayoutShareClass:
    """PAYOUT appears in 1209 live names and never alongside GROWTH."""

    def test_payout_variant_is_idcw_not_growth(self):
        key = parse_scheme_key(
            "Edelweiss NIFTY Large Midcap 250 Index Fund - Regular Plan Payout"
        )
        assert key.option == OPTION_IDCW

    def test_payout_and_growth_variants_never_collide(self):
        growth = parse_scheme_key(
            "Edelweiss NIFTY Large Midcap 250 Index Fund - Regular Plan Growth",
            amc_code="118",
        )
        payout = parse_scheme_key(
            "Edelweiss NIFTY Large Midcap 250 Index Fund - Regular Plan Payout",
            amc_code="118",
        )
        assert growth != payout


class TestFundOfFunds:
    """FUND and OF are both filler, so "X ETF Fund of Fund" reduces to "X ETF"
    -- a fund whose NAV is 6x different."""

    def test_fund_of_fund_never_collides_with_the_etf_it_tracks(self):
        etf = parse_scheme_key("DSP Gold ETF", amc_code="D")
        fof = parse_scheme_key("DSP Gold ETF Fund of Fund - Regular - Growth", amc_code="D")
        assert etf != fof

    def test_fund_of_funds_plural_is_also_distinguished(self):
        etf = parse_scheme_key("Edelweiss Gold ETF", amc_code="118")
        fof = parse_scheme_key(
            "Edelweiss Gold ETF Fund of Funds Regular Plan Growth Option", amc_code="118"
        )
        assert etf != fof


class TestPaymentSuffix:
    """ABSL writes its growth option as "Growth / Payment"."""

    def test_growth_payment_matches_a_plain_growth_name(self):
        rta = parse_scheme_key(
            "Aditya Birla Sun Life Regular Savings Fund - Growth-Regular Plan",
            amc_code="B",
        )
        amfi = parse_scheme_key(
            "Aditya Birla Sun Life Regular Savings Fund - Growth / Payment - Regular Plan",
            amc_code="B",
        )
        assert rta == amfi


class TestRomanNumeralSeries:
    """Closed-end series are identified by a Roman numeral. numbers_conflict()
    only reads Arabic digits, so XIV scored 98.7% against XXIV."""

    def test_different_roman_series_are_a_conflict(self):
        assert numbers_conflict(
            "NIPPON INDIA FIXED HORIZON XIV SERIES 3",
            "NIPPON INDIA FIXED HORIZON XXIV SERIES 3",
        )

    def test_malformed_series_numeral_still_counts_as_a_marker(self):
        """AMFI ships "XXXX" as a series label on 55 live schemes. It is not a
        well-formed numeral, but it is still the thing that identifies the
        series, so it has to take part in the conflict check."""
        assert numbers_conflict(
            "NIPPON INDIA FIXED HORIZON XIV SERIES 3",
            "NIPPON INDIA FIXED HORIZON XXXX SERIES 3",
        )

    def test_roman_letters_outside_a_series_name_are_never_markers(self):
        """ICICI, LIC, MID and DIV are all spelled with Roman-numeral letters
        and appear in 3,000+ live names. Only a name that says SERIES is
        using the numeral as identity."""
        assert not numbers_conflict(
            "ICICI PRUDENTIAL MID CAP", "LIC MF LARGE CAP"
        )

    def test_same_roman_series_is_not_a_conflict(self):
        assert not numbers_conflict(
            "NIPPON INDIA FIXED HORIZON XXV SERIES 23",
            "NIPPON INDIA FIXED HORIZON XXV SERIES 23",
        )

    @pytest.mark.parametrize(
        "left,right",
        [
            # Same fund, one side carries an extra word spelled with Roman letters.
            ("HDFC MIDCAP OPPORTUNITIES", "HDFC MIDCAP"),
            ("SBI MAGNUM MIDCAP", "SBI MAGNUM MIDCAP DIV"),
            ("ICICI PRUDENTIAL MIP", "ICICI PRUDENTIAL MIP CI"),
        ],
    )
    def test_ordinary_words_made_of_roman_letters_are_not_series_markers(self, left, right):
        """MID, DIV, MC and friends are spelled with Roman-numeral letters.
        A marker only one side carries is extra detail, not a contradiction."""
        assert not numbers_conflict(left, right)
