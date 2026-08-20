import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


def _int(name, default):
    return int(os.getenv(name, str(default)))


DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# Master DB (intelli-wealth-backend's own database) — separate credentials
# and host from the project DB above, e.g. reachable at its docker-compose
# service name rather than "localhost".
MASTER_POSTGRES_HOST = os.getenv("MASTER_POSTGRES_HOST", "localhost")
MASTER_POSTGRES_PORT = os.getenv("MASTER_POSTGRES_PORT", "5432")
MASTER_POSTGRES_USER = os.getenv("MASTER_POSTGRES_USER", "postgres")
MASTER_POSTGRES_PASSWORD = os.getenv("MASTER_POSTGRES_PASSWORD")
MASTER_POSTGRES_DB = os.getenv("MASTER_POSTGRES_DB", "intelliwealth")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or None

INTELLIWEALTH_API_BASE = os.getenv("INTELLIWEALTH_API_BASE", "http://localhost:8000/api/v1")
INTELLIWEALTH_RUNNER_EMAIL = os.getenv("INTELLIWEALTH_RUNNER_EMAIL")
INTELLIWEALTH_RUNNER_PASSWORD = os.getenv("INTELLIWEALTH_RUNNER_PASSWORD")
ETL_RUNNER_NAME = os.getenv("ETL_RUNNER_NAME", "de-etl-worker-1")

ETL_BATCH_LIMIT = _int("ETL_BATCH_LIMIT", 50)
ETL_PEEK_LIMIT = _int("ETL_PEEK_LIMIT", 200)
ETL_HOLD_TIMEOUT_MINUTES = _int("ETL_HOLD_TIMEOUT_MINUTES", 240)
ETL_MAX_RESERVATION_CALLS_PER_RUN = _int("ETL_MAX_RESERVATION_CALLS_PER_RUN", 5)
