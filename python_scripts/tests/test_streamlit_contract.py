"""The Streamlit app must run byte-identically after the pipeline work. It is
not being replaced, wrapped or deprecated -- it remains the manual entry point.

These are the assertions that would catch a regression in the contract app.py
depends on, without importing app.py itself (importing it would execute the
Streamlit page)."""

import inspect
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

import gold_loader
import raw_ingestion
import utils.db
from transformations import transform
from utils.db import engine


# ---- signatures ----------------------------------------------------------

def test_extract_and_push_still_takes_one_argument():
    assert list(inspect.signature(raw_ingestion.extract_and_push).parameters) == [
        "uploaded_files"
    ]


def test_extract_and_push_still_returns_a_four_tuple():
    # app.py:262-269 unpacks exactly four values.
    result = raw_ingestion.extract_and_push([])

    assert isinstance(result, tuple)
    assert len(result) == 4
    assert result[:3] == (0, 0, 0)
    assert result[3] is None


def test_process_loader_signatures_are_unchanged():
    from etl_investor_master import process_investor_master
    from etl_sip import process_sip
    from etl_trans import process_transactions

    assert list(inspect.signature(process_transactions).parameters) == ["cams", "kfin"]
    assert list(inspect.signature(process_investor_master).parameters) == [
        "cams", "kfin"
    ]
    assert list(inspect.signature(process_sip).parameters) == [
        "cams", "kfin", "cams_source", "kfin_source"
    ]


def test_load_silver_and_load_gold_still_take_no_arguments():
    # app.py:647 and app.py:665 call them as bare statements.
    assert list(inspect.signature(transform.load_silver).parameters) == []
    assert list(inspect.signature(gold_loader.load_gold).parameters) == []


def test_append_new_rows_signature_is_unchanged():
    assert list(inspect.signature(transform.append_new_rows).parameters) == [
        "df", "table_name"
    ]


def test_upsert_dataframe_signature_is_unchanged():
    assert list(inspect.signature(utils.db.upsert_dataframe).parameters) == [
        "df", "schema", "table", "conflict_columns", "conflict_constraint",
        "conflict_index_expr", "engine", "chunksize", "updated_at_column",
    ]


# ---- exception handling --------------------------------------------------

@pytest.fixture
def folio():
    value = uuid.uuid4().hex[:10]
    yield value
    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM silver.investor_master WHERE folio_no = :f"
        ), {"f": value})


def _rows(folio):
    return pd.DataFrame([{
        "source": "CAMS", "folio_no": folio, "product_code": "P1",
        "investor_name": "TEST INVESTOR", "flag": 0,
    }])


def test_append_new_rows_still_swallows_an_upsert_failure(monkeypatch, folio):
    # The Transform button must not blow up on a silver failure. This is the
    # real swallow: the try/except around upsert_dataframe at transform.py:652.
    def boom(*args, **kwargs):
        raise RuntimeError("upsert exploded")

    monkeypatch.setattr(utils.db, "upsert_dataframe", boom)

    result = transform.append_new_rows(_rows(folio), "investor_master")

    assert result["status"] == "FAILED"
    assert "upsert exploded" in result["error"]


def test_append_new_rows_still_prints_its_error(monkeypatch, capsys, folio):
    def boom(*args, **kwargs):
        raise RuntimeError("upsert exploded")

    monkeypatch.setattr(utils.db, "upsert_dataframe", boom)

    transform.append_new_rows(_rows(folio), "investor_master")

    printed = capsys.readouterr().out
    assert "SILVER INSERT ERROR" in printed
    assert "upsert exploded" in printed


def test_a_missing_silver_table_is_reported_rather_than_raised(folio):
    result = transform.append_new_rows(_rows(folio), "no_such_silver_table")

    assert result["status"] == "FAILED"
    assert result["inserted"] == 0


def test_load_gold_still_swallows_a_raising_entity(monkeypatch):
    def boom():
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(gold_loader, "extract_amc", boom)

    # Must not raise.
    results = gold_loader.load_gold()

    assert results["amc"]["status"] == "FAILED"


def test_load_gold_still_prints_its_error(monkeypatch, capsys):
    def boom():
        raise RuntimeError("extract exploded")

    monkeypatch.setattr(gold_loader, "extract_amc", boom)

    gold_loader.load_gold()

    assert "AMC Gold Failed" in capsys.readouterr().out


def test_gold_loader_still_has_eight_swallowing_blocks():
    source = inspect.getsource(gold_loader.load_gold)

    assert source.count("except Exception as e:") == 8


# ---- app.py itself -------------------------------------------------------

def test_app_py_was_not_modified():
    # Read as text rather than imported: importing app.py executes the
    # Streamlit page. These three call sites are the whole contract.
    source = Path(__file__).resolve().parent.parent.joinpath("app.py").read_text()

    assert ") = extract_and_push(" in source
    assert "\n            load_silver()\n" in source
    assert "\n            load_gold()\n" in source
    # The runner must not have been wired into the UI.
    assert "etl_pipeline" not in source
