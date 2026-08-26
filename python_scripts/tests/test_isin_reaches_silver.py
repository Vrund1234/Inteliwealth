"""silver.transaction_master_new.isin: verifies the claim that no code
change is needed in transformations/transform.py for isin to reach silver
-- append_new_rows() writes whatever columns exist on the silver table
(via get_table_columns()), and load_silver()'s own extraction is a bare
`SELECT * FROM bronze.transaction_master_new`, so a column that exists on
both tables should already pass straight through untouched."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from transformations.transform import append_new_rows

SENTINEL = "__TEST_ISIN_SILVER__"


@pytest.fixture(autouse=True)
def cleanup():
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM silver.transaction_master_new WHERE source = :s"
        ), {"s": SENTINEL})
    yield
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM silver.transaction_master_new WHERE source = :s"
        ), {"s": SENTINEL})


def test_isin_passes_through_bronze_to_silver_untouched():
    trxnno = uuid.uuid4().hex[:10]
    df = pd.DataFrame([{
        "source": SENTINEL, "flag": 0, "trxnno": trxnno,
        "folio_no": "F1", "amount": 100, "units": 10,
        "isin": "INF123A01234",
    }])

    append_new_rows(df, "transaction_master_new")

    with engine.begin() as conn:
        isin = conn.execute(text(
            "SELECT isin FROM silver.transaction_master_new "
            "WHERE source = :s AND trxnno = :t"
        ), {"s": SENTINEL, "t": trxnno}).scalar()

    assert isin == "INF123A01234"


def test_missing_isin_stays_null_in_silver():
    trxnno = uuid.uuid4().hex[:10]
    df = pd.DataFrame([{
        "source": SENTINEL, "flag": 0, "trxnno": trxnno,
        "folio_no": "F1", "amount": 100, "units": 10,
    }])

    append_new_rows(df, "transaction_master_new")

    with engine.begin() as conn:
        isin = conn.execute(text(
            "SELECT isin FROM silver.transaction_master_new "
            "WHERE source = :s AND trxnno = :t"
        ), {"s": SENTINEL, "t": trxnno}).scalar()

    assert isin is None
