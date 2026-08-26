"""Mirrors test_etl_trans_row_hash_flag.py for etl_investor_master.py."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from utils.db import engine
from utils.dedupe_hash import hash_normalized_rows
from etl_investor_master import process_investor_master
import backfill_bronze_row_hash

# process_investor_master(cams=...) hardcodes source="CAMS" internally (not
# caller-controllable) -- these tests scope cleanup/assertions by a random
# folio_no per test instead of a sentinel source value.


@pytest.fixture
def folio():
    f = uuid.uuid4().hex[:10]
    yield f
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM bronze.investor_master WHERE folio_no = :f"
        ), {"f": f})


def _cams_row(folio, product_code, investor_name):
    # Raw CAMS R9 headers: FOLIO_NO/PRODUCT_CODE/INV_NAME (all valid source
    # aliases in INVESTOR_MASTER_MAPPING for folio_no/product_code/investor_name).
    return {"FOLIO_NO": folio, "PRODUCT_CODE": product_code, "INV_NAME": investor_name}


def test_exact_duplicate_of_an_existing_row_is_flagged_1(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)  # first insert: flag=0

    cams_again = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams_again)  # exact resend: flag=1

    result = pd.read_sql(
        "SELECT flag FROM bronze.investor_master WHERE folio_no = %(f)s ORDER BY created_at",
        engine, params={"f": folio},
    )
    assert result["flag"].tolist() == [0, 1]


def test_same_folio_and_product_but_changed_investor_name_is_flagged_0_and_both_rows_survive(folio):
    """Investor attributes legitimately change (address, name spelling
    corrections, etc.) -- must still reach flag=0 and get APPENDED, never
    updated in place: both the original and the changed row must survive,
    per the confirmed decision that bronze never updates investor data."""
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)

    cams_updated = pd.DataFrame([_cams_row(folio, "P1", "Jane A. Doe")])
    process_investor_master(cams=cams_updated)

    result = pd.read_sql(
        "SELECT flag, investor_name FROM bronze.investor_master "
        "WHERE folio_no = %(f)s ORDER BY investor_name",
        engine, params={"f": folio},
    )
    assert result["investor_name"].tolist() == ["Jane A. Doe", "Jane Doe"]
    assert result.loc[result["investor_name"] == "Jane Doe", "flag"].iloc[0] == 0
    assert result.loc[result["investor_name"] == "Jane A. Doe", "flag"].iloc[0] == 0


def test_brand_new_row_is_flagged_0_and_gets_a_row_hash(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "New Investor")])
    process_investor_master(cams=cams)

    result = pd.read_sql(
        "SELECT flag, row_hash FROM bronze.investor_master WHERE folio_no = %(f)s",
        engine, params={"f": folio},
    )
    assert (result["flag"] == 0).all()
    assert result["row_hash"].notna().all()


def test_loader_hash_matches_what_backfill_would_compute_for_the_same_row(folio):
    """Regression guard for a real bug found in final review: the loader's
    compare_cols derivation must match backfill_bronze_row_hash.py's
    derivation exactly (same columns, same order), or a pre-existing row's
    stored row_hash becomes permanently unreachable by the loader that's
    supposed to match against it on a resend."""
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)

    stored = pd.read_sql(
        "SELECT * FROM bronze.investor_master WHERE folio_no = %(f)s",
        engine, params={"f": folio},
    )
    compare_cols = backfill_bronze_row_hash._compare_cols_for("investor_master")
    recomputed = hash_normalized_rows(
        backfill_bronze_row_hash._normalize_investor(stored, compare_cols),
        compare_cols,
    )
    assert (stored["row_hash"] == recomputed).all()


def test_created_at_is_never_rewritten_on_a_later_run(folio):
    cams = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams)
    first = pd.read_sql(
        "SELECT created_at FROM bronze.investor_master WHERE folio_no = %(f)s",
        engine, params={"f": folio},
    )["created_at"].iloc[0]

    # Re-process the exact same row (an upstream resend) -- bronze must
    # append a second row (append-only, never UPDATE), leaving the first
    # row's created_at untouched.
    cams_again = pd.DataFrame([_cams_row(folio, "P1", "Jane Doe")])
    process_investor_master(cams=cams_again)

    unchanged = pd.read_sql(
        "SELECT created_at FROM bronze.investor_master "
        "WHERE folio_no = %(f)s ORDER BY created_at ASC LIMIT 1",
        engine, params={"f": folio},
    )["created_at"].iloc[0]
    assert unchanged == first
