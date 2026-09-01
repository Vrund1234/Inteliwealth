"""A reviewer-approved mapping must survive the next scheme_mapping.py run.

The upsert overwrote amfi_scheme_code unconditionally. That is right for rows
the engine owns -- a scheme unmatched last run should pick up a code a later
rule now finds. It is wrong for a mapping a human approved out of the review
queue: those schemes are in the queue precisely BECAUSE the engine cannot
resolve them, so the engine contributes NULL for them on every subsequent run
and would erase the approval, with no error and nothing in the log.

The rule encoded here: the engine's answer always wins when it has one, and
only yields to a verified mapping when it has none.
"""

import uuid

import pytest
from sqlalchemy import text

from scheme_mapping import UPSERT_MAPPING_SQL
from utils.db import engine

SENTINEL_RTA = "__TEST_UPSERT__"


def _row(code, **overrides):
    params = {
        "mapping_id": str(uuid.uuid4()),
        "scheme_id": None,
        "rta": SENTINEL_RTA,
        "rta_amc_code": "B",
        "rta_scheme_code": code,
        "rta_scheme_name": "test scheme",
        "normalized_scheme_name": "TEST SCHEME",
        "amfi_scheme_code": None,
        "mapping_source": None,
        "mapping_confidence": None,
        "mapping_status": "UNMATCHED",
        "rta_isin": None,
    }
    params.update(overrides)
    return params


def _upsert(params):
    with engine.begin() as conn:
        conn.execute(UPSERT_MAPPING_SQL, [params])


def _read(code):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT amfi_scheme_code, mapping_source, mapping_status, "
                 "mapping_confidence FROM bronze.scheme_mapping "
                 "WHERE rta = :rta AND rta_scheme_code = :code"),
            {"rta": SENTINEL_RTA, "code": code},
        ).first()
    return dict(row._mapping) if row else None


def _mark_verified(code):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE bronze.scheme_mapping SET verified_by = 'tester', "
                 "verified_at = now() WHERE rta = :rta AND rta_scheme_code = :code"),
            {"rta": SENTINEL_RTA, "code": code},
        )


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bronze.scheme_mapping WHERE rta = :rta"),
                     {"rta": SENTINEL_RTA})


def test_a_verified_mapping_survives_a_run_that_resolves_nothing():
    code = f"V{uuid.uuid4().hex[:8]}"
    _upsert(_row(code))
    _upsert(_row(code, amfi_scheme_code="123456", mapping_source="NAV_NAME_MATCH",
                 mapping_confidence=96, mapping_status="MATCHED",
                 scheme_id="B123456"))
    _mark_verified(code)

    # The engine runs again and still cannot resolve this scheme.
    _upsert(_row(code))

    row = _read(code)
    assert row["amfi_scheme_code"] == "123456"
    assert row["mapping_status"] == "MATCHED"
    assert row["mapping_source"] == "NAV_NAME_MATCH"


def test_an_unverified_mapping_is_no_longer_overwritten():
    """CONTRACT CHANGED 2026-08-31: an already-mapped scheme is frozen.

    This test previously asserted the opposite -- that a row the engine owns
    (verified_at IS NULL) could be blanked by a later run. That is exactly how
    CAMS/K205G and CAMS/TRFPG lost the mappings recorded in
    tests/baseline_mappings.csv: a run that simply failed to resolve them wrote
    NULL over a good code. Being mapped is now protection in itself, whether or
    not a curator verified it.
    """
    code = f"U{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, amfi_scheme_code="123456", mapping_source="CORE_FUZZY",
                 mapping_confidence=90, mapping_status="MATCHED"))

    _upsert(_row(code))

    assert _read(code)["amfi_scheme_code"] == "123456"


def test_the_engine_no_longer_wins_over_an_existing_mapping():
    """CONTRACT CHANGED 2026-08-31: a mapped scheme is frozen even against a
    strictly more confident rule.

    This previously asserted that STRUCT_EXACT (98) could re-point a verified
    NAV_NAME_MATCH (96). Confidence no longer buys the right to change a
    scheme that is already mapped -- CAMS/TRFPG had two AMFI candidates tied
    at 100, so "more confident" was not a safe tiebreak.

    To re-point a mapped scheme, clear amfi_scheme_code first; that makes it a
    deliberate act rather than a side effect of a run.
    """
    code = f"W{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, amfi_scheme_code="123456", mapping_source="NAV_NAME_MATCH",
                 mapping_confidence=96, mapping_status="MATCHED"))
    _mark_verified(code)

    _upsert(_row(code, amfi_scheme_code="999999", mapping_source="STRUCT_EXACT",
                 mapping_confidence=98, mapping_status="MATCHED"))

    row = _read(code)
    assert row["amfi_scheme_code"] == "123456"
    assert row["mapping_source"] == "NAV_NAME_MATCH"


def test_a_weaker_engine_result_cannot_overwrite_a_verified_mapping():
    """The guard originally yielded to ANY engine result, on the assumption
    that every rule producing one outranks the fallbacks. CORE_FUZZY does not:
    it sits at 90, below NAV_NAME_MATCH's 96.

    Observed live. RMF7GGP was approved to 117534 (Series 11) and a later run
    re-pointed it at 117794 (Series 22) -- a different fund -- because
    CORE_FUZZY returned something and the guard only asked whether the engine
    had an answer, not whether it was a better one.
    """
    code = f"C{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, amfi_scheme_code="117534", mapping_source="NAV_NAME_MATCH",
                 mapping_confidence=96, mapping_status="MATCHED"))
    _mark_verified(code)

    _upsert(_row(code, amfi_scheme_code="117794", mapping_source="CORE_FUZZY",
                 mapping_confidence=90, mapping_status="MATCHED"))

    row = _read(code)
    assert row["amfi_scheme_code"] == "117534"
    assert row["mapping_source"] == "NAV_NAME_MATCH"


def test_an_equally_confident_engine_result_does_not_displace_a_reviewer():
    """A tie goes to the human, who looked at the scheme."""
    code = f"E{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, amfi_scheme_code="111111", mapping_source="NAV_NAME_MATCH",
                 mapping_confidence=96, mapping_status="MATCHED"))
    _mark_verified(code)

    _upsert(_row(code, amfi_scheme_code="222222", mapping_source="SOMETHING",
                 mapping_confidence=96, mapping_status="MATCHED"))

    assert _read(code)["amfi_scheme_code"] == "111111"
