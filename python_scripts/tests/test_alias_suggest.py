"""Deriving alias candidates from near-miss scheme pairs.

The miner is deliberately loose, so the guards carry the weight. Two of them
come from cases observed in the live residual:

  1228 -> 385   Both sides are the duration that distinguishes two real SBI
                funds. Aliasing them makes a wrong mapping look right, and no
                collision check catches it: the Growth variant of the 1228-day
                fund does not exist in the master, so nothing is collapsed --
                the RTA scheme simply lands on the wrong fund.

  BIRLA SUN LIFE FIXED TERM SERIES HC 1099 -> ING LATIN AMERICA EQUITY
                Two unrelated funds share a launch NAV. difflib reports the
                whole core as one contiguous replacement, which passes a naive
                "single edit" test, so shared context has to be required too.
"""

from scheme_matching.alias_suggest import (
    build_key_index,
    derive_term_pair,
    is_structurally_safe,
    new_collisions,
)
from scheme_matching.scheme_key import parse_scheme_key


class TestDerivingTheChangedTerm:
    def test_a_localised_edit_yields_the_differing_run(self):
        pair = derive_term_pair(
            "TATA FIXED MATURITY SERIES 39 SCHEME F",
            "TFMP SERIES 39 SCHEME F",
        )
        assert pair == ("TATA FIXED MATURITY", "TFMP")

    def test_a_one_token_spelling_variant_is_found(self):
        pair = derive_term_pair(
            "NIPPON INDIA VISION LARGE AND MID CAP",
            "NIPPON INDIA VISION LARGE AND MIDCAP",
        )
        assert pair == ("MID CAP", "MIDCAP")

    def test_identical_cores_yield_nothing(self):
        assert derive_term_pair("AXIS FIXED TERM SERIES 48",
                                "AXIS FIXED TERM SERIES 48") is None

    def test_two_separate_edits_are_not_a_single_alias(self):
        """Two changed runs describe two rules, not one. Proposing either in
        isolation would not resolve the scheme."""
        assert derive_term_pair("ALPHA ONE SERIES 3 GAMMA",
                                "BETA ONE SERIES 3 DELTA") is None

    def test_unrelated_names_sharing_no_context_are_rejected(self):
        """The whole core replaced: one contiguous run, but no evidence the two
        names are the same fund."""
        assert derive_term_pair(
            "BIRLA SUN LIFE FIXED TERM SERIES HC 1099",
            "ING LATIN AMERICA EQUITY",
        ) is None

    def test_a_single_shared_token_is_not_enough_context(self):
        assert derive_term_pair("ALPHA BETA GAMMA DELTA", "ALPHA ZULU") is None


class TestStructuralSafety:
    def test_a_number_is_never_aliased_to_another_number(self):
        """The 1228 -> 385 case. Numbers carry series and duration, which are
        exactly what separates sibling schemes."""
        assert is_structurally_safe("1228", "385") is False

    def test_a_multi_token_run_of_pure_numbers_is_also_rejected(self):
        assert is_structurally_safe("30 385", "30 1228") is False

    def test_a_word_renamed_to_a_word_is_allowed(self):
        assert is_structurally_safe("TATA FIXED MATURITY", "TFMP") is True

    def test_a_number_paired_with_a_word_is_allowed(self):
        """Roman-numeral normalisation is legitimate; the collision report and
        a human decide whether it is wanted."""
        assert is_structurally_safe("2", "II") is True

    def test_an_empty_side_is_rejected(self):
        assert is_structurally_safe("", "TFMP") is False
        assert is_structurally_safe("TFMP", "") is False


class TestCollisionDetection:
    """An alias must not collapse two schemes that are distinct today."""

    NAMES = {
        "1": "SBI Debt Fund Series C 30 (385 Days) Regular Plan Growth",
        "2": "SBI Debt Fund Series C 30 (1228 Days) Regular Plan Growth",
        "3": "Axis Flexi Cap Fund Regular Plan Growth",
    }

    def _index(self):
        return build_key_index(self.NAMES, alias_fn=None)

    def test_an_alias_merging_two_distinct_schemes_is_reported(self):
        def merge_durations(text, amc_code=None):
            return text.replace("1228", "385")

        found = new_collisions(
            self.NAMES, self._index(), merge_durations, raw_term="1228"
        )

        assert found, "merging 1228 into 385 collapses two distinct SBI funds"
        collided = {c for _, codes in found for c in codes}
        assert collided == {"1", "2"}

    def test_an_alias_touching_nothing_reports_no_collision(self):
        def noop(text, amc_code=None):
            return text

        assert new_collisions(self.NAMES, self._index(), noop, raw_term="ZZZ") == []

    def test_a_scheme_is_not_reported_as_colliding_with_itself(self):
        """Rewriting a term that appears in only one name changes its key but
        collides with nothing."""
        def rename_axis(text, amc_code=None):
            return text.replace("Flexi Cap", "Flexicap")

        assert new_collisions(
            self.NAMES, self._index(), rename_axis, raw_term="Flexi Cap"
        ) == []


class TestKeyIndex:
    def test_schemes_sharing_a_key_are_grouped(self):
        names = {
            "1": "Axis Flexi Cap Fund - Regular Plan - Growth",
            "2": "Axis Flexi Cap Fund Regular Growth Option",
            "3": "Axis Flexi Cap Fund - Regular Plan - IDCW",
        }

        index = build_key_index(names, alias_fn=None)

        growth_key = parse_scheme_key(names["1"], alias_fn=None)
        assert index[growth_key] == {"1", "2"}
