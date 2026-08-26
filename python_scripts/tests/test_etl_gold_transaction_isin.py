"""gold.transactions.isin: passed through from silver, normalized
(stripped + uppercased, blank -> null) the same way pan/arn already are in
transform_transactions(), and actually selected by extract_transactions()
from silver.transaction_master_new (an explicit column list, not SELECT *,
so a new silver column has to be added there by name to reach gold)."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_gold_transaction import extract_transactions, transform_transactions


def _silver_row(**overrides):
    row = {
        "source": "KFIN", "folio_no": "F1", "prodcode": "P1", "scheme_id": None,
        "trxntype": "PUR", "trxnno": "T1", "trxnstat": "SUCCESS", "trxnsubtyp": None,
        "traddate": "2026-08-01", "postdate": "2026-08-01", "purprice": "10.0",
        "units": "1.0", "amount": "10.0", "brokcode": None, "src_brk_code": None,
        "trxn_nature": "PURCHASE", "load": None, "pan": "ABCDE1234F", "stt": None,
        "siptrxnno": None, "euin": None, "igst_amount": None, "cgst_amount": None,
        "sgst_amount": None, "stamp_duty": None, "td_purred": None, "isin": None,
    }
    row.update(overrides)
    return row


def _transform_input(**overrides):
    return pd.DataFrame([_silver_row(**overrides)])


def test_transform_transactions_maps_isin_uppercased_and_stripped():
    df = _transform_input(isin=" inf123a01234 ")
    gold_df = transform_transactions(df)
    assert gold_df["isin"].iloc[0] == "INF123A01234"


def test_transform_transactions_blank_isin_becomes_null():
    df = _transform_input(isin="")
    gold_df = transform_transactions(df)
    assert pd.isna(gold_df["isin"].iloc[0])


def test_transform_transactions_missing_isin_stays_null():
    df = _transform_input(isin=None)
    gold_df = transform_transactions(df)
    assert pd.isna(gold_df["isin"].iloc[0])


@pytest.fixture
def silver_prodcode():
    code = f"__TEST_ISIN_{uuid.uuid4().hex[:8]}__"
    yield code
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM silver.transaction_master_new WHERE prodcode = :p"
        ), {"p": code})


def test_extract_transactions_selects_isin_from_silver(silver_prodcode):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO silver.transaction_master_new
                (source, folio_no, prodcode, scheme_id, trxntype, trxnno, trxnstat,
                 trxnsubtyp, traddate, postdate, purprice, units, amount, brokcode,
                 src_brk_code, trxn_nature, load, pan, stt, siptrxnno, euin,
                 igst_amount, cgst_amount, sgst_amount, stamp_duty, td_purred,
                 isin, flag, created_at)
            VALUES
                (:source, :folio_no, :prodcode, NULL, :trxntype, :trxnno, :trxnstat,
                 NULL, NULL, NULL, NULL, 1.0, 10.0, NULL,
                 NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                 NULL, NULL, NULL, NULL, NULL,
                 :isin, 0, now())
        """), {
            "source": "KFIN", "folio_no": "F1", "prodcode": silver_prodcode,
            "trxntype": "PUR", "trxnno": f"T-{silver_prodcode}", "trxnstat": "SUCCESS",
            "isin": "INF999Z01234",
        })

    df = extract_transactions()

    assert not df.empty
    assert "isin" in df.columns
    matched = df[df["prodcode"] == silver_prodcode]
    assert len(matched) == 1
    assert matched["isin"].iloc[0] == "INF999Z01234"
