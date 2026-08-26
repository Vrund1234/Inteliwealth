"""Mirrors test_etl_trans_row_hash_flag.py for etl_sip.py. process_sip()
hardcodes source="CAMS"/"KFIN" internally (not sentinel-controllable), so
these tests scope cleanup/assertions by a random folio_no per test instead
of a sentinel source value."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from etl_sip import process_sip


@pytest.fixture
def folio():
    f = uuid.uuid4().hex[:10]
    yield f
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.sip_master_new WHERE folio_no = :f"
        ), {"f": f})


def _cams_row(folio, scheme_code, reg_date, amount, ft_sip_regno):
    return {
        "FOLIO_NO": folio, "SCHEME_CODE": scheme_code, "REG_DATE": reg_date,
        "AUTO_AMOUNT": amount, "FT_SIP_REGNO": ft_sip_regno,
    }


def test_exact_duplicate_of_an_existing_row_is_flagged_1(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    cams_again = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams_again, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag FROM bronze.sip_master_new WHERE folio_no = %(f)s ORDER BY created_at",
        engine, params={"f": folio},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_registration_but_different_amount_top_up_is_flagged_0(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    cams_topup = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1500", "REG1")])
    process_sip(cams=cams_topup, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag FROM bronze.sip_master_new WHERE folio_no = %(f)s AND auto_amount = '1500'",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.sip_master_new WHERE folio_no = %(f)s",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_created_at_is_never_rewritten_on_a_later_run(folio):
    cams = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams, cams_source="CAMS")
    first = pd.read_sql(
        "SELECT created_at FROM bronze.sip_master_new WHERE folio_no = %(f)s",
        engine, params={"f": folio},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(folio, "S1", "17-08-2020", "1000", "REG1")])
    process_sip(cams=cams_again, cams_source="CAMS")

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.sip_master_new WHERE folio_no = %(f)s "
        "ORDER BY created_at ASC LIMIT 1",
        engine, params={"f": folio},
    )["created_at"].iloc[0]
    assert unchanged == first
