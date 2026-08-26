"""bronze.transaction_master_new.isin: mapped from a source file's `isin`
column when present (KFIN today), left NULL when the source doesn't carry
one (CAMS today) -- and, because it's part of the full-row row_hash, a
resend of an already-loaded transaction that newly carries an ISIN must NOT
be flagged as a duplicate (or the ISIN would silently never reach silver)."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_trans import process_transactions


@pytest.fixture
def trxnno():
    t = uuid.uuid4().hex[:10]
    yield t
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.transaction_master_new WHERE trxnno = :t"
        ), {"t": t})


def _kfin_row(trxnno, folio_no, amount, units, isin=None):
    row = {
        "TRXNNO": trxnno, "FOLIO_NO": folio_no, "AMOUNT": amount, "UNITS": units,
        "PRODCODE": "P1", "SCHEME": "S1",
    }
    if isin is not None:
        row["ISIN"] = isin
    return row


def _cams_row(trxnno, folio_no, amount, units):
    return {
        "TRXNNO": trxnno, "FOLIO_NO": folio_no, "AMOUNT": amount, "UNITS": units,
        "PRODCODE": "P1", "SCHEME": "S1",
    }


def test_isin_is_mapped_from_kfin_source(trxnno):
    kfin = pd.DataFrame([_kfin_row(trxnno, "F1", "100.00", "10.000", isin="INF123A01234")])
    process_transactions(kfin=kfin)

    result = pd.read_sql(
        "SELECT isin FROM bronze.transaction_master_new WHERE trxnno = %(t)s",
        engine, params={"t": trxnno},
    )
    assert result["isin"].tolist() == ["INF123A01234"]


def test_isin_stays_null_when_source_has_no_isin_column(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000")])
    process_transactions(cams=cams)

    result = pd.read_sql(
        "SELECT isin FROM bronze.transaction_master_new WHERE trxnno = %(t)s",
        engine, params={"t": trxnno},
    )
    assert result["isin"].isna().all()


def test_resend_with_isin_now_populated_is_not_flagged_as_duplicate(trxnno):
    """The exact scenario this column exists for: a transaction is first
    loaded without an ISIN, then a later file resends it now carrying one.
    Because isin is part of the full-row row_hash, that resend must be
    treated as a new row (flag=0) so it reaches silver and the ISIN is
    actually captured -- not silently dropped as an "already seen" dup."""
    kfin = pd.DataFrame([_kfin_row(trxnno, "F1", "100.00", "10.000")])
    process_transactions(kfin=kfin)

    kfin_with_isin = pd.DataFrame([_kfin_row(trxnno, "F1", "100.00", "10.000", isin="INF123A01234")])
    process_transactions(kfin=kfin_with_isin)

    result = pd.read_sql(
        "SELECT flag, isin FROM bronze.transaction_master_new "
        "WHERE trxnno = %(t)s ORDER BY created_at",
        engine, params={"t": trxnno},
    )
    assert result["flag"].tolist() == [0, 0]
    assert result["isin"].isna().tolist() == [True, False]
    assert result["isin"].iloc[1] == "INF123A01234"
