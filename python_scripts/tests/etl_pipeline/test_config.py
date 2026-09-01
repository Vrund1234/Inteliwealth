"""etl_pipeline.config: every tunable comes from .env with a documented
default, and the two API-side limits are clamped rather than allowed to
produce a 422 that is never retried."""

import importlib
from pathlib import Path

import pytest


def _reload(monkeypatch, **env):
    """Reload config with ONLY the given environment.

    load_dotenv() is stubbed out for the reload: otherwise every "default"
    assertion below silently tests whatever python_scripts/.env happens to
    contain, and changing a real .env value breaks an unrelated test. (It did:
    raising ETL_BATCH_LIMIT to 50 in .env failed
    test_defaults_apply_when_nothing_is_set.)
    """
    import dotenv

    import etl_pipeline.config as config

    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    # Patched on the dotenv module, not on config: importlib.reload() re-runs
    # `from dotenv import load_dotenv`, which would rebind a config-level patch.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    return importlib.reload(config)


def test_defaults_apply_when_nothing_is_set(monkeypatch):
    config = _reload(
        monkeypatch,
        AWS_REGION=None,
        ETL_RUNNER_NAME=None,
        ETL_BATCH_LIMIT=None,
        ETL_PEEK_LIMIT=None,
        ETL_HTTP_TIMEOUT_SECONDS=None,
        ETL_TOKEN_REFRESH_MARGIN_SECONDS=None,
        ETL_LOG_RETENTION_DAYS=None,
    )

    assert config.AWS_REGION == "ap-south-1"
    assert config.ETL_RUNNER_NAME == "de-etl-worker-1"
    assert config.ETL_BATCH_LIMIT == 10
    assert config.ETL_PEEK_LIMIT == 50
    assert config.ETL_HTTP_TIMEOUT_SECONDS == 30
    assert config.ETL_TOKEN_REFRESH_MARGIN_SECONDS == 300
    assert config.ETL_LOG_RETENTION_DAYS == 30


def test_integers_come_from_the_environment(monkeypatch):
    config = _reload(monkeypatch, ETL_BATCH_LIMIT="25", ETL_PEEK_LIMIT="200")

    assert config.ETL_BATCH_LIMIT == 25
    assert config.ETL_PEEK_LIMIT == 200


def test_a_non_numeric_value_falls_back_to_the_default(monkeypatch):
    config = _reload(monkeypatch, ETL_BATCH_LIMIT="ten")

    assert config.ETL_BATCH_LIMIT == 10


def test_batch_limit_is_clamped_to_the_api_maximum(monkeypatch):
    # POST /etl-handoff/reservations declares limit ge=1, le=50. 51 is a 422,
    # and a 422 is never retried -- so every run would silently do nothing.
    config = _reload(monkeypatch, ETL_BATCH_LIMIT="500")

    assert config.ETL_BATCH_LIMIT == 50


def test_batch_limit_is_clamped_to_at_least_one(monkeypatch):
    config = _reload(monkeypatch, ETL_BATCH_LIMIT="0")

    assert config.ETL_BATCH_LIMIT == 1


def test_peek_limit_is_clamped_to_the_api_maximum(monkeypatch):
    # GET /etl-handoff/pending declares limit ge=1, le=200.
    config = _reload(monkeypatch, ETL_PEEK_LIMIT="9999")

    assert config.ETL_PEEK_LIMIT == 200


def test_an_empty_s3_endpoint_url_is_none(monkeypatch):
    # boto3 must not be handed endpoint_url="" -- the key is only passed when
    # a real MinIO/localstack endpoint is configured.
    config = _reload(monkeypatch, AWS_S3_ENDPOINT_URL="")

    assert config.AWS_S3_ENDPOINT_URL is None


def test_a_relative_log_dir_resolves_against_the_repo_root(monkeypatch):
    # The crontab cds into python_scripts/, but a run started from anywhere
    # else must write to the same logs/ directory.
    config = _reload(monkeypatch, ETL_LOG_DIR="../logs")

    assert config.ETL_LOG_DIR.is_absolute()
    assert config.ETL_LOG_DIR == config.REPO_ROOT / "logs"


def test_an_absolute_log_dir_is_used_verbatim(monkeypatch):
    config = _reload(monkeypatch, ETL_LOG_DIR="/var/log/etl_pipeline")

    assert config.ETL_LOG_DIR == Path("/var/log/etl_pipeline")


def test_repo_root_is_the_parent_of_python_scripts(monkeypatch):
    config = _reload(monkeypatch)

    assert (config.REPO_ROOT / "python_scripts" / "etl_pipeline").is_dir()
