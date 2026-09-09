"""detect() runs unattended inside gold_loader on every pipeline pass, and it
now WRITES merges, not just a review queue. What it may write, and what a dry
run must not, is pinned here.
"""

import pytest

from detect_client_address_duplicates import detect


def _counts():
    try:
        from sqlalchemy import text

        from utils.db import engine
    except Exception:
        pytest.skip("database not configured")

    try:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text("SELECT COUNT(*) FROM gold.client_address_alias")
                ).scalar(),
                conn.execute(
                    text("SELECT COUNT(*) FROM gold.client_address_review")
                ).scalar(),
            )
    except Exception as exc:  # unreachable database is not a test failure
        if "could not connect" in str(exc).lower() or "connection" in str(exc).lower():
            pytest.skip(f"database unreachable: {exc}")
        raise


def test_a_dry_run_writes_nothing():
    """The flag exists so a change to the rule can be measured before it
    merges anything. If a dry run wrote, there would be no way to look first."""
    before = _counts()

    outcome = detect(dry_run=True)

    assert outcome is not None, "detection failed"
    assert _counts() == before


def test_every_candidate_is_either_merged_or_queued():
    """A pair that falls through both branches is a pair nobody ever sees."""
    outcome = detect(dry_run=True)

    assert outcome["auto"] + outcome["review"] == outcome["detected"]


def test_gold_loader_still_gets_the_keys_it_reports_on():
    """gold_loader reads these two by name to build its run summary."""
    outcome = detect(dry_run=True)

    assert "detected" in outcome
    assert "newly_queued" in outcome
