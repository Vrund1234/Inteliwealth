import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from utils.upsert import upsert_dataframe  # noqa: E402

TEST_TABLE = "test_upsert_target"


def setup_module(module):
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS pipeline.{TEST_TABLE}"))
        conn.execute(
            text(
                f"CREATE TABLE pipeline.{TEST_TABLE} ("
                f"id INTEGER, value VARCHAR, "
                f"CONSTRAINT uq_test_upsert_id UNIQUE (id))"
            )
        )


def teardown_module(module):
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS pipeline.{TEST_TABLE}"))


def _rows():
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT id, value FROM pipeline.{TEST_TABLE} ORDER BY id")
        ).fetchall()


def test_upsert_inserts_new_rows():
    df = pd.DataFrame({"id": [1, 2], "value": ["a", "b"]})
    affected = upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert affected == 2
    assert _rows() == [(1, "a"), (2, "b")]


def test_upsert_updates_on_conflict():
    df = pd.DataFrame({"id": [1], "value": ["updated"]})
    upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert _rows() == [(1, "updated"), (2, "b")]


def test_upsert_do_nothing_on_conflict():
    df = pd.DataFrame({"id": [2], "value": ["should-not-apply"]})
    upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql=None,
    )
    assert _rows() == [(1, "updated"), (2, "b")]


def test_upsert_empty_dataframe_is_a_noop():
    affected = upsert_dataframe(
        engine, pd.DataFrame(columns=["id", "value"]), schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert affected == 0
