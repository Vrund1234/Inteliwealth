"""bronze.scheme_mapping.rta_isin must actually be written by
UPSERT_MAPPING_SQL. Before this change the column existed on the table and
scheme_mapping.py computed df["rta_isin"] in memory (used only to feed RULE
0's inline ISIN_MATCH check), but the upsert's INSERT column list never
named rta_isin at all -- so the raw ISIN value was silently dropped on
every run, even once a real one started arriving from load_rta_scheme_candidates()."""

import uuid

import pytest
from sqlalchemy import text

from scheme_mapping import UPSERT_MAPPING_SQL
from utils.db import engine

SENTINEL_RTA = "__TEST_UPSERT_ISIN__"


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


def _read_isin(code):
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT rta_isin FROM bronze.scheme_mapping "
                 "WHERE rta = :rta AND rta_scheme_code = :code"),
            {"rta": SENTINEL_RTA, "code": code},
        ).scalar()


@pytest.fixture(autouse=True)
def cleanup():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM bronze.scheme_mapping WHERE rta = :rta"),
                     {"rta": SENTINEL_RTA})


def test_rta_isin_is_stored_on_first_insert():
    code = f"I{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, rta_isin="INF123A01234"))
    assert _read_isin(code) == "INF123A01234"


def test_rta_isin_is_refreshed_on_conflict():
    code = f"J{uuid.uuid4().hex[:8]}"
    _upsert(_row(code, rta_isin=None))
    assert _read_isin(code) is None

    _upsert(_row(code, rta_isin="INF999Z01234"))
    assert _read_isin(code) == "INF999Z01234"
