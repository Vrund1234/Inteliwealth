import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_scheme import load_scheme  # noqa: E402
from etl_gold_folio_nominees import load_folio_nominees  # noqa: E402


def test_load_scheme_upsert_is_idempotent_and_preserves_isin_and_id():
    rta = "TESTRTA"
    scheme_code = f"TST{uuid.uuid4().hex[:6]}".upper()  # load_scheme upper-cases via clean_text
    original_id = str(uuid.uuid4())

    df = pd.DataFrame([{
        "id": original_id, "rta": rta, "scheme_code": scheme_code,
        "scheme_name": "Test Scheme", "category": "Equity", "plan": "Growth",
        "isin": "INE000000001", "amfi_code": None, "category_id": None,
        "plan_type": None, "option_type": None, "rta_scheme_code": scheme_code,
        "benchmark_id": None, "expense_ratio": None, "exit_load_json": None,
        "lock_in_months": None, "riskometer": None, "status": "ACTIVE",
        "arn": "ARN-1", "sub_arn": None, "amc_id": None,
        "created_at": pd.Timestamp.now(),
    }])
    try:
        r1 = load_scheme(df.copy())
        assert r1["status"] == "ok"

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id, isin FROM gold.scheme WHERE rta = :r AND scheme_code = :c"),
                {"r": rta, "c": scheme_code},
            ).fetchone()
        assert row is not None
        assert str(row[0]) == original_id
        # ISIN is intentionally never written by this loader, on insert OR update —
        # matches the original code's "FORCE ISIN NULL BEFORE INSERT" behavior.
        assert row[1] is None

        # Re-run with a different id — existing id must survive; scheme_name should update.
        df2 = df.copy()
        df2["id"] = str(uuid.uuid4())
        df2["scheme_name"] = "Test Scheme Renamed"
        r2 = load_scheme(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM gold.scheme WHERE rta = :r AND scheme_code = :c"),
                {"r": rta, "c": scheme_code},
            ).scalar()
            row2 = conn.execute(
                text("SELECT id, isin, scheme_name FROM gold.scheme WHERE rta = :r AND scheme_code = :c"),
                {"r": rta, "c": scheme_code},
            ).fetchone()
        assert count == 1  # no duplicate row
        assert str(row2[0]) == original_id  # id unchanged
        assert row2[1] is None  # isin still never written
        assert row2[2] == "Test Scheme Renamed"  # scheme_name did update
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.scheme WHERE rta = :r AND scheme_code = :c"), {"r": rta, "c": scheme_code})


def test_load_folio_nominees_upsert_is_idempotent_on_rerun():
    holding_id = str(uuid.uuid4())
    df = pd.DataFrame([{
        "holding_id": holding_id, "seq": 1, "name": "Test Nominee",
        "relationship": "Spouse", "percentage": 100, "dob": None,
        "is_minor": False, "guardian_name": None, "id_type": None,
        "id_no": None, "address": None, "arn": "ARN-1", "sub_arn": None,
    }])
    try:
        r1 = load_folio_nominees(df.copy())
        assert r1["status"] == "ok"

        df2 = df.copy()
        df2["name"] = "Renamed Nominee"
        r2 = load_folio_nominees(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM gold.folio_nominees WHERE holding_id = :h AND seq = 1"),
                {"h": holding_id},
            ).scalar()
            name = conn.execute(
                text("SELECT name FROM gold.folio_nominees WHERE holding_id = :h AND seq = 1"),
                {"h": holding_id},
            ).scalar()
        assert count == 1
        assert name == "Renamed Nominee"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.folio_nominees WHERE holding_id = :h"), {"h": holding_id})
