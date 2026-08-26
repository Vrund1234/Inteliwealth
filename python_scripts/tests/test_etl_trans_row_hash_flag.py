"""process_transactions() must keep computing `flag` with full-row
duplicate semantics (see the 2026-08-26 spec) but without ever reading the
whole bronze.transaction_master_new table. Uses sentinel-scoped rows in
the real table (it's hardcoded to that table name, same as every other
bronze loader in this codebase)."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from utils.dedupe_hash import hash_normalized_rows
from etl_trans import process_transactions
import backfill_bronze_row_hash

# process_transactions(cams=...) hardcodes source="CAMS" internally (not
# caller-controllable) -- these tests scope cleanup/assertions by a random
# trxnno per test instead of a sentinel source value.


@pytest.fixture
def trxnno():
    t = uuid.uuid4().hex[:10]
    yield t
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.transaction_master_new WHERE trxnno = :t"
        ), {"t": t})


def _cams_row(trxnno, folio_no, amount, units, td_ptrno=""):
    return {
        "TRXNNO": trxnno, "FOLIO_NO": folio_no, "AMOUNT": amount, "UNITS": units,
        "TD_PTRNO": td_ptrno, "PRODCODE": "P1", "SCHEME": "S1",
    }


def test_exact_duplicate_of_an_existing_row_is_flagged_1(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)  # first insert: flag=0

    cams_again = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams_again)  # exact resend: flag=1

    result = pd.read_sql(
        "SELECT flag FROM bronze.transaction_master_new WHERE trxnno = %(t)s ORDER BY created_at",
        engine, params={"t": trxnno},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_natural_key_but_different_non_key_column_is_flagged_0(trxnno):
    """The real bronze pattern (td_ptrno/rep_date changing across resends)
    must still reach flag=0, exactly as it does today -- proves this
    change preserves full-row semantics, not just the natural key."""
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)

    cams_resend = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="222")])
    process_transactions(cams=cams_resend)

    result = pd.read_sql(
        "SELECT flag FROM bronze.transaction_master_new WHERE trxnno = %(t)s AND td_ptrno = '222'",
        engine, params={"t": trxnno},
    )
    assert (result["flag"] == 0).all()


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000")])
    process_transactions(cams=cams)

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.transaction_master_new WHERE trxnno = %(t)s",
        engine, params={"t": trxnno},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_loader_hash_matches_what_backfill_would_compute_for_the_same_row(trxnno):
    """Regression guard for a real bug found in final review: the loader's
    compare_cols derivation must match backfill_bronze_row_hash.py's
    derivation exactly (same columns, same order), or a pre-existing row's
    stored row_hash becomes permanently unreachable by the loader that's
    supposed to match against it on a resend."""
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)

    stored = pd.read_sql(
        "SELECT * FROM bronze.transaction_master_new WHERE trxnno = %(t)s",
        engine, params={"t": trxnno},
    )
    compare_cols = backfill_bronze_row_hash._compare_cols_for("transaction_master_new")
    recomputed = hash_normalized_rows(
        backfill_bronze_row_hash._normalize_transaction(stored, compare_cols),
        compare_cols,
    )
    assert (stored["row_hash"] == recomputed).all()


def test_created_at_is_never_rewritten_on_a_later_run(trxnno):
    cams = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams)

    first = pd.read_sql(
        "SELECT created_at FROM bronze.transaction_master_new WHERE trxnno = %(t)s AND td_ptrno = '111'",
        engine, params={"t": trxnno},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(trxnno, "F1", "100.00", "10.000", td_ptrno="111")])
    process_transactions(cams=cams_again)

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.transaction_master_new "
        "WHERE trxnno = %(t)s AND td_ptrno = '111' ORDER BY created_at ASC LIMIT 1",
        engine, params={"t": trxnno},
    )["created_at"].iloc[0]
    assert unchanged == first
