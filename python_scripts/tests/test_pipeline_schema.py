import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from utils.db import engine  # noqa: E402

EXPECTED_TABLES = {"etl_pipeline_log", "etl_report_group_hold", "etl_processed_files"}


def test_pipeline_schema_tables_exist():
    df = pd.read_sql(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'pipeline'",
        engine
    )
    assert EXPECTED_TABLES <= set(df["table_name"])


def test_etl_report_group_hold_columns():
    df = pd.read_sql(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'pipeline' AND table_name = 'etl_report_group_hold'
        """,
        engine
    )
    expected = {
        "group_key", "rta", "arn_code", "s3_date", "required_report_codes",
        "members", "status", "first_seen_at", "last_updated_at", "completed_at",
    }
    assert expected <= set(df["column_name"])
