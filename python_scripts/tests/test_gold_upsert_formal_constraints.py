import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_amc import load_amc  # noqa: E402
from etl_gold_scheme_nav import load_scheme_nav  # noqa: E402


def _count(table, where_sql, params):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM gold.{table} WHERE {where_sql}"), params).scalar()


def test_load_amc_upsert_is_idempotent_on_rerun():
    amc_code = f"TEST-AMC-{uuid.uuid4().hex[:8]}"
    df = pd.DataFrame([{
        "amc_code": amc_code, "name": "Test AMC", "short_name": "TAMC",
        "rta": "CAMS", "logo_url": None, "status": "ACTIVE",
        "arn": "ARN-1", "sub_arn": None, "created_at": pd.Timestamp.now(),
    }])
    try:
        r1 = load_amc(df.copy())
        assert r1["status"] == "ok"
        assert r1["rows_loaded"] == 1
        assert _count("amc", "amc_code = :c", {"c": amc_code}) == 1

        df2 = df.copy()
        df2["name"] = "Test AMC Renamed"
        r2 = load_amc(df2)
        assert r2["status"] == "ok"
        assert _count("amc", "amc_code = :c", {"c": amc_code}) == 1  # still exactly 1, not 2

        with engine.connect() as conn:
            name = conn.execute(
                text("SELECT name FROM gold.amc WHERE amc_code = :c"), {"c": amc_code}
            ).scalar()
        assert name == "Test AMC Renamed"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.amc WHERE amc_code = :c"), {"c": amc_code})


def test_load_scheme_nav_upsert_is_idempotent_on_rerun():
    scheme_id = str(uuid.uuid4())
    nav_date = pd.Timestamp("2026-08-19").date()
    df = pd.DataFrame([{
        "scheme_id": scheme_id, "nav_date": nav_date, "nav": 10.5,
        "repurchase_nav": None, "source": "TEST", "created_at": pd.Timestamp.now(),
        "arn": "ARN-1", "sub_arn": None,
    }])
    try:
        r1 = load_scheme_nav(df.copy())
        assert r1["status"] == "ok"
        assert _count("scheme_nav", "scheme_id = :s AND nav_date = :d", {"s": scheme_id, "d": nav_date}) == 1

        df2 = df.copy()
        df2["nav"] = 11.0
        load_scheme_nav(df2)
        assert _count("scheme_nav", "scheme_id = :s AND nav_date = :d", {"s": scheme_id, "d": nav_date}) == 1

        with engine.connect() as conn:
            nav = conn.execute(
                text("SELECT nav FROM gold.scheme_nav WHERE scheme_id = :s AND nav_date = :d"),
                {"s": scheme_id, "d": nav_date},
            ).scalar()
        assert float(nav) == 11.0
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM gold.scheme_nav WHERE scheme_id = :s AND nav_date = :d"),
                {"s": scheme_id, "d": nav_date},
            )
