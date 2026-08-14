"""Environment-driven configuration.

Nothing here is hardcoded to a machine. Every value has a default that works on a
local developer box and can be overridden by an environment variable, so the same
code runs in a container or CI without edits.

Deliberately different from python_scripts/utils/db.py, which hardcodes
postgres/postgres in a git-tracked file and keeps an unread .env beside it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class DatabaseSettings:
    """Connection settings for the pipeline's own database."""

    host: str = field(default_factory=lambda: _env("WBR_DB_HOST", "localhost"))
    port: int = field(default_factory=lambda: _env_int("WBR_DB_PORT", 5432))
    user: str = field(default_factory=lambda: _env("WBR_DB_USER", "postgres"))
    password: str = field(default_factory=lambda: _env("WBR_DB_PASSWORD", "postgres"))
    database: str = field(default_factory=lambda: _env("WBR_DB_NAME", "master_tables_db"))

    # Pool settings. pool_pre_ping guards against connections killed while idle;
    # pool_recycle bounds connection age so a long-lived process does not hold a
    # stale socket forever. The existing pipeline sets neither on master_engine.
    pool_size: int = field(default_factory=lambda: _env_int("WBR_DB_POOL_SIZE", 5))
    max_overflow: int = field(default_factory=lambda: _env_int("WBR_DB_MAX_OVERFLOW", 5))
    pool_recycle_seconds: int = field(
        default_factory=lambda: _env_int("WBR_DB_POOL_RECYCLE", 1800)
    )

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def masked_url(self) -> str:
        """Safe for logs — never log `url`."""
        return (
            f"postgresql+psycopg2://{self.user}:***"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass(frozen=True)
class SchemaSettings:
    """Schema names.

    Separate schemas from the existing bronze/silver/gold so this pipeline cannot
    write into live tables. The existing tables have no unique constraints, so a
    mistaken write there is not undoable by re-running.
    """

    bronze: str = field(default_factory=lambda: _env("WBR_BRONZE_SCHEMA", "bronze_wbr"))
    silver: str = field(default_factory=lambda: _env("WBR_SILVER_SCHEMA", "silver_wbr"))
    gold: str = field(default_factory=lambda: _env("WBR_GOLD_SCHEMA", "gold_wbr"))
    audit: str = field(default_factory=lambda: _env("WBR_AUDIT_SCHEMA", "audit_wbr"))


@dataclass(frozen=True)
class PathSettings:
    input_dir: Path = field(
        default_factory=lambda: Path(
            _env("WBR_INPUT_DIR", "/home/user/Inteliwealth-pipeline/files/gold")
        )
    )
    output_dir: Path = field(
        default_factory=lambda: Path(_env("WBR_OUTPUT_DIR", str(PROJECT_ROOT / "output")))
    )
    sql_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "sql")


@dataclass(frozen=True)
class RuntimeSettings:
    log_level: str = field(default_factory=lambda: _env("WBR_LOG_LEVEL", "INFO").upper())
    chunksize: int = field(default_factory=lambda: _env_int("WBR_CHUNKSIZE", 2000))

    # Fail the run when a required column is missing or a cast fails, instead of
    # inserting NULL and reporting success. This is the single behavioural choice
    # that separates this pipeline from the existing one.
    strict: bool = field(default_factory=lambda: _env_bool("WBR_STRICT", True))

    # soffice is only needed to emit legacy .xls output. Reading never needs it.
    soffice_bin: str = field(default_factory=lambda: _env("WBR_SOFFICE", "soffice"))


@dataclass(frozen=True)
class Settings:
    db: DatabaseSettings = field(default_factory=DatabaseSettings)
    schemas: SchemaSettings = field(default_factory=SchemaSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)


def load_settings() -> Settings:
    """Build settings from the current environment.

    Called explicitly rather than evaluated at import so that importing any module
    in this package never touches the environment or the database. Two modules in
    the existing pipeline run pd.read_sql at import time, which means starting
    Streamlit queries the database before rendering anything.
    """
    return Settings()
