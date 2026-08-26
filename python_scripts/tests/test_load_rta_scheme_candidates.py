"""load_rta_scheme_candidates(): the distinct (source, prodcode) scheme list
scheme_mapping.py builds its RTA candidates from -- now also carrying
rta_isin, picked from bronze.transaction_master_new.isin. A given RTA
scheme code can have many transaction rows; when only some of them carry an
ISIN, the ISIN-bearing one must win the DISTINCT ON pick (NULLS LAST), or
RULE 0 (ISIN_MATCH) would starve on an arbitrarily-chosen null row even
though the data exists."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from scheme_mapping import load_rta_scheme_candidates


def _insert(source, prodcode, scheme, isin=None):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO bronze.transaction_master_new
                (source, amc_code, prodcode, scheme, trxnno, isin, row_hash)
            VALUES (:source, 'X', :prodcode, :scheme, :trxnno, :isin, :row_hash)
        """), {
            "source": source, "prodcode": prodcode, "scheme": scheme,
            "trxnno": uuid.uuid4().hex[:10], "isin": isin,
            "row_hash": uuid.uuid4().hex,
        })


@pytest.fixture
def prodcode():
    code = f"__TEST_ISIN_CAND_{uuid.uuid4().hex[:8]}__"
    yield code
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.transaction_master_new WHERE prodcode = :p"
        ), {"p": code})


def test_isin_bearing_row_wins_over_a_null_row_for_the_same_code(prodcode):
    _insert("KFIN", prodcode, "Test Scheme", isin=None)
    _insert("KFIN", prodcode, "Test Scheme", isin="inf123a01234")

    df = load_rta_scheme_candidates()

    row = df[(df["rta"] == "KFIN") & (df["rta_scheme_code"] == prodcode)]
    assert len(row) == 1
    assert row["rta_isin"].iloc[0] == "INF123A01234"


def test_isin_is_normalized_stripped_and_uppercased(prodcode):
    _insert("KFIN", prodcode, "Test Scheme", isin="  inf999z01234  ")

    df = load_rta_scheme_candidates()

    row = df[(df["rta"] == "KFIN") & (df["rta_scheme_code"] == prodcode)]
    assert row["rta_isin"].iloc[0] == "INF999Z01234"


def test_a_code_with_no_isin_anywhere_yields_a_null_rta_isin(prodcode):
    _insert("KFIN", prodcode, "Test Scheme", isin=None)

    df = load_rta_scheme_candidates()

    row = df[(df["rta"] == "KFIN") & (df["rta_scheme_code"] == prodcode)]
    assert len(row) == 1
    assert pd.isna(row["rta_isin"].iloc[0])
