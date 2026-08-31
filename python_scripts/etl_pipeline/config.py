"""Typed .env loading. Every tunable is overridable; nothing is hardcoded at a
call site."""

import os
from pathlib import Path

from dotenv import load_dotenv

# python_scripts/etl_pipeline/config.py -> python_scripts/ -> <repo root>
PYTHON_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PYTHON_SCRIPTS_DIR.parent

# Loaded explicitly by path rather than by search: cron runs this with a
# working directory that load_dotenv()'s upward search would not start from.
load_dotenv(PYTHON_SCRIPTS_DIR / ".env")


def _int(name, default, minimum=None, maximum=None):
    """Read an int, falling back to `default` on anything unparseable, and
    clamp it into [minimum, maximum] when those are given.

    Clamping is not defensive padding: ETL_BATCH_LIMIT is `ge=1, le=50` on
    POST /etl-handoff/reservations and ETL_PEEK_LIMIT is `ge=1, le=200` on
    GET /etl-handoff/pending. An out-of-range value is a 422, a 422 is never
    retried, and the run would silently accomplish nothing every 15 minutes.
    """
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _path(name, default_relative):
    """Resolve a directory setting against the repo root when it is relative.

    The crontab cds into python_scripts/ and the .env value is written as
    "../logs", but a manual run from anywhere else must reach the same
    directory. A leading ".." is therefore interpreted as "up out of
    python_scripts/", which is exactly the repo root -- not as a literal
    parent of wherever the process happens to have been started.
    """
    raw = os.getenv(name) or default_relative
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    parts = [p for p in candidate.parts if p not in ("..", ".")]
    return (REPO_ROOT.joinpath(*parts)).resolve()


# --- AWS S3 ---------------------------------------------------------------

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION") or "ap-south-1"
# Empty string means "no custom endpoint" -- boto3 must never be handed
# endpoint_url="". A real value points at MinIO or localstack in development.
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or None

# --- intelli-wealth-backend etl-handoff API -------------------------------

INTELLIWEALTH_API_BASE = (
    os.getenv("INTELLIWEALTH_API_BASE") or "http://127.0.0.1:8000/api/v1"
).rstrip("/")
INTELLIWEALTH_RUNNER_EMAIL = os.getenv("INTELLIWEALTH_RUNNER_EMAIL")
# Contains "@". It travels only in a JSON body, never interpolated into a URL,
# so the quote_plus hazard utils/db.py documents for the DB DSN does not apply.
INTELLIWEALTH_RUNNER_PASSWORD = os.getenv("INTELLIWEALTH_RUNNER_PASSWORD")
ETL_RUNNER_NAME = os.getenv("ETL_RUNNER_NAME") or "de-etl-worker-1"

# --- Pipeline tuning ------------------------------------------------------

ETL_BATCH_LIMIT = _int("ETL_BATCH_LIMIT", 10, minimum=1, maximum=50)
ETL_PEEK_LIMIT = _int("ETL_PEEK_LIMIT", 50, minimum=1, maximum=200)
ETL_HTTP_TIMEOUT_SECONDS = _int("ETL_HTTP_TIMEOUT_SECONDS", 30, minimum=1)
# The access token lives 30 minutes and a reservation lives 60, so outliving
# the token mid-batch is the normal case. 300 refreshes at ~25 minutes.
ETL_TOKEN_REFRESH_MARGIN_SECONDS = _int(
    "ETL_TOKEN_REFRESH_MARGIN_SECONDS", 300, minimum=0
)
ETL_LOG_DIR = _path("ETL_LOG_DIR", "logs")
ETL_LOG_RETENTION_DAYS = _int("ETL_LOG_RETENTION_DAYS", 30, minimum=1)
