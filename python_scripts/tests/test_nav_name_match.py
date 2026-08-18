"""Structured-key matching restricted to a NAV-derived candidate set.

The schemes this rule exists for are closed/matured ones that live only in
public.scheme_master. The structured engine indexes public.amfi_scheme_master,
so it never sees them; NAV lookup does reach scheme_master, so NAV is what
narrows the universe from 37,764 names to a handful before any name comparison
happens.

Every assertion here is drawn from a real row in the 56 the last run left
UNMATCHED. The rejection cases matter more than the acceptance ones: fuzzy
scoring ranked a WRONG scheme above the right one on L555G, which is why this
rule compares parsed keys and never a similarity score.
"""

from scheme_matching.alias_suggest import build_key_index
from scheme_matching.aliases import build_alias_fn
from scheme_matching.nav_name_match import (
    AMBIGUOUS,
    NO_MATCH,
    RESOLVED,
    match_in_key_index,
    match_nav_anchored,
    match_within_candidates,
    nav_candidate_union,
)


class TestNavCandidateUnion:
    """Rule 3.5 intersects per-date candidate sets; this rule unions them.

    Intersection is why 33 of the 56 fail: a closed fund's sampled prices
    include its Rs.10 launch NAV, which thousands of schemes share, and its
    real NAV on another date, which only it has. The correct scheme is in
    exactly one of those sets, so the intersection excludes it.
    """

    def test_a_code_from_only_one_sampled_date_is_still_a_candidate(self):
        lookup = {
            ("2014-07-10", 12.4275): {"116448"},
            ("2012-01-12", 10.0): {"100182", "104548"},
        }
        samples = [("2014-07-10", 12.4275), ("2012-01-12", 10.0)]

        assert nav_candidate_union(samples, lookup) == {
            "116448", "100182", "104548",
        }

    def test_a_date_absent_from_nav_master_contributes_nothing(self):
        lookup = {("2021-05-19", 46.6449): {"101818"}}
        samples = [("2021-09-21", 49.4533), ("2021-05-19", 46.6449)]

        assert nav_candidate_union(samples, lookup) == {"101818"}

    def test_no_samples_yields_no_candidates(self):
        assert nav_candidate_union([], {("2020-01-01", 1.0): {"999"}}) == set()


class TestExactKeyMatchResolves:
    def test_a_lone_candidate_with_an_equal_key_is_the_match(self):
        """128Z6GP: the RTA omits "Regular Plan", the key supplies it."""
        result = match_within_candidates(
            "Axis Fixed Term Plan - Series 48 (30 Days) - Growth",
            {"126248": "Axis Fixed Term Plan - Series 48 (30 Days) - Regular Plan - Growth"},
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "126248"

    def test_the_matching_candidate_is_picked_out_of_a_crowd(self):
        """128WHGP: 30 NAV candidates, only one shares the key."""
        result = match_within_candidates(
            "Axis Fixed Term Plan - Series 96 (1124 Days) - Growth",
            {
                "144830": "Axis Fixed Term Plan - Series 96 (1124 Days) - Regular Plan - Growth Option",
                "144722": "IDFC Fixed Term Plan Series 161 REGULAR PLAN-GROWTH (1098 days)",
                "121540": "HDFC Annual Interval Fund - Series I - Plan A-Regular Option-Flexi Option",
            },
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "144830"


class TestWrongSchemesAreRejected:
    """The cases fuzzy scoring got wrong. Each must yield no match at all."""

    def test_a_different_duration_is_not_the_same_scheme(self):
        """L555G. Fuzzy ranked the 385-day fund at 94.7 over the true 1228-day
        one — the highest score of any candidate. The duration token is the
        only thing separating them, and the key keeps it."""
        result = match_within_candidates(
            "SBI Debt Fund Series - C - 30 (1228 Days) - Regular Growth",
            {"145646": "SBI Debt Fund Series C - 30 - (385 Days) - Regular Plan - Growth"},
        )

        assert result.status == NO_MATCH
        assert result.amfi_scheme_code is None

    def test_the_idcw_share_class_is_not_the_growth_one(self):
        """Same fund, same duration, different option. Distinct NAV series."""
        result = match_within_candidates(
            "SBI Debt Fund Series - C - 30 (1228 Days) - Regular Growth",
            {
                "145644": "SBI Debt Fund Series - C - 30 (1228 Days) - Regular Plan - "
                          "Income Distribution cum Capital Withdrawal Option (IDCW)"
            },
        )

        assert result.status == NO_MATCH

    def test_an_unrelated_fund_sharing_a_launch_nav_is_rejected(self):
        """P1644: every candidate reached it via the Rs.10 launch price."""
        result = match_within_candidates(
            "ICICI Prudential FMP Series 53 - 18 Months Plan A Cumulative",
            {
                "104551": "Fidelity Cash Fund - Super Institutional Plan - Monthly Dividend Option",
                "100184": "ING Income Fund-Institutional Plan -Dividend Option (Annual)",
            },
        )

        assert result.status == NO_MATCH


class TestAmbiguityIsNeverGuessed:
    def test_two_candidates_sharing_a_key_resolve_to_nothing(self):
        result = match_within_candidates(
            "Axis Fixed Term Plan - Series 48 (30 Days) - Growth",
            {
                "126248": "Axis Fixed Term Plan - Series 48 (30 Days) - Regular Plan - Growth",
                "126249": "Axis Fixed Term Plan Series 48 (30 Days) Regular Growth Option",
            },
        )

        assert result.status == AMBIGUOUS
        assert result.amfi_scheme_code is None
        assert set(result.matches) == {"126248", "126249"}


class TestRenamedAmcsResolveThroughAliases:
    """AMFI/scheme_master carry the pre-rename name; the RTA carries the new
    one. Without an alias the cores differ by the AMC token and nothing
    matches — this is what held 23 of the 31 back."""

    def test_nippon_india_matches_its_reliance_era_name(self):
        alias_fn = build_alias_fn([
            {"raw_term": "NIPPON INDIA", "normalized_term": "RELIANCE",
             "alias_type": "FUND_RENAME", "amc_code": None},
        ])

        result = match_within_candidates(
            "NIPPON INDIA FIXED HORIZON FUND - XIV - SERIES 3 - GROWTH PLAN",
            {"112727": "Reliance Fixed Horizon Fund - XIV - Series 3 - Growth Option"},
            alias_fn=alias_fn,
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "112727"

    def test_without_the_alias_the_same_pair_does_not_match(self):
        result = match_within_candidates(
            "NIPPON INDIA FIXED HORIZON FUND - XIV - SERIES 3 - GROWTH PLAN",
            {"112727": "Reliance Fixed Horizon Fund - XIV - Series 3 - Growth Option"},
        )

        assert result.status == NO_MATCH

    def test_an_alias_does_not_collapse_distinct_series(self):
        """The rename must not make Series 3 and Series 4 interchangeable."""
        alias_fn = build_alias_fn([
            {"raw_term": "NIPPON INDIA", "normalized_term": "RELIANCE",
             "alias_type": "FUND_RENAME", "amc_code": None},
        ])

        result = match_within_candidates(
            "NIPPON INDIA FIXED HORIZON FUND - XIV - SERIES 3 - GROWTH PLAN",
            {"114272": "Reliance Fixed Horizon Fund - XVI - Series - 4 - Growth Option"},
            alias_fn=alias_fn,
        )

        assert result.status == NO_MATCH


class TestDegenerateInput:
    def test_an_empty_candidate_set_yields_no_match(self):
        result = match_within_candidates("Axis Fixed Term Plan - Growth", {})

        assert result.status == NO_MATCH
        assert result.amfi_scheme_code is None

    def test_a_candidate_with_no_name_is_skipped_not_crashed_on(self):
        result = match_within_candidates(
            "Axis Fixed Term Plan - Series 48 (30 Days) - Growth",
            {
                "999999": None,
                "126248": "Axis Fixed Term Plan - Series 48 (30 Days) - Regular Plan - Growth",
            },
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "126248"

    def test_an_rta_name_that_will_not_parse_yields_no_match(self):
        result = match_within_candidates("", {"126248": "Axis Fixed Term Plan - Growth"})

        assert result.status == NO_MATCH


class TestMatchingAgainstAPrebuiltKeyIndex:
    """Tier 2: exact key equality over the whole historical universe, with no
    NAV filter, for the schemes whose NAV evidence is missing or unusable.

    Safe only because the test is unchanged -- full SchemeKey equality AND
    uniqueness across 39,640 names. Cross-validated against the NAV-filtered
    tier on live data: where both fire they agreed 26/26, never once
    disagreeing.
    """

    def _index(self, names):
        return build_key_index(names, alias_fn=None)

    def test_a_key_held_by_exactly_one_scheme_resolves(self):
        index = self._index({
            "115422": "ICICI Prudential Fixed Maturity Plan - Series 58 - 2 Year Plan A Cumulative",
            "999999": "Axis Flexi Cap Fund - Regular Plan - Growth",
        })

        result = match_in_key_index(
            "ICICI Prudential Fixed Maturity Plan Series 58 - 2 Year Plan A Cumulative",
            index,
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "115422"

    def test_a_key_held_by_several_schemes_is_ambiguous(self):
        index = self._index({
            "1": "Axis Flexi Cap Fund - Regular Plan - Growth",
            "2": "Axis Flexi Cap Fund Regular Growth Option",
        })

        result = match_in_key_index("Axis Flexi Cap Fund - Regular - Growth", index)

        assert result.status == AMBIGUOUS
        assert result.amfi_scheme_code is None
        assert set(result.matches) == {"1", "2"}

    def test_a_key_nobody_holds_yields_no_match(self):
        index = self._index({"1": "Axis Flexi Cap Fund - Regular Plan - Growth"})

        result = match_in_key_index("HDFC Balanced Advantage Fund - Growth", index)

        assert result.status == NO_MATCH

    def test_the_growth_and_idcw_share_classes_stay_separate(self):
        index = self._index({
            "1": "Axis Flexi Cap Fund - Regular Plan - Growth",
            "2": "Axis Flexi Cap Fund - Regular Plan - IDCW",
        })

        result = match_in_key_index("Axis Flexi Cap Fund - Regular Plan - IDCW", index)

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "2"

    def test_an_unparseable_name_yields_no_match(self):
        assert match_in_key_index("", self._index({"1": "Axis Flexi Cap Fund"})).status == NO_MATCH


class TestNavAnchoredFuzzyMatch:
    """Tier 3: a looser name test, safe only because the NAV set is tiny.

    Tiers 1 and 2 need exact key equality, which rejects RMFGSGP over "Mid Cap"
    vs "Midcap" even though its NAV pins it to exactly one scheme. Loosening is
    safe only while the tokens that SEPARATE sibling schemes are still compared
    exactly -- in these names, the numbers: series, duration, portfolio.

    L555G is the case that defines the guard. Fuzzy scoring ranked the 385-day
    SBI fund above the true 1228-day one; nothing about similarity catches
    that, but the numeric multiset does.
    """

    def test_a_spelling_variant_resolves_when_nav_gives_one_candidate(self):
        """RMFGSGP: MID CAP vs MIDCAP, everything else identical."""
        result = match_nav_anchored(
            "NIPPON INDIA VISION LARGE & MID CAP FUND - GROWTH PLAN GROWTH OPTION",
            {"100380": "Nippon India Vision Large & Midcap Fund-GROWTH PLAN-Growth Option"},
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "100380"

    def test_a_different_duration_is_rejected_however_similar(self):
        """L555G. 87.2 similarity, and still the wrong fund."""
        result = match_nav_anchored(
            "SBI Debt Fund Series - C - 30 (1228 Days) - Regular Growth",
            {"145646": "SBI Debt Fund Series C - 30 - (385 Days) - Regular Plan - Growth"},
        )

        assert result.status == NO_MATCH

    def test_a_different_series_number_is_rejected(self):
        result = match_nav_anchored(
            "Bandhan Fixed Term Plan Series 1-Growth",
            {"117773": "IDFC Fixed Term Plan Series 140 Regular Plan-Growth"},
        )

        assert result.status == NO_MATCH

    def test_the_idcw_sibling_is_still_rejected(self):
        """Attribute equality is not relaxed just because the name is close."""
        result = match_nav_anchored(
            "Nippon India Vision Large & Mid Cap Fund - Growth",
            {"999999": "Nippon India Vision Large & Midcap Fund - IDCW"},
        )

        assert result.status == NO_MATCH

    def test_a_wholly_different_fund_is_rejected_even_with_one_candidate(self):
        """118TFSS: one NAV candidate, but Edelweiss is not JPMorgan on name
        alone. A unique NAV hit lowers the bar; it does not remove it."""
        result = match_nav_anchored(
            "Edelweiss Low Duration Fund - Segregated Asset - Growth",
            {"135450": "JPMorgan India Treasury Fund - Segregated Asset - Growth Option"},
        )

        assert result.status == NO_MATCH

    def test_the_bar_is_stricter_when_several_candidates_remain(self):
        """With one candidate the name corroborates; with many it decides.

        This pair scores ~88 -- accepted at the lenient bar, rejected at the
        strict one. Two candidates means the name is doing the discriminating.
        """
        candidates = {
            "115890": "HDFC FMP 24M September 2011 (1) - Growth Option",
            "888888": "Some Other Entirely Different Fund - Regular Plan - IDCW",
        }
        result = match_nav_anchored(
            "HDFC FMP 24M September 2011 (1) - Growth - Series XIX", candidates
        )

        assert result.status == NO_MATCH

    def test_two_candidates_passing_the_bar_are_ambiguous(self):
        result = match_nav_anchored(
            "Axis Flexi Cap Fund - Regular Plan - Growth",
            {
                "1": "Axis Flexicap Fund - Regular Plan - Growth",
                "2": "Axis Flexi Cap Fund Regular Growth Option",
            },
        )

        assert result.status == AMBIGUOUS
        assert result.amfi_scheme_code is None

    def test_an_empty_candidate_set_yields_no_match(self):
        assert match_nav_anchored("Anything At All - Growth", {}).status == NO_MATCH


class TestRomanNumeralsInTheFuzzyTier:
    """Roman/arabic series equivalence, applied HERE and not in scheme_key.

    Converting romans while building core names broke rules.numbers_conflict,
    which deliberately keeps roman series markers in a namespace of their own:
    "XXII - Series 11" and "XXII - Series 22" both reduced to a set containing
    22, the subset test stopped seeing a contradiction, and CORE_FUZZY mapped
    Series 11 onto Series 22. Confined to this rule the same normalisation is
    safe, because it only ever compares one scheme against its own NAV
    candidates.
    """

    def test_an_arabic_series_matches_its_roman_counterpart(self):
        """L455G."""
        result = match_nav_anchored(
            "SBI Equity Opportunities Fund-Series - 2 - Regular Plan - Growth",
            {"133106": "SBI EQUITY OPPORTUNITIES FUND - SERIES II - REGULAR PLAN - GROWTH"},
        )

        assert result.status == RESOLVED
        assert result.amfi_scheme_code == "133106"

    def test_a_different_roman_series_is_still_rejected(self):
        result = match_nav_anchored(
            "SBI Equity Opportunities Fund-Series - 2 - Regular Plan - Growth",
            {"131336": "SBI EQUITY OPPORTUNITIES FUND - SERIES I - REGULAR PLAN - GROWTH"},
        )

        assert result.status == NO_MATCH

    def test_the_reliance_series_group_case_stays_rejected(self):
        """The pair that broke when this ran inside scheme_key. Series 11 and
        Series 22 of the same XXII group must never match."""
        result = match_nav_anchored(
            "NIPPON INDIA FIXED HORIZON FUND - XXII - SERIES 11 - GROWTH PLAN",
            {"117794": "Reliance Fixed Horizon Fund - XXII - Series 22 - Growth Option"},
        )

        assert result.status == NO_MATCH

    def test_a_two_letter_series_label_is_not_read_as_a_numeral(self):
        result = match_nav_anchored(
            "Aditya Birla Sun Life Fixed Term Plan - Series LI - Growth",
            {"999999": "Aditya Birla Sun Life Fixed Term Plan - Series 51 - Growth"},
        )

        assert result.status == NO_MATCH
