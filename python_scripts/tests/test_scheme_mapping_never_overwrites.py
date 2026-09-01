"""An already-mapped or approved scheme must never be changed by a later run.

Live loss this guards against: CAMS/K205G and CAMS/TRFPG were mapped in
tests/baseline_mappings.csv, then a later run that failed to resolve them
wrote NULL straight over the stored code -- the old upsert only protected a row
when verified_at IS NOT NULL, so every automatically-mapped scheme was exposed.
CAMS/TRFPG additionally shows the change case: two AMFI candidates now tie at
score 100, so the matcher can re-point it to the other one."""

import uuid

import pytest
from sqlalchemy import text

from scheme_mapping import UPSERT_MAPPING_SQL
from utils.db import engine

RTA = "CAMS"


@pytest.fixture
def code():
    value = "ZZ" + uuid.uuid4().hex[:8].upper()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.scheme_mapping WHERE rta=:r AND rta_scheme_code=:c"
        ), {"r": RTA, "c": value})


def _upsert(code, amfi, confidence, source="TEST_RULE", status="MATCHED"):
    with engine.begin() as conn:
        conn.execute(UPSERT_MAPPING_SQL, {
            "mapping_id": str(uuid.uuid4()), "scheme_id": None,
            "rta": RTA, "rta_amc_code": "A", "rta_scheme_code": code,
            "rta_scheme_name": "Test Scheme", "normalized_scheme_name": "test scheme",
            "amfi_scheme_code": amfi, "mapping_source": source,
            "mapping_confidence": confidence, "mapping_status": status,
            "rta_isin": None,
        })


def _stored(code):
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT amfi_scheme_code, mapping_confidence, mapping_source, mapping_status "
            "FROM bronze.scheme_mapping WHERE rta=:r AND rta_scheme_code=:c"
        ), {"r": RTA, "c": code}).fetchone()
    return dict(row._mapping) if row else None


def test_a_null_result_never_blanks_an_existing_mapping(code):
    _upsert(code, "133805", 96)

    _upsert(code, None, None, source=None, status="UNMATCHED")

    assert _stored(code)["amfi_scheme_code"] == "133805"


def test_a_different_code_never_replaces_an_existing_mapping(code):
    # TRFPG's live failure: a second candidate tying at 100 re-pointed it.
    _upsert(code, "115942", 100)

    _upsert(code, "149203", 100)

    assert _stored(code)["amfi_scheme_code"] == "115942"


def test_even_a_more_confident_rule_does_not_change_a_mapped_scheme(code):
    _upsert(code, "133805", 50)

    _upsert(code, "999999", 100)

    assert _stored(code)["amfi_scheme_code"] == "133805"


def test_the_whole_mapping_record_is_frozen_not_just_the_code(code):
    _upsert(code, "133805", 96, source="NAV_NAME_MATCH", status="MATCHED")

    _upsert(code, None, 10, source="CORE_FUZZY", status="UNMATCHED")

    stored = _stored(code)
    assert stored["amfi_scheme_code"] == "133805"
    assert stored["mapping_confidence"] == 96
    assert stored["mapping_source"] == "NAV_NAME_MATCH"
    assert stored["mapping_status"] == "MATCHED"


def test_an_unmapped_scheme_still_accepts_a_new_match(code):
    # The freeze must not stop a NULL row from ever being resolved.
    _upsert(code, None, None, source=None, status="UNMATCHED")

    _upsert(code, "133805", 96)

    assert _stored(code)["amfi_scheme_code"] == "133805"


def test_descriptive_columns_still_refresh_on_a_mapped_scheme(code):
    # Freezing the MAPPING must not freeze the scheme's name/ISIN, which are
    # facts from the RTA feed rather than decisions.
    _upsert(code, "133805", 96)
    with engine.begin() as conn:
        conn.execute(UPSERT_MAPPING_SQL, {
            "mapping_id": str(uuid.uuid4()), "scheme_id": None,
            "rta": RTA, "rta_amc_code": "B", "rta_scheme_code": code,
            "rta_scheme_name": "Renamed Scheme",
            "normalized_scheme_name": "renamed scheme",
            "amfi_scheme_code": None, "mapping_source": None,
            "mapping_confidence": None, "mapping_status": "UNMATCHED",
            "rta_isin": "INF000000001",
        })

    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT rta_scheme_name, rta_isin, amfi_scheme_code "
            "FROM bronze.scheme_mapping WHERE rta=:r AND rta_scheme_code=:c"
        ), {"r": RTA, "c": code}).fetchone()

    assert row.rta_scheme_name == "Renamed Scheme"
    assert row.rta_isin == "INF000000001"
    assert row.amfi_scheme_code == "133805"
