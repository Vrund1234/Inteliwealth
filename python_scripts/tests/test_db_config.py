import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import config  # noqa: E402
from utils.db import engine, master_engine  # noqa: E402


def test_engine_uses_configured_database_name():
    assert config.DB_NAME in str(engine.url)


def test_master_engine_uses_configured_master_database_name():
    assert config.MASTER_POSTGRES_DB in str(master_engine.url)


def test_master_engine_uses_separate_credentials_from_project_engine():
    assert master_engine.url.username == config.MASTER_POSTGRES_USER
    assert master_engine.url.host == config.MASTER_POSTGRES_HOST
    assert engine.url.username == config.DB_USER
    assert engine.url.host == config.DB_HOST


def test_engine_is_actually_reachable():
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1
