import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import config  # noqa: E402


def test_db_config_loaded_from_env():
    assert config.DB_NAME
    assert config.DB_PASSWORD
    assert config.DB_HOST == "localhost"
    assert config.DB_PORT == "5432"


def test_master_db_config_is_separate_from_project_db():
    assert config.MASTER_POSTGRES_DB == "intelliwealth"
    assert config.MASTER_POSTGRES_USER == "intelliwealth"
    assert config.MASTER_POSTGRES_PASSWORD == "intelliwealth"
    assert config.MASTER_POSTGRES_HOST == "db"
    # must NOT silently fall back to reusing the project DB's credentials
    assert config.MASTER_POSTGRES_USER != config.DB_USER or config.MASTER_POSTGRES_HOST != config.DB_HOST


def test_tuning_values_are_ints_with_sane_defaults():
    assert isinstance(config.ETL_BATCH_LIMIT, int)
    assert config.ETL_BATCH_LIMIT == 50
    assert isinstance(config.ETL_PEEK_LIMIT, int)
    assert config.ETL_PEEK_LIMIT == 200
    assert isinstance(config.ETL_HOLD_TIMEOUT_MINUTES, int)
    assert isinstance(config.ETL_MAX_RESERVATION_CALLS_PER_RUN, int)
