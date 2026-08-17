"""Tests for the single write-path that every matching rule goes through.

update_best_match() decides which rule's answer survives. It is the only place
a scheme's amfi_scheme_code is set, so its guard is what makes "confidence
decides, not execution order" true — or, when it is wrong, silently false.
"""

import pandas as pd

from scheme_mapping import update_best_match


def one_row(code=None, source=None, confidence=None):
    """A one-row frame shaped like the pipeline's, at index 0."""
    return pd.DataFrame(
        {
            "best_amfi_scheme_code": pd.Series([code], dtype=object),
            "best_mapping_source": pd.Series([source], dtype=object),
            "best_mapping_confidence": pd.Series([confidence], dtype=object),
        }
    )


class TestUpdateBestMatch:
    def test_writes_into_an_empty_row(self):
        df = one_row()
        update_best_match(df, 0, "107745", "PRODUCT_MATCH", 100)
        assert df.at[0, "best_amfi_scheme_code"] == "107745"
        assert df.at[0, "best_mapping_source"] == "PRODUCT_MATCH"
        assert df.at[0, "best_mapping_confidence"] == 100

    def test_higher_confidence_overwrites(self):
        df = one_row("111111", "NAV_MATCH", 97)
        update_best_match(df, 0, "107745", "STRUCT_EXACT", 98)
        assert df.at[0, "best_amfi_scheme_code"] == "107745"

    def test_lower_confidence_does_not_overwrite(self):
        df = one_row("107745", "PRODUCT_MATCH", 100)
        update_best_match(df, 0, "111111", "CORE_FUZZY", 90)
        assert df.at[0, "best_amfi_scheme_code"] == "107745"

    def test_equal_confidence_keeps_the_incumbent(self):
        """Registry order already broke this tie in arbitrate(); don't re-break it."""
        df = one_row("107745", "ISIN_MATCH", 100)
        update_best_match(df, 0, "111111", "PRODUCT_MATCH", 100)
        assert df.at[0, "best_amfi_scheme_code"] == "107745"
        assert df.at[0, "best_mapping_source"] == "ISIN_MATCH"

    def test_a_forced_write_beats_an_equal_confidence_incumbent(self):
        """A curator's OVERRIDE is authority, not a higher score.

        PRODUCT_MATCH and ISIN_MATCH run inline and write 100 before the engine
        proposes OVERRIDE, also at 100. Without force the strict `>` guard drops
        the override, so the one mechanism for correcting a confident-but-wrong
        automatic match silently does nothing.
        """
        df = one_row("107745", "PRODUCT_MATCH", 100)
        update_best_match(df, 0, "999999", "OVERRIDE", 100, force=True)
        assert df.at[0, "best_amfi_scheme_code"] == "999999"
        assert df.at[0, "best_mapping_source"] == "OVERRIDE"

    def test_a_null_code_is_never_written(self):
        df = one_row("107745", "PRODUCT_MATCH", 100)
        update_best_match(df, 0, None, "OVERRIDE", 100, force=True)
        assert df.at[0, "best_amfi_scheme_code"] == "107745"


from scheme_mapping import clear_best_match
from scheme_matching.rules import AUTHORITATIVE_RULES


class TestAuthoritativeRules:
    def test_override_is_authoritative(self):
        """Only a human assertion may displace an equal-confidence incumbent."""
        assert "OVERRIDE" in AUTHORITATIVE_RULES

    def test_algorithmic_rules_are_not_authoritative(self):
        for rule in ("ISIN_MATCH", "PRODUCT_MATCH", "STRUCT_EXACT", "CORE_FUZZY"):
            assert rule not in AUTHORITATIVE_RULES


class TestClearBestMatch:
    def test_clears_code_source_and_confidence(self):
        """A NOT_IN_AMFI override must not leave an earlier rule's code behind.

        Otherwise the row is written with mapping_status='NOT_IN_AMFI' AND a
        populated amfi_scheme_code — a contradiction no consumer can resolve.
        """
        df = one_row("107745", "PRODUCT_MATCH", 100)
        clear_best_match(df, 0)
        assert df.at[0, "best_amfi_scheme_code"] is None
        assert df.at[0, "best_mapping_source"] is None
        assert df.at[0, "best_mapping_confidence"] is None

    def test_is_safe_on_an_already_empty_row(self):
        df = one_row()
        clear_best_match(df, 0)
        assert df.at[0, "best_amfi_scheme_code"] is None


from scheme_mapping import dedupe_mappings


class TestDedupeMappings:
    """One RTA scheme code is one share class with one NAV, so it resolves to
    exactly one AMFI scheme. The mapping table carries one row per
    (rta, rta_scheme_code) and bronze.scheme_mapping enforces that with
    uq_scheme_mapping; anything wider silently lets an upsert pick a winner.
    """

    def frame(self, rows):
        return pd.DataFrame(
            rows, columns=["rta", "rta_scheme_code", "amfi_scheme_code"]
        )

    def test_keeps_distinct_schemes(self):
        df = self.frame([
            ("CAMS", "B02G", "107745"),
            ("CAMS", "B105G", "131666"),
            ("KFIN", "B02G", "107745"),
        ])
        assert len(dedupe_mappings(df)) == 3

    def test_collapses_a_scheme_that_resolved_to_two_amfi_codes(self):
        """A merge fan-out must not reach the upsert as two competing rows."""
        df = self.frame([
            ("CAMS", "TAFMD", "145726"),
            ("CAMS", "TAFMD", "145728"),
        ])
        out = dedupe_mappings(df)
        assert len(out) == 1
        assert out.iloc[0]["amfi_scheme_code"] == "145726"

    def test_collapses_exact_duplicates(self):
        df = self.frame([("CAMS", "B02G", "107745"), ("CAMS", "B02G", "107745")])
        assert len(dedupe_mappings(df)) == 1

    def test_keeps_one_row_for_an_unmatched_scheme(self):
        df = self.frame([("CAMS", "X1", None), ("CAMS", "X1", None)])
        assert len(dedupe_mappings(df)) == 1
