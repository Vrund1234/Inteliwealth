import pytest

from scheme_matching.aliases import build_alias_fn
from scheme_matching.scheme_key import (
    SchemeKey,
    roman_to_arabic,
    extract_attributes,
    parse_scheme_key,
    strip_parentheticals,
)


class TestStripParentheticals:
    def test_removes_formerly_known_as(self):
        out = strip_parentheticals(
            "MIRAE ASSET LIQUID FUND ( FORMERLY MIRAE ASSET CASH MANAGEMENT FUND ) "
            "- REGULAR PLAN"
        )
        assert "CASH MANAGEMENT" not in out
        assert "MIRAE ASSET LIQUID FUND" in out

    def test_removes_erstwhile(self):
        out = strip_parentheticals(
            "FRANKLIN INDIA FLEXI CAP FUND - GROWTH (ERSTWHILE FRANKLIN INDIA EQUITY FUND)"
        )
        assert "EQUITY FUND" not in out
        assert "FLEXI CAP" in out

    def test_removes_elss_boilerplate(self):
        out = strip_parentheticals(
            "ADITYA BIRLA SUN LIFE TAX PLAN - (ELSS U/S 80C OF IT ACT) - GROWTH"
        )
        assert "80C" not in out
        assert "TAX PLAN" in out

    def test_removes_maturity_date(self):
        out = strip_parentheticals(
            "ADITYA BIRLA SUN LIFE FIXED TERM PLAN SERIES ED "
            "- (MATURITY DATE - 10-JUL-2014) - GROWTH"
        )
        assert "2014" not in out
        assert "SERIES ED" in out

    def test_keeps_unrelated_parentheticals_content(self):
        """A parenthetical that is not a known annotation keeps its words."""
        out = strip_parentheticals("KOTAK MULTI ASSET OMNI FOF GROWTH (REGULAR PLAN)")
        assert "REGULAR" in out


class TestExtractAttributes:
    def test_regular_plan_becomes_plan_attribute_not_deleted(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH")
        assert attrs["plan"] == "REGULAR"

    def test_direct_plan_detected(self):
        _, attrs = extract_attributes("CANARA ROBECO MID CAP FUND DIRECT PLAN GROWTH")
        assert attrs["plan"] == "DIRECT"

    def test_plan_defaults_to_regular(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND GROWTH")
        assert attrs["plan"] == "REGULAR"

    def test_idcw_detected(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN IDCW")
        assert attrs["option"] == "IDCW"

    def test_dividend_is_idcw(self):
        _, attrs = extract_attributes(
            "HDFC ARBITRAGE FUND QUARTERLY DIVIDEND REINVESTMENT OPTION"
        )
        assert attrs["option"] == "IDCW"

    def test_option_defaults_to_growth(self):
        """UTI Flexi Cap Fund - Regular Plan carries no option token."""
        _, attrs = extract_attributes("UTI FLEXI CAP FUND REGULAR PLAN")
        assert attrs["option"] == "GROWTH"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HDFC LOW DURATION FUND WEEKLY IDCW", "WEEKLY"),
            ("DSP ULTRA SHORT FUND REGULAR PLAN IDCW DAILY", "DAILY"),
            ("ABSL REGULAR SAVINGS FUND REGULAR MONTHLY IDCW", "MONTHLY"),
            ("HDFC ARBITRAGE FUND RETAIL PLAN QUARTERLY DIVIDEND", "QUARTERLY"),
            ("MOTILAL OSWAL BALANCED ADVANTAGE FUND REGULAR ANNUAL IDCW", "ANNUAL"),
            ("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH", None),
        ],
    )
    def test_frequency(self, text, expected):
        _, attrs = extract_attributes(text)
        assert attrs["frequency"] == expected

    def test_hyphenated_half_yearly_is_not_read_as_annual(self):
        """"Half-Yearly" must not fall through to the bare YEARLY pattern —
        that silently files a half-yearly payout as an annual one."""
        _, attrs = extract_attributes("LIC MF ULIS (15 Yrs. Cover Half-Yearly) - IDCW")
        assert attrs["frequency"] == "HALF_YEARLY"

    def test_half_yearly_normalizes_with_underscore(self):
        _, attrs = extract_attributes("SOME FUND HALF YEARLY IDCW")
        assert attrs["frequency"] == "HALF_YEARLY"

    def test_qualifiers_are_captured_not_discarded(self):
        _, attrs = extract_attributes(
            "HDFC ARBITRAGE FUND RETAIL PLAN QUARTERLY DIVIDEND"
        )
        assert "RETAIL" in attrs["qualifiers"]

    def test_segregated_captured(self):
        _, attrs = extract_attributes(
            "NIPPON INDIA CREDIT RISK FUND SEGREGATED PORTFOLIO 1 GROWTH"
        )
        assert "SEGREGATED" in attrs["qualifiers"]

    def test_no_qualifiers_is_empty_frozenset(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH")
        assert attrs["qualifiers"] == frozenset()


class TestParseSchemeKey:
    def test_rta_and_amfi_names_produce_the_same_key(self):
        """The whole point of the design: divergent names, identical keys."""
        rta = parse_scheme_key(
            "Canara Robeco Mid Cap Fund - Regular Growth", amc_code="101"
        )
        amfi = parse_scheme_key(
            "CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", amc_code="101"
        )
        assert rta == amfi

    def test_hdfc_hybrid_equity_growth_matches(self):
        rta = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
        )
        amfi = parse_scheme_key("HDFC HYBRID EQUITY FUND GROWTH PLAN", amc_code="H")
        assert rta == amfi

    def test_growth_and_idcw_variants_never_collide(self):
        """The most dangerous failure mode this design must prevent."""
        growth = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
        )
        idcw = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - IDCW", amc_code="H"
        )
        assert growth != idcw

    def test_discontinued_share_class_never_collides_with_the_live_plan(self):
        """A discontinued class has its own NAV series that AMFI stops
        publishing. Folding it onto the surviving plan attaches wrong prices."""
        live = parse_scheme_key(
            "DSP Strategic Bond Fund - Regular Plan - Growth", amc_code="D"
        )
        gone = parse_scheme_key(
            "DSP Strategic Bond Fund - Regular Plan - Growth (Discontinued)",
            amc_code="D",
        )
        assert live != gone

    def test_discontinued_is_captured_as_a_qualifier_not_left_in_core_name(self):
        k = parse_scheme_key(
            "DSP Strategic Bond Fund - Regular Plan - Growth (Discontinued)",
            amc_code="D",
        )
        assert "DISCONTINUED" in k.qualifiers
        assert "DISCONTINUED" not in k.core_name

    def test_retail_and_non_retail_never_collide(self):
        plain = parse_scheme_key("HDFC Arbitrage Fund - Quarterly IDCW", amc_code="H")
        retail = parse_scheme_key(
            "HDFC Arbitrage Fund - Retail Plan - Quarterly IDCW", amc_code="H"
        )
        assert plain != retail

    def test_daily_and_weekly_idcw_never_collide(self):
        daily = parse_scheme_key(
            "Aditya Birla Sun Life Low Duration Fund - Daily IDCW", amc_code="B"
        )
        weekly = parse_scheme_key(
            "Aditya Birla Sun Life Low Duration Fund - Weekly IDCW", amc_code="B"
        )
        assert daily != weekly

    def test_ampersand_and_and_are_equivalent(self):
        a = parse_scheme_key(
            "Aditya Birla Sun Life Pharma & Healthcare Fund Regular Growth", amc_code="B"
        )
        b = parse_scheme_key(
            "ADITYA BIRLA SUN LIFE PHARMA AND HEALTHCARE FUND REGULAR GROWTH",
            amc_code="B",
        )
        assert a == b

    def test_appreciation_is_a_legacy_growth_label_not_a_distinguishing_word(self):
        """CAMS writes the Growth option as "Growth(Appreciation)" on some Tata
        schemes. Left in the core name it blocks an otherwise exact match."""
        rta = parse_scheme_key(
            "Tata Mid Cap Fund Regular Plan - Growth(Appreciation)", amc_code="T"
        )
        amfi = parse_scheme_key(
            "Tata Mid Cap Fund Regular Plan- Growth Option", amc_code="T"
        )
        assert rta == amfi

    def test_appreciation_does_not_collapse_growth_and_idcw(self):
        """Dropping the word must not weaken the option guard."""
        growth = parse_scheme_key(
            "Tata Mid Cap Fund Regular Plan - Growth(Appreciation)", amc_code="T"
        )
        idcw = parse_scheme_key(
            "Tata Mid Cap Fund Regular Plan - IDCW", amc_code="T"
        )
        assert growth != idcw

    def test_returns_none_for_blank_name(self):
        assert parse_scheme_key("", amc_code="H") is None
        assert parse_scheme_key(None, amc_code="H") is None

    def test_key_is_hashable(self):
        """Keys are used as dict keys for candidate lookup."""
        k = parse_scheme_key("HDFC Flexi Cap Fund - Growth", amc_code="H")
        assert isinstance(hash(k), int)
        assert len({k, k}) == 1


class TestSegregatedPortfolioPercentage:
    """CAMS states the segregated portion as a percentage; AMFI does not.

    "(Segregated Portfolio - 1 with 3.69%)" against the master's
    "Segregated Portfolio 1". The percentage is a property of one AMC's
    reporting, not of the share class -- it is the same fund either way, and
    it varies between distributor files for a single scheme. Left in, it lands
    in the core name as "WITH 3 69" and blocks a match the NAV fingerprint had
    already pinpointed.
    """

    def test_the_percentage_suffix_does_not_block_a_match(self):
        """The live CAMS/AMFI pair for BSCRB.

        The alias is supplied because the pipeline supplies it: this name also
        abbreviates the option as "Gr.", and GR -> GROWTH is a seeded row. The
        test would otherwise fail on the abbreviation rather than on the
        percentage it is meant to cover.
        """
        alias_fn = build_alias_fn([
            {"raw_term": "GR", "normalized_term": "GROWTH",
             "alias_type": "TOKEN", "amc_code": None},
        ])
        rta = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund Gr. REGULAR "
            "(Segregated Portfolio - 1 with 3.69%)",
            alias_fn=alias_fn,
        )
        amfi = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund- Segregated Portfolio 1-"
            "Regular Plan- Growth",
            alias_fn=alias_fn,
        )
        assert rta == amfi

    def test_a_whole_number_percentage_is_handled_too(self):
        rta = parse_scheme_key(
            "Aditya Birla Sun Life Medium Term Plan Growth Regular Plan "
            "(Segregated Portfolio - 1 with 7%)"
        )
        amfi = parse_scheme_key(
            "Aditya Birla Sun Life Medium term Plan- Segregated Portfolio 1- "
            "Growth- Regular Plan"
        )
        assert rta == amfi

    def test_the_portfolio_number_is_still_significant(self):
        """Portfolio 1 and Portfolio 2 are different pools of assets."""
        one = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund Gr. REGULAR "
            "(Segregated Portfolio - 1 with 3.69%)"
        )
        two = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund Gr. REGULAR "
            "(Segregated Portfolio - 2 with 3.69%)"
        )
        assert one != two

    def test_the_segregated_qualifier_survives(self):
        """A segregated share class must never merge with the main scheme."""
        seg = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund Gr. REGULAR "
            "(Segregated Portfolio - 1 with 3.69%)"
        )
        main = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund - Regular Plan - Growth"
        )
        assert "SEGREGATED" in seg.qualifiers
        assert seg != main

    def test_the_word_with_is_untouched_elsewhere(self):
        """Only a percentage is stripped, not every occurrence of "with"."""
        k = parse_scheme_key("Some Fund With Growth Advantage - Regular Plan - Growth")
        assert "WITH" in k.core_name


class TestRomanNumeralHelper:
    """roman_to_arabic is a utility, NOT applied when building core names.

    It lives here because it parses names, but scheme_key deliberately does not
    call it: rules.numbers_conflict keeps roman series markers in a namespace
    of their own, and collapsing them into digits made "XXII - Series 11" and
    "XXII - Series 22" both carry 22, so the subset test stopped seeing a
    contradiction and CORE_FUZZY mapped one onto the other. Only
    nav_name_match.match_nav_anchored uses it, where the comparison is confined
    to one scheme's own NAV candidates.
    """

    @pytest.mark.parametrize("roman,arabic", [
        ("II", "2"), ("III", "3"), ("IV", "4"), ("VI", "6"), ("VII", "7"),
        ("VIII", "8"), ("IX", "9"), ("XI", "11"), ("XIV", "14"), ("XVII", "17"),
        ("XIX", "19"), ("XXII", "22"), ("XXVIII", "28"), ("XXX", "30"),
        ("XL", "40"), ("XLII", "42"), ("XLVI", "46"), ("XLIX", "49"),
    ])
    def test_the_full_range_of_real_series_numerals(self, roman, arabic):
        assert roman_to_arabic(roman) == arabic

    @pytest.mark.parametrize("letter", ["C", "D", "L", "M", "V", "X", "I"])
    def test_a_single_letter_is_never_a_numeral(self, letter):
        """Series C is the letter C, not 100. The master holds Series C x170,
        V x134, X x116, D x20, M x15 and L x7."""
        assert roman_to_arabic(letter) is None

    @pytest.mark.parametrize("code", ["LI", "LV", "LX", "CC", "CD", "CI", "CL",
                                      "CM", "CV", "CX", "DC", "DI", "DL", "DV",
                                      "DX", "MD", "MX"])
    def test_two_letter_series_labels_above_the_cap_are_refused(self, code):
        """Every one is a real ABSL fixed term plan label, and every one parses
        as 51 or more. Genuine series numerals in the master stop at XLVI."""
        assert roman_to_arabic(code) is None

    @pytest.mark.parametrize("code", ["ED", "FF", "JJ", "CH", "CK", "CP", "DD"])
    def test_series_labels_outside_the_roman_alphabet_are_refused(self, code):
        assert roman_to_arabic(code) is None

    def test_a_word_that_parses_as_a_large_numeral_is_refused(self):
        """MIX is technically M+IX = 1009."""
        assert roman_to_arabic("MIX") is None

    def test_malformed_numerals_are_refused(self):
        for bad in ("IIII", "VV", "IC", "", None):
            assert roman_to_arabic(bad) is None


class TestCoreNamesLeaveRomansAlone:
    """The safety property the revert restored."""

    def test_a_roman_series_marker_survives_in_the_core_name(self):
        k = parse_scheme_key(
            "Reliance Fixed Horizon Fund - XXII - Series 11 - Growth Option")
        assert "XXII" in k.core_name.split()
        assert "22" not in k.core_name.split()

    def test_series_11_and_series_22_of_one_group_stay_distinct(self):
        eleven = parse_scheme_key(
            "Reliance Fixed Horizon Fund - XXII - Series 11 - Growth Option")
        twentytwo = parse_scheme_key(
            "Reliance Fixed Horizon Fund - XXII - Series 22 - Growth Option")
        assert eleven != twentytwo

    @pytest.mark.parametrize("letter", ["C", "D", "L", "M", "V", "X"])
    def test_a_single_letter_series_survives(self, letter):
        k = parse_scheme_key(
            f"Sundaram Hybrid Fund Series {letter} Regular Plan Growth")
        assert letter in k.core_name.split()

    def test_certificate_of_deposit_is_not_four_hundred(self):
        k = parse_scheme_key("Principal Bank CD Fund - Growth Option")
        assert "CD" in k.core_name.split()
