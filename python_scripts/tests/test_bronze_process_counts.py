"""The three bronze loaders must report {total, new, duplicate} for the file
they just processed. These are the per-file numbers the etl_pipeline runner
reports back to the handoff API and writes to pipeline.etl_pipeline_log, so
they have to agree with the `flag` column actually written to bronze.

Row shapes are copied from the existing test_etl_*_row_hash_flag.py suites,
which are the proven-mappable ones for each loader."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from etl_investor_master import process_investor_master
from etl_sip import process_sip
from etl_trans import process_transactions
from utils.db import engine


def _key():
    return uuid.uuid4().hex[:10]


@pytest.fixture
def trxnno():
    value = _key()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.transaction_master_new WHERE trxnno = :t"
        ), {"t": value})


@pytest.fixture
def investor_folio():
    value = _key()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.investor_master WHERE folio_no = :f"
        ), {"f": value})


@pytest.fixture
def sip_folio():
    value = _key()
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.sip_master_new WHERE folio_no = :f"
        ), {"f": value})


def _cams_transactions(trxnno, count):
    return pd.DataFrame([
        {"TRXNNO": f"{trxnno}{index}", "FOLIO_NO": "F1", "AMOUNT": "100.00",
         "UNITS": "10.000", "TD_PTRNO": "111", "PRODCODE": "P1", "SCHEME": "S1"}
        for index in range(count)
    ])


def _cams_investors(folio, count):
    return pd.DataFrame([
        {"FOLIO_NO": folio, "PRODUCT_CODE": f"P{index}", "INV_NAME": "Jane Doe"}
        for index in range(count)
    ])


def _cams_sips(folio, count):
    return pd.DataFrame([
        {"FOLIO_NO": folio, "SCHEME_CODE": f"S{index}", "REG_DATE": "17-08-2020",
         "AUTO_AMOUNT": "1000", "FT_SIP_REGNO": f"REG{index}"}
        for index in range(count)
    ])


def _flag_counts(table, column, value):
    with engine.begin() as conn:
        rows = conn.execute(text(
            f"SELECT flag, count(*) FROM {table} WHERE {column} LIKE :v GROUP BY flag"
        ), {"v": f"{value}%"}).fetchall()
    return {int(flag): int(n) for flag, n in rows}


# ---- shape ---------------------------------------------------------------

@pytest.mark.parametrize("loader", [
    process_transactions,
    process_investor_master,
    process_sip,
])
def test_no_input_returns_an_all_zero_result(loader):
    result = loader()

    assert result == {"total": 0, "new": 0, "duplicate": 0}


# ---- transactions --------------------------------------------------------

def test_transactions_first_load_is_all_new(trxnno):
    result = process_transactions(cams=_cams_transactions(trxnno, 3))

    assert result == {"total": 3, "new": 3, "duplicate": 0}


def test_transactions_resend_is_all_duplicate(trxnno):
    process_transactions(cams=_cams_transactions(trxnno, 3))

    result = process_transactions(cams=_cams_transactions(trxnno, 3))

    assert result == {"total": 3, "new": 0, "duplicate": 3}


def test_transaction_counts_agree_with_the_flag_column(trxnno):
    process_transactions(cams=_cams_transactions(trxnno, 2))
    result = process_transactions(cams=_cams_transactions(trxnno, 5))

    counts = _flag_counts("bronze.transaction_master_new", "trxnno", trxnno)
    assert result["total"] == result["new"] + result["duplicate"]
    assert counts.get(0, 0) == 2 + result["new"]
    assert counts.get(1, 0) == result["duplicate"]


# ---- investor master -----------------------------------------------------

def test_investors_first_load_is_all_new(investor_folio):
    result = process_investor_master(cams=_cams_investors(investor_folio, 3))

    assert result == {"total": 3, "new": 3, "duplicate": 0}


def test_investors_resend_is_all_duplicate(investor_folio):
    process_investor_master(cams=_cams_investors(investor_folio, 3))

    result = process_investor_master(cams=_cams_investors(investor_folio, 3))

    assert result == {"total": 3, "new": 0, "duplicate": 3}


# ---- sip -----------------------------------------------------------------

def test_sips_first_load_is_all_new(sip_folio):
    result = process_sip(cams=_cams_sips(sip_folio, 3), cams_source="CAMS")

    assert result == {"total": 3, "new": 3, "duplicate": 0}


def test_sips_resend_is_all_duplicate(sip_folio):
    process_sip(cams=_cams_sips(sip_folio, 3), cams_source="CAMS")

    result = process_sip(cams=_cams_sips(sip_folio, 3), cams_source="CAMS")

    assert result == {"total": 3, "new": 0, "duplicate": 3}
