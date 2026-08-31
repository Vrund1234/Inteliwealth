"""append_new_rows() and load_silver() must report per-table {total, inserted,
updated} without changing what they do. The Streamlit Transform button
discards these return values (app.py:647), so the swallow-and-print behaviour
on failure is load-bearing and must survive: a silver failure must NOT raise."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import text

from transformations.transform import append_new_rows, load_silver
from utils.db import engine


@pytest.fixture
def folio():
    value = uuid.uuid4().hex[:10]
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM silver.investor_master WHERE folio_no = :f"
        ), {"f": value})


def _silver_investor_rows(folio, count):
    """Minimal frames shaped like transform_investor_master()'s output: the
    silver.investor_master natural key is (source, folio_no, product_code),
    and append_new_rows() requires a `flag` column."""
    return pd.DataFrame([
        {
            "source": "CAMS",
            "folio_no": folio,
            "product_code": f"P{index}",
            "investor_name": "TEST INVESTOR",
            "flag": 0,
        }
        for index in range(count)
    ])


def test_empty_frame_is_reported_as_skipped():
    result = append_new_rows(pd.DataFrame(), "investor_master")

    assert result["status"] == "SKIPPED"
    assert result["table"] == "investor_master"
    assert result["total"] == 0
    assert result["inserted"] == 0
    assert result["updated"] == 0
    assert result["error"] is None


def test_a_frame_without_a_flag_column_is_reported_as_skipped():
    df = pd.DataFrame([{"source": "CAMS", "folio_no": "X", "product_code": "Y"}])

    result = append_new_rows(df, "investor_master")

    assert result["status"] == "SKIPPED"
    assert "flag" in result["error"]


def test_a_frame_with_no_flag_zero_rows_is_reported_as_skipped(folio):
    df = _silver_investor_rows(folio, 2)
    df["flag"] = 1

    result = append_new_rows(df, "investor_master")

    assert result["status"] == "SKIPPED"
    assert result["inserted"] == 0


def test_new_rows_are_reported_as_inserted(folio):
    result = append_new_rows(_silver_investor_rows(folio, 3), "investor_master")

    assert result["status"] == "COMPLETED"
    assert result["total"] == 3
    assert result["inserted"] == 3
    assert result["updated"] == 0


def test_a_resend_is_reported_as_updated(folio):
    append_new_rows(_silver_investor_rows(folio, 3), "investor_master")

    result = append_new_rows(_silver_investor_rows(folio, 3), "investor_master")

    assert result["status"] == "COMPLETED"
    assert result["inserted"] == 0
    assert result["updated"] == 3


def test_an_unknown_table_is_reported_as_failed_and_does_not_raise(folio):
    # append_new_rows() raises ValueError internally for a table with no
    # conflict key defined, then catches and prints it. That swallow is
    # required by the Streamlit contract -- it must stay a swallow.
    df = _silver_investor_rows(folio, 1)

    result = append_new_rows(df, "no_such_silver_table")

    assert result["status"] in {"SKIPPED", "FAILED"}
    assert result["error"]
    assert result["inserted"] == 0
    assert result["updated"] == 0


def test_load_silver_returns_a_result_for_all_three_tables():
    results = load_silver()

    assert set(results) == {
        "investor_master",
        "transaction_master_new",
        "sip_master_new",
    }
    for table, result in results.items():
        assert result["table"] == table
        assert result["status"] in {"COMPLETED", "SKIPPED", "FAILED"}
        assert isinstance(result["inserted"], int)
        assert isinstance(result["updated"], int)
