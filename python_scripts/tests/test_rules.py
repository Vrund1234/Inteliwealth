from scheme_matching.rules import Candidate, arbitrate


def c(code, conf, rule, score=100.0):
    return Candidate(amfi_scheme_code=code, score=score, rule_name=rule, confidence=conf)


class TestArbitrate:
    def test_returns_none_for_no_candidates(self):
        assert arbitrate([]) is None

    def test_returns_the_only_candidate(self):
        only = c("100669", 98, "STRUCT_EXACT")
        assert arbitrate([only]) is only

    def test_highest_confidence_wins(self):
        low = c("111111", 90, "CORE_FUZZY")
        high = c("100669", 98, "STRUCT_EXACT")
        assert arbitrate([low, high]).amfi_scheme_code == "100669"

    def test_override_beats_everything_at_equal_confidence(self):
        """OVERRIDE and PRODUCT_MATCH are both 100; registry order breaks the tie."""
        product = c("222222", 100, "PRODUCT_MATCH")
        override = c("333333", 100, "OVERRIDE")
        assert arbitrate([product, override]).rule_name == "OVERRIDE"

    def test_tie_at_same_confidence_and_rule_prefers_higher_score(self):
        a = c("444444", 90, "CORE_FUZZY", score=91.0)
        b = c("555555", 90, "CORE_FUZZY", score=95.0)
        assert arbitrate([a, b]).amfi_scheme_code == "555555"


class TestCandidate:
    def test_is_hashable_and_comparable(self):
        a = c("100669", 98, "STRUCT_EXACT")
        b = c("100669", 98, "STRUCT_EXACT")
        assert a == b
        assert len({a, b}) == 1


from scheme_matching.rules import MatchContext, rule_struct_exact
from scheme_matching.scheme_key import parse_scheme_key


def ctx_with(pairs):
    """pairs: list of (amfi_name, amc_code, amfi_code)."""
    by_key = {}
    names = {}
    for amfi_name, amc, amfi_code in pairs:
        k = parse_scheme_key(amfi_name, amc_code=amc)
        by_key.setdefault(k, []).append(amfi_code)
        names[amfi_code] = amfi_name
    return MatchContext(amfi_by_key=by_key, amfi_names=names)


class TestStructExact:
    def test_matches_when_exactly_one_amfi_row_shares_the_key(self):
        context = ctx_with(
            [("CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", "101", "150816")]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "Canara Robeco Mid Cap Fund - Regular Growth", amc_code="101"
            )
        }
        out = rule_struct_exact(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "150816"
        assert out[0].confidence == 98
        assert out[0].rule_name == "STRUCT_EXACT"

    def test_returns_nothing_when_no_amfi_row_shares_the_key(self):
        context = ctx_with(
            [("CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", "101", "150816")]
        )
        row = {"scheme_key": parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")}
        assert rule_struct_exact(row, context) == []

    def test_returns_all_candidates_when_key_is_ambiguous(self):
        """Two AMFI rows on one key: emit both, let the tiebreak rule decide."""
        context = ctx_with(
            [
                ("AXIS TREASURY ADVANTAGE FUND REGULAR PLAN GROWTH", "128", "111111"),
                ("AXIS TREASURY ADVANTAGE FUND REGULAR GROWTH OPTION", "128", "222222"),
            ]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "Axis Treasury Advantage Fund - Regular Growth", amc_code="128"
            )
        }
        out = rule_struct_exact(row, context)
        assert len(out) == 2
        assert {c.amfi_scheme_code for c in out} == {"111111", "222222"}

    def test_growth_row_never_matches_an_idcw_key(self):
        context = ctx_with(
            [("HDFC HYBRID EQUITY FUND IDCW PLAN", "H", "102947")]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
            )
        }
        assert rule_struct_exact(row, context) == []

    def test_returns_nothing_when_row_has_no_key(self):
        assert rule_struct_exact({"scheme_key": None}, MatchContext()) == []


from scheme_matching.rules import NOT_IN_AMFI, rule_override


class TestOverride:
    def test_override_produces_a_candidate_at_confidence_100(self):
        context = MatchContext(overrides={("CAMS", "B02G"): "107745"})
        row = {"rta": "CAMS", "rta_scheme_code": "B02G"}
        out = rule_override(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "107745"
        assert out[0].confidence == 100
        assert out[0].rule_name == "OVERRIDE"

    def test_null_override_signals_not_in_amfi(self):
        """A curator asserting the fund does not exist in AMFI."""
        context = MatchContext(overrides={("KFIN", "906HLRG"): None})
        row = {"rta": "KFIN", "rta_scheme_code": "906HLRG"}
        out = rule_override(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code is NOT_IN_AMFI

    def test_no_override_returns_nothing(self):
        context = MatchContext(overrides={})
        assert rule_override({"rta": "CAMS", "rta_scheme_code": "B02G"}, context) == []

    def test_override_is_keyed_by_rta_and_code_together(self):
        """rta_scheme_code alone is not unique across RTAs."""
        context = MatchContext(overrides={("CAMS", "X1"): "111111"})
        assert rule_override({"rta": "KFIN", "rta_scheme_code": "X1"}, context) == []


from scheme_matching.rules import FUZZY_CUTOFF, FUZZY_MARGIN, rule_core_fuzzy


def bucket_ctx(key, entries):
    return MatchContext(amfi_by_bucket={key.bucket(): entries})


class TestCoreFuzzy:
    def test_matches_a_close_core_name(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(key, [("HDFC LARG CAP", "102001")])
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "102001"
        assert out[0].confidence == 90

    def test_rejects_below_the_cutoff(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(key, [("TOTALLY UNRELATED BOND", "999999")])
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_rejects_a_near_tie_even_when_both_clear_the_cutoff(self):
        """The margin guard. A near-tie means the name does not distinguish them."""
        key = parse_scheme_key("Axis Treasury Advantage Fund - Regular Growth", "128")
        context = bucket_ctx(
            key,
            [
                ("AXIS TREASURY ADVANTAGE", "111111"),
                ("AXIS TREASURY ADVANTAG", "222222"),
            ],
        )
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_accepts_when_the_margin_is_wide_enough(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(
            key,
            [
                ("HDFC LARG CAP", "111111"),
                ("HDFC SHORT TERM DEBT SOMETHING ELSE", "222222"),
            ],
        )
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "111111"

    def test_never_crosses_bucket_boundaries(self):
        """A Growth row must not fuzzy-match an IDCW candidate, however similar."""
        growth = parse_scheme_key("HDFC Flexi Cap Fund - Regular Plan - Growth", "H")
        idcw = parse_scheme_key("HDFC Flexi Cap Fund - Regular Plan - IDCW", "H")
        context = bucket_ctx(idcw, [("HDFC FLEXI CAP", "101763")])
        assert rule_core_fuzzy({"scheme_key": growth}, context) == []

    def test_returns_nothing_for_an_empty_bucket(self):
        key = parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")
        assert rule_core_fuzzy({"scheme_key": key}, MatchContext()) == []

    def test_guard_constants_match_the_spec(self):
        assert FUZZY_CUTOFF == 88
        assert FUZZY_MARGIN == 5


from scheme_matching.rules import option_from_prodcode, rule_struct_tiebreak


class TestOptionFromProdcode:
    """KFIN encodes the option in the product code suffix."""

    def test_growth_suffixes(self):
        assert option_from_prodcode("117IORG") == "GROWTH"
        assert option_from_prodcode("120EFGP") == "GROWTH"
        assert option_from_prodcode("108EQGP") == "GROWTH"

    def test_idcw_suffixes(self):
        assert option_from_prodcode("117IORD") == "IDCW"
        assert option_from_prodcode("120EFDP") == "IDCW"
        assert option_from_prodcode("120COID") == "IDCW"

    def test_unknown_suffix_returns_none(self):
        assert option_from_prodcode("B02X") is None

    def test_handles_none_and_empty(self):
        assert option_from_prodcode(None) is None
        assert option_from_prodcode("") is None


class TestStructTiebreak:
    def test_resolves_ambiguity_when_prodcode_agrees_with_one_candidate(self):
        key = parse_scheme_key("UTI Flexi Cap Fund - Regular Plan", "108")
        context = MatchContext(
            amfi_by_key={key: ["100669", "100668"]},
            amfi_names={
                "100669": "UTI FLEXI CAP FUND GROWTH OPTION",
                "100668": "UTI FLEXI CAP FUND REGULAR PLAN IDCW",
            },
        )
        row = {
            "rta": "KFIN",
            "rta_scheme_code": "108EQGP",
            "scheme_key": key,
        }
        out = rule_struct_tiebreak(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "100669"
        assert out[0].confidence == 95

    def test_returns_nothing_when_the_key_is_unambiguous(self):
        """Unambiguous keys are STRUCT_EXACT's job, not the tiebreaker's."""
        key = parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")
        context = MatchContext(amfi_by_key={key: ["101763"]})
        row = {"rta": "CAMS", "rta_scheme_code": "H01", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []

    def test_returns_nothing_when_prodcode_gives_no_signal(self):
        """No signal means route to review, not guess."""
        key = parse_scheme_key("Axis Treasury Advantage Fund - Regular Growth", "128")
        context = MatchContext(amfi_by_key={key: ["111111", "222222"]})
        row = {"rta": "KFIN", "rta_scheme_code": "128XXXX", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []

    def test_returns_nothing_when_prodcode_contradicts_the_name(self):
        """Name says IDCW, code says Growth. Disagreement is never resolved silently."""
        key = parse_scheme_key("Mirae Asset Large Cap Fund - Regular Plan IDCW", "117")
        context = MatchContext(amfi_by_key={key: ["107579", "118826"]})
        row = {"rta": "KFIN", "rta_scheme_code": "117IORG", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []


class TestCoreFuzzyRespectsIdentityBearingNumbers:
    """A series number IS the fund's identity for closed-end plans.

    "Series 48" and "Series 113" differ by one short token, so a similarity
    ratio barely registers the difference — but they are different funds with
    different maturities and different NAVs. Fuzzy matching must not bridge
    them. Both cases below are mappings this rule actually produced.
    """

    def test_rejects_a_different_series_number(self):
        key = parse_scheme_key(
            "Axis Fixed Term Plan - Series 48 (30 Days) - Regular Plan - Growth", "128"
        )
        other = parse_scheme_key(
            "Axis Fixed Term Plan - Series 113 (1228 Days) - Regular Plan - Growth", "128"
        )
        context = bucket_ctx(key, [(other.core_name, "146695")])
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_rejects_bandhan_series_1_against_series_179(self):
        key = parse_scheme_key("Bandhan Fixed Term Plan Series 1-Growth", "G")
        other = parse_scheme_key(
            "BANDHAN Fixed Term Plan Series 179 REGULAR PLAN-GROWTH (3652 days)", "G"
        )
        context = bucket_ctx(key, [(other.core_name, "146695")])
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_allows_extra_tenor_tokens_on_one_side_only(self):
        """The RTA often omits the day-count the AMFI name carries. That is
        additional detail, not a conflicting identity."""
        key = parse_scheme_key("HSBC Fixed Term Series 135 - Growth", "O")
        other = parse_scheme_key(
            "HSBC Fixed Term Series 135 1117 DAYS PLAN - Growth Option", "O"
        )
        context = bucket_ctx(key, [(other.core_name, "144115")])
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1

    def test_matching_numbers_still_match(self):
        key = parse_scheme_key("Motilal Oswal S and P 500 Index Fund - Regular Growth", "127")
        context = bucket_ctx(key, [("MOTILAL OSWAL S AND P 500 INDEX", "150001")])
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1

    def test_names_without_numbers_are_unaffected(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(key, [("HDFC LARG CAP", "102001")])
        assert len(rule_core_fuzzy({"scheme_key": key}, context)) == 1
