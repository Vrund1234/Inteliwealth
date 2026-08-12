import pandas as pd
import pytest

from scheme_matching.nav_verify import (
    RTAS_WITH_NAV,
    classify,
    classify_collision,
    load_amfi_navs,
    verify,
)
from utils.db import master_engine

AMFI_NAVS = pd.DataFrame(
    [
        {"scheme_code": "100669", "nav_date": "2026-07-09", "nav_round": 100.1234},
        {"scheme_code": "100669", "nav_date": "2026-07-08", "nav_round": 99.5000},
        {"scheme_code": "100669", "nav_date": "2026-07-07", "nav_round": 99.1111},
        {"scheme_code": "999999", "nav_date": "2026-07-09", "nav_round": 55.0000},
    ]
)


class TestVerify:
    def test_passes_when_every_nav_agrees(self):
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 99.5000),
            ("2026-07-07", 99.1111),
        ]
        assert verify(fingerprint, "100669", AMFI_NAVS) is True

    def test_fails_when_one_nav_disagrees(self):
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 88.8888),
            ("2026-07-07", 99.1111),
        ]
        assert verify(fingerprint, "100669", AMFI_NAVS) is False

    def test_fails_when_the_amfi_code_has_no_navs(self):
        fingerprint = [("2026-07-09", 100.1234)]
        assert verify(fingerprint, "123456", AMFI_NAVS) is False

    def test_fails_on_an_empty_fingerprint(self):
        """No evidence is not the same as verified."""
        assert verify([], "100669", AMFI_NAVS) is False

    def test_fails_when_a_date_is_missing_from_amfi(self):
        fingerprint = [("2026-01-01", 100.1234)]
        assert verify(fingerprint, "100669", AMFI_NAVS) is False


class TestClassify:
    def test_verified_when_every_date_matches(self):
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 99.5000),
            ("2026-07-07", 99.1111),
        ]
        outcome = classify(fingerprint, "100669", AMFI_NAVS)
        assert outcome == {
            "matched": 3,
            "missing": 0,
            "differed": 0,
            "verdict": "VERIFIED",
            "max_pct_diff": 0.0,
        }

    def test_no_evidence_on_an_empty_fingerprint(self):
        """No RTA-side fingerprint at all is not a contradiction."""
        outcome = classify([], "100669", AMFI_NAVS)
        assert outcome["verdict"] == "NO_EVIDENCE"
        assert outcome["matched"] == 0
        assert outcome["differed"] == 0

    def test_no_evidence_when_amfi_code_has_no_navs(self):
        """AMFI publishing nothing for the code is absence of evidence, not disagreement."""
        fingerprint = [("2026-07-09", 100.1234)]
        outcome = classify(fingerprint, "123456", AMFI_NAVS)
        assert outcome["verdict"] == "NO_EVIDENCE"
        assert outcome["matched"] == 0
        assert outcome["differed"] == 0
        assert outcome["missing"] == 1

    def test_no_evidence_when_every_date_is_missing_from_amfi(self):
        fingerprint = [("2020-01-01", 100.1234), ("2020-01-02", 99.0)]
        outcome = classify(fingerprint, "100669", AMFI_NAVS)
        assert outcome["verdict"] == "NO_EVIDENCE"
        assert outcome["missing"] == 2

    def test_weak_when_one_date_differs_but_others_match(self):
        """A single stale RTA row alongside otherwise-exact dates is not a mapping error."""
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 88.8888),  # stale/off
            ("2026-07-07", 99.1111),
        ]
        outcome = classify(fingerprint, "100669", AMFI_NAVS)
        assert outcome["matched"] == 2
        assert outcome["differed"] == 1
        assert outcome["verdict"] == "WEAK"
        assert outcome["max_pct_diff"] > 0

    def test_contradicted_when_no_date_matches_but_some_differ(self):
        """Every AMFI-comparable date disagrees — the real red flag."""
        fingerprint = [
            ("2026-07-09", 91.0),
            ("2026-07-08", 90.0),
            ("2026-07-07", 89.0),
        ]
        outcome = classify(fingerprint, "100669", AMFI_NAVS)
        assert outcome["matched"] == 0
        assert outcome["differed"] == 3
        assert outcome["verdict"] == "CONTRADICTED"
        assert outcome["max_pct_diff"] > 0

    def test_max_pct_diff_reflects_the_worst_differed_date(self):
        """max_pct_diff is measured relative to the RTA (fingerprint) value —
        matching how the D151 investigation reported its ~8.33% gap:
        abs(1430.2759 - 1311.187) / 1430.2759 * 100.
        """
        fingerprint = [
            ("2026-07-09", 100.1234),  # matches exactly
            ("2026-07-08", 50.0000),  # amfi publishes 99.5 for this date -> 99% off
        ]
        outcome = classify(fingerprint, "100669", AMFI_NAVS)
        assert outcome["verdict"] == "WEAK"
        assert outcome["max_pct_diff"] == pytest.approx(99.0)


class TestCoverage:
    def test_only_cams_has_nav_data(self):
        """gold.scheme_nav holds 68,424 rows across 332 codes, all CAMS.

        KFIN has zero. Any verification path must not assume KFIN NAVs exist.
        """
        assert RTAS_WITH_NAV == {"CAMS"}


class TestLoadAmfiNavsAgainstRealDatabase:
    """public.nav_master.nav_date is a real `date` column, not text.

    In-memory unit tests above can't catch a `date = text` operator
    mismatch — this hits the live database, the way verify() actually
    does via Task 13's NAV audit gate.
    """

    def test_returns_rows_for_real_dates_from_nav_master(self):
        dates = pd.read_sql(
            "SELECT DISTINCT nav_date FROM public.nav_master LIMIT 3",
            master_engine,
        )["nav_date"].tolist()
        if not dates:
            pytest.skip("public.nav_master has no rows to test against")

        navs = load_amfi_navs(master_engine, dates)

        assert not navs.empty
        assert set(navs.columns) == {"scheme_code", "nav_date", "nav_round"}
        assert set(navs["nav_date"]).issubset(set(dates))

    def test_returns_empty_frame_for_no_dates(self):
        navs = load_amfi_navs(master_engine, [])
        assert navs.empty
        assert list(navs.columns) == ["scheme_code", "nav_date", "nav_round"]


class TestClassifyCollision:
    """One AMFI code reached by several RTA codes is not automatically an error.

    Two share classes of one fund (lock-in and not) legitimately share a code
    and a NAV series. A genuinely wrong mapping looks different: at least one
    member's NAV series contradicts the code. NAV decides, not a hardcoded list.
    """

    def test_all_members_verified_is_a_legitimate_shared_code(self):
        assert classify_collision(["VERIFIED", "VERIFIED"]) == "SAME_FUND"

    def test_a_single_contradiction_makes_it_an_error(self):
        assert classify_collision(["VERIFIED", "CONTRADICTED"]) == "WRONG"

    def test_contradiction_outranks_everything_else(self):
        assert classify_collision(
            ["CONTRADICTED", "NO_EVIDENCE", "VERIFIED", "WEAK"]
        ) == "WRONG"

    def test_absent_evidence_is_not_a_verdict(self):
        """KFIN has no NAV at all, so this must not be reported as legitimate."""
        assert classify_collision(["NO_EVIDENCE", "NO_EVIDENCE"]) == "UNVERIFIED"

    def test_weak_evidence_is_not_enough_to_bless_a_collision(self):
        assert classify_collision(["VERIFIED", "WEAK"]) == "UNVERIFIED"

    def test_no_members_is_unverified_not_same_fund(self):
        assert classify_collision([]) == "UNVERIFIED"


class TestClassifyRespectsSuppliedPrecision:
    """AMFI publishes 4dp; the RTA feed sometimes supplies 2dp for the same
    date. Comparing at 4dp then reports a difference that is an artefact of
    the RTA's precision, not a disagreement about the price. Agreement at the
    coarser precision is real but weaker, so it gets its own verdict rather
    than being folded into VERIFIED.
    """

    NAVS = pd.DataFrame([
        {"scheme_code": "1", "nav_date": "2026-07-09", "nav_round": 10.0564},
        {"scheme_code": "1", "nav_date": "2026-07-08", "nav_round": 10.0620},
        {"scheme_code": "2", "nav_date": "2026-07-09", "nav_round": 10.0600},
        {"scheme_code": "3", "nav_date": "2026-07-09", "nav_round": 10.0668},
    ])

    def test_two_decimal_rta_value_agrees_with_four_decimal_amfi(self):
        out = classify([("2026-07-09", 10.06)], "1", self.NAVS)
        assert out["matched"] == 1
        assert out["differed"] == 0
        assert out["verdict"] == "VERIFIED_LOW_PRECISION"

    def test_full_precision_agreement_is_still_plain_verified(self):
        out = classify([("2026-07-09", 10.06)], "2", self.NAVS)
        assert out["verdict"] == "VERIFIED"

    def test_disagreement_at_the_supplied_precision_is_still_a_difference(self):
        """10.0668 rounds to 10.07, not 10.06 — a real conflict, not rounding."""
        out = classify([("2026-07-09", 10.06)], "3", self.NAVS)
        assert out["differed"] == 1
        assert out["verdict"] == "CONTRADICTED"

    def test_low_precision_agreement_does_not_bless_a_shared_code(self):
        assert classify_collision(["VERIFIED", "VERIFIED_LOW_PRECISION"]) == "UNVERIFIED"
