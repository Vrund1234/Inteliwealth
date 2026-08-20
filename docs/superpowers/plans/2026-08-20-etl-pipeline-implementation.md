# Automated ETL Handoff Pipeline — Implementation Plan (Part A: Core Pipeline)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cron-driven worker in `python_scripts/etl_pipeline/` that claims files from `intelli-wealth-backend`'s `/etl-handoff` queue, holds CAMS `WBR2`/`WBR9`/`WBR49` (and KFIN `MFSD201/211/243`) for the same distributor+date until all three arrive, processes them through the existing bronze→silver→gold functions, reports outcomes back, and logs every step layer-wise to a new `pipeline` schema.

**Architecture:** New `python_scripts/etl_pipeline/` package (config, API client, S3 client, hold/group logic, dedup, advisory lock, log writer, orchestrator) sits in front of the existing, unmodified bronze ETL functions (`raw_ingestion.read_file`, `etl_trans.process_transactions`, `etl_investor_master.process_investor_master`, `etl_sip.process_sip`, `gold_loader.load_gold`). Three new tables in a new `pipeline` schema track state. One `load_*` return-value change (`utils/gold_result.py` + 8 files) is included because the orchestrator needs real row counts, not `print()`+swallow, to report accurate outcomes.

**Tech Stack:** Python 3.13.14, SQLAlchemy 2.x + psycopg2 (existing), pandas (existing), `boto3` (new), `requests` (new), `python-dotenv` (new), pytest (existing, real-DB-integration style per this repo's convention — no sqlite/mocking for DB tests, `unittest.mock` for the external API/S3 boundary only).

**Spec:** `docs/superpowers/specs/2026-08-19-etl-pipeline-design.md` (read alongside this plan — this plan implements §2–§6, §8–§11, and the row-count-return slice of §7.1; the rest of §7 — ON CONFLICT upserts, `drop_duplicates`, folio_nominees ARN population — is **Part B**, a separate plan, because it's independently valuable and independently testable: gold-layer idempotency matters whether or not this pipeline exists, and this pipeline works without it, just single-instance-only).

## Global Constraints

- Python 3.13.14, existing `venv` at `python_scripts/venv/`.
- No hardcoded secrets in code — everything through `python_scripts/.env`, loaded via `python-dotenv`. `.env` must be gitignored; `.env.example` documents every key with a placeholder, never a real value.
- Postgres 14.23 (confirmed live) — no `NULLS NOT DISTINCT` (PG15+ only).
- DB access convention: real Postgres via `utils.db.engine`/`master_engine`, no test-database mocking — matches every existing test in `python_scripts/tests/`.
- `source_s3_uri` is ONLY available from `POST /etl-handoff/reservations`'s `EtlHandoffItem`, never from `GET /etl-handoff/pending`'s `EtlHandoffRead` (confirmed against `intelli-wealth-backend/app/modules/etl_handoff/router.py` — see spec §5.1 errata). Grouping is two-stage: coarse key from `/pending` (`rta|arn_code|created_at.date()`), authoritative key from `/reservations` (`rta|arn_code|s3_date-from-uri`).
- Report codes are exactly `WBR2`, `WBR9`, `WBR49` (CAMS) and `MFSD201`, `MFSD211`, `MFSD243` (KFIN) — confirmed against `intelli-wealth-backend/app/modules/email_automation/report_registry.py`.
- `COMPLETED`/`ABANDONED`/`SKIPPED` are terminal on the API; `FAILED` re-enters the queue until `attempt_count >= 3`. A `409` on `PATCH` means "another runner reclaimed it" — log and drop, never retry that PATCH.

---

## Known simplification (documented, not hidden)

`gold_loader.load_gold()` processes whatever is newly available system-wide each call (its `extract_*` functions use incremental filters like `created_at > MAX(gold.created_at)`), not a slice scoped to one distributor+date group. So GOLD-layer outcome reporting in this plan is necessarily **per-run, not per-file**: one `load_gold()` call covers every group that finished bronze in that run, and every file in every one of those groups gets the same gold outcome. This is correct today because Part A runs single-instance (one group-batch per run is the common case); it stops being precise if this pipeline is later scaled to fully independent concurrent group processing — that upgrade depends on Part B's idempotent, per-table ON CONFLICT writes.

If one member of an otherwise-ready group fails at the bronze-download/parse step while its siblings in the same run already succeeded, the succeeded siblings' bronze rows stay committed (each `to_sql` call auto-commits) and are marked `bronze_done` in `etl_report_group_hold.members` so a later run does not re-download or re-insert them — only the failed member is retried once it reappears through the queue. This relies on Part B's bronze unique indexes (already drafted in `sql_scripts/add_constraints.sql`) to make any accidental re-insert fail loudly rather than duplicate silently; apply Part B before relying on this pipeline under real concurrent load.

---

### Task 1: Dependencies + env-based config scaffold

**Files:**
- Modify: `python_scripts/requirements.txt`
- Create: `python_scripts/.env.example`
- Create: `python_scripts/etl_pipeline/__init__.py`
- Create: `python_scripts/etl_pipeline/config.py`
- Modify: `.gitignore` (repo root)
- Test: `python_scripts/tests/test_etl_pipeline_config.py`

**Interfaces:**
- Produces: `etl_pipeline.config` module with attributes `DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME` (project DB — this repo's own bronze/silver/gold/pipeline schemas), `MASTER_POSTGRES_HOST, MASTER_POSTGRES_PORT, MASTER_POSTGRES_USER, MASTER_POSTGRES_PASSWORD, MASTER_POSTGRES_DB` (master DB — `intelli-wealth-backend`'s own database, e.g. `public.arn` — a **separate credential set and host**, not reused from the project DB), `AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_S3_ENDPOINT_URL, INTELLIWEALTH_API_BASE, INTELLIWEALTH_RUNNER_EMAIL, INTELLIWEALTH_RUNNER_PASSWORD, ETL_RUNNER_NAME, ETL_BATCH_LIMIT (int), ETL_PEEK_LIMIT (int), ETL_HOLD_TIMEOUT_MINUTES (int), ETL_MAX_RESERVATION_CALLS_PER_RUN (int)` — every later task imports from here.

- [ ] **Step 1: Add new dependencies**

Append to `python_scripts/requirements.txt`:
```
boto3
requests
python-dotenv
dbfread
```
(`dbfread` is already imported by `raw_ingestion.py` but was missing from this file — fixing that gap here too.)

- [ ] **Step 2: Install them**

Run: `cd python_scripts && source venv/bin/activate && pip install -r requirements.txt`
Expected: all four install cleanly, no errors.

- [ ] **Step 3: Create `python_scripts/.env.example`**

```
# --- Project Database (this repo's own bronze/silver/gold/pipeline schemas —
# moved off the hardcoded values in utils/db.py) ---
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=changeme
DB_NAME=19_08_2026_intelliwealth_layer_db

# --- Master Database (intelli-wealth-backend's own DB — public.arn, etc.
# SEPARATE credentials/host from the project DB above: this is a different
# service, typically reachable at its docker-compose service name when this
# pipeline runs inside that network — not "localhost"). ---
MASTER_POSTGRES_HOST=db
MASTER_POSTGRES_PORT=5432
MASTER_POSTGRES_USER=intelliwealth
MASTER_POSTGRES_PASSWORD=intelliwealth
MASTER_POSTGRES_DB=intelliwealth

# --- AWS S3 (new — this pipeline is the first S3 consumer in this repo) ---
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=ap-south-1
AWS_S3_ENDPOINT_URL=

# --- intelli-wealth-backend etl-handoff API ---
INTELLIWEALTH_API_BASE=http://localhost:8000/api/v1
INTELLIWEALTH_RUNNER_EMAIL=etl-runner@intelliwealth.com
INTELLIWEALTH_RUNNER_PASSWORD=changeme
ETL_RUNNER_NAME=de-etl-worker-1

# --- Pipeline tuning (all overridable, none hardcoded in code) ---
ETL_BATCH_LIMIT=50
ETL_PEEK_LIMIT=200
ETL_HOLD_TIMEOUT_MINUTES=240
ETL_MAX_RESERVATION_CALLS_PER_RUN=5
```

- [ ] **Step 4: Add `.gitignore` entry**

Append to the repo-root `.gitignore` (create the file if it doesn't exist):
```
python_scripts/.env
```

- [ ] **Step 5: Create the real `.env` for this environment**

Copy `python_scripts/.env.example` to `python_scripts/.env`. Set the project DB values to the actual live credentials for this environment — `DB_HOST=localhost`, `DB_PORT=5432`, `DB_USER=postgres`, `DB_PASSWORD=Test123migration`, `DB_NAME=19_08_2026_intelliwealth_layer_db` (do not read these from `python_scripts/utils/db.py` — that file may be mid-migration/stale at this point in the branch history; these are the confirmed-working values). Set the five `MASTER_POSTGRES_*` values exactly as given in `.env.example` (`db` / `5432` / `intelliwealth` / `intelliwealth` / `intelliwealth`) — these are `intelli-wealth-backend`'s own DB credentials, confirmed separately, not derived from the project DB. Leave `AWS_*`/`INTELLIWEALTH_*` blank until Part A's later tasks need them (Task 4 for the API client, Task 6 for S3).

- [ ] **Step 6: Create `python_scripts/etl_pipeline/__init__.py`** (empty file, makes it a package)

- [ ] **Step 7: Write the failing test**

```python
# python_scripts/tests/test_etl_pipeline_config.py
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
```

- [ ] **Step 8: Run test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_etl_pipeline_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.config'` (or `config` has no such attribute).

- [ ] **Step 9: Write `python_scripts/etl_pipeline/config.py`**

```python
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
```

- [ ] **Step 10: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_etl_pipeline_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 11: Commit**

```bash
git add python_scripts/requirements.txt python_scripts/.env.example .gitignore \
        python_scripts/etl_pipeline/__init__.py python_scripts/etl_pipeline/config.py \
        python_scripts/tests/test_etl_pipeline_config.py
git commit -m "feat: add etl_pipeline package with env-based config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Migrate `utils/db.py` off hardcoded credentials

**Files:**
- Modify: `python_scripts/utils/db.py`
- Test: `python_scripts/tests/test_db_config.py`

**Interfaces:**
- Consumes: `etl_pipeline.config` (Task 1).
- Produces: `utils.db.engine`, `utils.db.master_engine` unchanged in shape (still SQLAlchemy `Engine` objects), now built from `config.*` instead of literals. `utils.db.read_table` unchanged.

- [ ] **Step 1: Write the failing test**

```python
# python_scripts/tests/test_db_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_db_config.py -v`
Expected: FAIL — current `db.py` still has its own hardcoded (and possibly stale — see Task 1 Step 5's note) credentials, not `config`-derived ones, so at minimum the name/credential-matching assertions fail, and `engine.connect()` may fail outright if the hardcoded values don't point at a reachable DB in this environment. Either failure mode confirms the migration in Step 3 is necessary.

- [ ] **Step 3: Rewrite `python_scripts/utils/db.py`**

```python
from sqlalchemy import create_engine
import pandas as pd
from urllib.parse import quote_plus

from etl_pipeline import config

ENCODED_PASSWORD = quote_plus(config.DB_PASSWORD)
MASTER_ENCODED_PASSWORD = quote_plus(config.MASTER_POSTGRES_PASSWORD)

# Project Database (this repo's own bronze/silver/gold/pipeline schemas)
engine = create_engine(
    f"postgresql+psycopg2://{config.DB_USER}:{ENCODED_PASSWORD}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}",
    pool_pre_ping=True
)

# Master Database (intelli-wealth-backend's own DB — separate credentials/host)
master_engine = create_engine(
    f"postgresql+psycopg2://{config.MASTER_POSTGRES_USER}:{MASTER_ENCODED_PASSWORD}"
    f"@{config.MASTER_POSTGRES_HOST}:{config.MASTER_POSTGRES_PORT}/{config.MASTER_POSTGRES_DB}",
    pool_pre_ping=True
)


def read_table(schema, table, limit=100):
    try:
        cols_df = pd.read_sql(
            f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            """,
            engine
        )
        columns = set(cols_df["column_name"])
        order_col = next(
            (c for c in ("last_synced_at", "updated_at", "created_at") if c in columns),
            None
        )
        order_clause = f'ORDER BY "{order_col}" DESC' if order_col else ""
        df = pd.read_sql(
            f'SELECT * FROM "{schema}"."{table}" {order_clause} LIMIT {int(limit)}',
            engine
        )
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].dt.tz_localize("UTC", nonexistent="NaT", ambiguous="NaT") \
                    if df[col].dt.tz is None else df[col]
                df[col] = df[col].dt.tz_convert("Asia/Kolkata")
        return df
    except Exception as e:
        print("read_table error:", e)
        return pd.DataFrame()
```

Note: keep whatever the current `read_table` body actually does beyond the engine change — this step only changes how `engine`/`master_engine` are constructed. If the existing `read_table` body differs from the reconstruction above in any formatting/logic detail, preserve the existing body verbatim and only touch the two `create_engine(...)` blocks and the new `from etl_pipeline import config` import.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_db_config.py -v`
Expected: PASS (4 tests). Note `master_engine` is built from `MASTER_POSTGRES_HOST=db`, a docker-compose service name — it will not resolve outside that network, so this test suite deliberately never calls `master_engine.connect()`, only checks its constructed URL.

- [ ] **Step 5: Run the full existing test suite to confirm nothing else broke**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: same pass/fail counts as before this change (this migration must be behavior-neutral for every existing test).

- [ ] **Step 6: Commit**

```bash
git add python_scripts/utils/db.py python_scripts/tests/test_db_config.py
git commit -m "refactor: move DB credentials from utils/db.py into env config

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `pipeline` schema — three new tables

**Files:**
- Create: `sql_scripts/etl_pipeline_schema.sql`
- Test: `python_scripts/tests/test_pipeline_schema.py`

**Interfaces:**
- Produces: `pipeline.etl_pipeline_log`, `pipeline.etl_report_group_hold`, `pipeline.etl_processed_files` tables, used by every task from here on.

- [ ] **Step 1: Write `sql_scripts/etl_pipeline_schema.sql`**

```sql
-- ETL pipeline operational tracking schema.
-- Idempotent: every statement is CREATE ... IF NOT EXISTS, safe to re-run.

CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE IF NOT EXISTS pipeline.etl_pipeline_log (
    log_id                   UUID PRIMARY KEY,
    run_id                   UUID NOT NULL,
    handoff_id               UUID,
    group_key                VARCHAR NOT NULL,
    rta                      VARCHAR NOT NULL,
    arn_code                 VARCHAR,
    report_code              VARCHAR NOT NULL,
    filename                 VARCHAR,
    source_s3_uri            VARCHAR,
    content_hash             VARCHAR,
    layer                    VARCHAR NOT NULL,
    status                   VARCHAR NOT NULL,
    total_records_in_report  INTEGER,
    total_processed          INTEGER,
    error_message            VARCHAR,
    started_at               TIMESTAMPTZ,
    ended_at                 TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_group_key ON pipeline.etl_pipeline_log (group_key);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_handoff_id ON pipeline.etl_pipeline_log (handoff_id);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_status ON pipeline.etl_pipeline_log (status);
CREATE INDEX IF NOT EXISTS ix_etl_pipeline_log_created_at ON pipeline.etl_pipeline_log (created_at);

-- s3_date is nullable: a group first appears from GET /pending's coarse key
-- (rta|arn_code|created_at-date), before we've reserved anything and learned
-- the real S3 partition date. It's filled in once reservation succeeds.
CREATE TABLE IF NOT EXISTS pipeline.etl_report_group_hold (
    group_key               VARCHAR PRIMARY KEY,
    rta                     VARCHAR NOT NULL,
    arn_code                VARCHAR,
    s3_date                 DATE,
    required_report_codes   JSONB NOT NULL,
    members                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    status                  VARCHAR NOT NULL,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_etl_report_group_hold_status ON pipeline.etl_report_group_hold (status);

CREATE TABLE IF NOT EXISTS pipeline.etl_processed_files (
    content_hash    VARCHAR PRIMARY KEY,
    handoff_id      UUID NOT NULL,
    rows_extracted  INTEGER,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Apply it**

Run: `cd python_scripts && source venv/bin/activate && python3 -c "
from utils.db import engine
with open('../sql_scripts/etl_pipeline_schema.sql') as f:
    sql = f.read()
with engine.begin() as conn:
    for statement in sql.split(';'):
        if statement.strip():
            conn.exec_driver_sql(statement)
print('applied')
"`
Expected: prints `applied`, no errors.

- [ ] **Step 3: Write the test**

```python
# python_scripts/tests/test_pipeline_schema.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_pipeline_schema.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add sql_scripts/etl_pipeline_schema.sql python_scripts/tests/test_pipeline_schema.py
git commit -m "feat: add pipeline schema with 3 ETL tracking tables

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `hold_groups.py` — pure R2/R9/R49 grouping logic

**Files:**
- Create: `python_scripts/etl_pipeline/hold_groups.py`
- Test: `python_scripts/tests/etl_pipeline/test_hold_groups.py`
- Create: `python_scripts/tests/etl_pipeline/__init__.py`

**Interfaces:**
- Produces: `REQUIRED_REPORT_GROUPS` (dict), `s3_date_from_uri(uri) -> date`, `coarse_group_key(rta, arn_code, created_at) -> str`, `group_key(rta, arn_code, s3_date) -> str`, `required_report_codes(rta) -> set`, `group_pending_items(pending_items: list[dict]) -> dict[str, dict]`, `ready_handoff_ids(groups) -> list`, `regroup_by_authoritative_key(reserved_items: list[dict]) -> dict[str, dict]`, `is_group_complete(group: dict) -> bool`. No I/O — pure functions, no DB/network.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/etl_pipeline/test_hold_groups.py
import sys
from datetime import date, datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import hold_groups  # noqa: E402


def test_s3_date_from_uri_parses_partition_date():
    uri = "s3://bucket/mailback/org_abc/arn_ARN-266051/2026-08-19/msg_123/processed/WBR2.csv"
    assert hold_groups.s3_date_from_uri(uri) == date(2026, 8, 19)


def test_s3_date_from_uri_raises_on_missing_date():
    try:
        hold_groups.s3_date_from_uri("s3://bucket/no/date/here.csv")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_required_report_codes_cams():
    assert hold_groups.required_report_codes("CAMS") == {"WBR2", "WBR9", "WBR49"}


def test_required_report_codes_kfin():
    assert hold_groups.required_report_codes("KFIN") == {"MFSD201", "MFSD211", "MFSD243"}


def test_required_report_codes_unknown_rta_returns_empty():
    assert hold_groups.required_report_codes("UNKNOWN") == set()


def _pending_item(id_, rta, report_code, arn_code, created_at):
    return {"id": id_, "rta": rta, "report_code": report_code,
            "arn_code": arn_code, "created_at": created_at}


def test_group_pending_items_marks_complete_group_ready():
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [
        _pending_item("h1", "CAMS", "WBR2", "ARN-1", now),
        _pending_item("h2", "CAMS", "WBR9", "ARN-1", now),
        _pending_item("h3", "CAMS", "WBR49", "ARN-1", now),
    ]
    groups = hold_groups.group_pending_items(items)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert group["missing"] == set()
    ready_ids = hold_groups.ready_handoff_ids(groups)
    assert set(ready_ids) == {"h1", "h2", "h3"}


def test_group_pending_items_marks_incomplete_group_missing():
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [
        _pending_item("h1", "CAMS", "WBR2", "ARN-1", now),
        _pending_item("h2", "CAMS", "WBR9", "ARN-1", now),
    ]
    groups = hold_groups.group_pending_items(items)
    group = next(iter(groups.values()))
    assert group["missing"] == {"WBR49"}
    assert hold_groups.ready_handoff_ids(groups) == []


def _reserved_item(handoff_id, rta, report_code, arn_code, s3_date_str, filename):
    return {
        "handoff_id": handoff_id, "rta": rta, "report_code": report_code,
        "arn_code": arn_code, "filename": filename, "payload_format": "csv",
        "content_hash": f"hash-{handoff_id}", "file_size": 100,
        "source_s3_uri": f"s3://bucket/mailback/org_x/arn_{arn_code}/{s3_date_str}/msg_1/processed/{filename}",
    }


def test_regroup_by_authoritative_key_complete_group():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR9", "ARN-1", "2026-08-19", "WBR9.csv"),
        _reserved_item("h3", "CAMS", "WBR49", "ARN-1", "2026-08-19", "WBR49.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert hold_groups.is_group_complete(group)
    assert group["s3_date"] == date(2026, 8, 19)


def test_regroup_by_authoritative_key_splits_on_real_date_mismatch():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR9", "ARN-1", "2026-08-20", "WBR9.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 2
    assert all(not hold_groups.is_group_complete(g) for g in groups.values())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_hold_groups.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.hold_groups'`

- [ ] **Step 3: Create `python_scripts/tests/etl_pipeline/__init__.py`** (empty file)

- [ ] **Step 4: Write `python_scripts/etl_pipeline/hold_groups.py`**

```python
import re
from datetime import date, datetime

REQUIRED_REPORT_GROUPS = {
    "CAMS": {"WBR2", "WBR9", "WBR49"},
    "KFIN": {"MFSD201", "MFSD211", "MFSD243"},
}

_S3_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def s3_date_from_uri(source_s3_uri):
    match = _S3_DATE_RE.search(source_s3_uri)
    if not match:
        raise ValueError(f"No YYYY-MM-DD partition date found in {source_s3_uri!r}")
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def coarse_group_key(rta, arn_code, created_at):
    day = _as_date(created_at)
    return f"{rta}|{arn_code or ''}|{day.isoformat()}"


def group_key(rta, arn_code, s3_date):
    day = s3_date if isinstance(s3_date, date) else s3_date_from_uri(s3_date)
    return f"{rta}|{arn_code or ''}|{day.isoformat()}"


def required_report_codes(rta):
    return REQUIRED_REPORT_GROUPS.get((rta or "").upper(), set())


def group_pending_items(pending_items):
    """
    pending_items: list of dicts shaped like GET /pending's EtlHandoffRead —
    must have 'id', 'rta', 'report_code', 'arn_code', 'created_at'.

    Returns {coarse_group_key: {rta, arn_code, required, present, missing, items}}.
    """
    groups = {}
    for item in pending_items:
        rta = item["rta"]
        arn_code = item.get("arn_code")
        key = coarse_group_key(rta, arn_code, item["created_at"])
        group = groups.setdefault(key, {
            "rta": rta,
            "arn_code": arn_code,
            "required": required_report_codes(rta),
            "present": set(),
            "items": [],
        })
        group["present"].add(item["report_code"])
        group["items"].append(item)

    for group in groups.values():
        group["missing"] = group["required"] - group["present"]

    return groups


def ready_handoff_ids(groups):
    """handoff ids (peek item['id']) belonging to coarse groups with nothing missing."""
    ids = []
    for group in groups.values():
        if group["required"] and not group["missing"]:
            ids.extend(item["id"] for item in group["items"])
    return ids


def regroup_by_authoritative_key(reserved_items):
    """
    reserved_items: list of dicts shaped like POST /reservations' EtlHandoffItem —
    must have 'handoff_id', 'rta', 'report_code', 'arn_code', 'source_s3_uri',
    plus whatever else the caller wants carried through (filename, content_hash, ...).

    Returns {group_key: {rta, arn_code, s3_date, required, members: {report_code: item}}}.
    """
    groups = {}
    for item in reserved_items:
        s3_date = s3_date_from_uri(item["source_s3_uri"])
        key = group_key(item["rta"], item.get("arn_code"), s3_date)
        group = groups.setdefault(key, {
            "rta": item["rta"],
            "arn_code": item.get("arn_code"),
            "s3_date": s3_date,
            "required": required_report_codes(item["rta"]),
            "members": {},
        })
        group["members"][item["report_code"]] = item
    return groups


def is_group_complete(group):
    return bool(group["required"]) and group["required"] <= set(group["members"].keys())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_hold_groups.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add python_scripts/etl_pipeline/hold_groups.py python_scripts/tests/etl_pipeline/
git commit -m "feat: add R2/R9/R49 hold-group logic (pure functions)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `api_client.py` — etl-handoff HTTP client

**Files:**
- Create: `python_scripts/etl_pipeline/api_client.py`
- Test: `python_scripts/tests/etl_pipeline/test_api_client.py`

**Interfaces:**
- Consumes: `etl_pipeline.config` (Task 1).
- Produces: `EtlHandoffClient(base_url=None, email=None, password=None, runner=None)` with methods `peek_pending(limit=200) -> list[dict]`, `reserve(limit=50) -> list[dict]`, `report_outcome(handoff_id, status, rows_extracted=None, failure_reason=None, error_message=None) -> dict` (returns `{"ok": bool, "reason": str|None}`).

- [ ] **Step 1: Write the failing tests** (mocks `requests`, no real network)

```python
# python_scripts/tests/etl_pipeline/test_api_client.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline.api_client import EtlHandoffClient  # noqa: E402


def _client():
    return EtlHandoffClient(
        base_url="http://test/api/v1", email="e@x.com", password="pw", runner="runner-1"
    )


def _resp(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400 and status_code != 409:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


@patch("etl_pipeline.api_client.requests.post")
def test_login_called_lazily_and_token_cached(mock_post):
    mock_post.return_value = _resp(json_body={"data": {"access_token": "tok-1"}})
    client = _client()
    assert client._token is None
    client._login()
    assert client._token == "tok-1"
    mock_post.assert_called_once_with(
        "http://test/api/v1/auth/login",
        json={"email": "e@x.com", "password": "pw"},
        timeout=30,
    )


@patch("etl_pipeline.api_client.requests.request")
@patch("etl_pipeline.api_client.requests.post")
def test_call_relogs_in_on_401(mock_post, mock_request):
    mock_post.return_value = _resp(json_body={"data": {"access_token": "tok-new"}})
    mock_request.side_effect = [_resp(status_code=401), _resp(status_code=200, json_body={"items": []})]

    client = _client()
    client._token = "tok-old"
    response = client._call("GET", "/etl-handoff/pending")

    assert response.status_code == 200
    assert mock_post.call_count == 1
    assert mock_request.call_count == 2


@patch("etl_pipeline.api_client.requests.request")
def test_peek_pending_returns_items(mock_request):
    mock_request.return_value = _resp(json_body=[{"id": "h1", "rta": "CAMS", "report_code": "WBR2"}])
    client = _client()
    client._token = "tok"
    items = client.peek_pending(limit=200)
    assert items == [{"id": "h1", "rta": "CAMS", "report_code": "WBR2"}]


@patch("etl_pipeline.api_client.requests.request")
def test_reserve_returns_items(mock_request):
    mock_request.return_value = _resp(json_body={"items": [{"handoff_id": "h1"}]})
    client = _client()
    client._token = "tok"
    items = client.reserve(limit=10)
    assert items == [{"handoff_id": "h1"}]


@patch("etl_pipeline.api_client.requests.request")
def test_report_outcome_409_returns_reclaimed(mock_request):
    mock_request.return_value = _resp(status_code=409)
    client = _client()
    client._token = "tok"
    result = client.report_outcome("h1", "COMPLETED", rows_extracted=5)
    assert result == {"ok": False, "reason": "reservation_reclaimed"}


@patch("etl_pipeline.api_client.requests.request")
def test_report_outcome_success(mock_request):
    mock_request.return_value = _resp(status_code=200, json_body={"status": "COMPLETED"})
    client = _client()
    client._token = "tok"
    result = client.report_outcome("h1", "COMPLETED", rows_extracted=5)
    assert result == {"ok": True, "reason": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.api_client'`

- [ ] **Step 3: Write `python_scripts/etl_pipeline/api_client.py`**

```python
import requests

from . import config


class EtlHandoffClient:
    def __init__(self, base_url=None, email=None, password=None, runner=None):
        self.base_url = (base_url or config.INTELLIWEALTH_API_BASE).rstrip("/")
        self.email = email or config.INTELLIWEALTH_RUNNER_EMAIL
        self.password = password or config.INTELLIWEALTH_RUNNER_PASSWORD
        self.runner = runner or config.ETL_RUNNER_NAME
        self._token = None

    def _login(self):
        response = requests.post(
            f"{self.base_url}/auth/login",
            json={"email": self.email, "password": self.password},
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["data"]["access_token"]
        return self._token

    def _call(self, method, path, **kwargs):
        if self._token is None:
            self._login()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._token}"
        response = requests.request(
            method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs
        )

        if response.status_code == 401:
            self._login()
            headers["Authorization"] = f"Bearer {self._token}"
            response = requests.request(
                method, f"{self.base_url}{path}", headers=headers, timeout=30, **kwargs
            )

        return response

    def peek_pending(self, limit=200):
        response = self._call("GET", "/etl-handoff/pending", params={"limit": limit})
        response.raise_for_status()
        return response.json()

    def reserve(self, limit=50):
        response = self._call(
            "POST", "/etl-handoff/reservations",
            json={"runner": self.runner, "limit": limit},
        )
        response.raise_for_status()
        return response.json()["items"]

    def report_outcome(self, handoff_id, status, rows_extracted=None,
                        failure_reason=None, error_message=None):
        payload = {"runner": self.runner, "status": status}
        if rows_extracted is not None:
            payload["rows_extracted"] = rows_extracted
        if failure_reason is not None:
            payload["failure_reason"] = failure_reason
        if error_message is not None:
            payload["error_message"] = error_message[:2000]

        response = self._call("PATCH", f"/etl-handoff/{handoff_id}", json=payload)

        if response.status_code == 409:
            return {"ok": False, "reason": "reservation_reclaimed"}

        response.raise_for_status()
        return {"ok": True, "reason": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_api_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/api_client.py python_scripts/tests/etl_pipeline/test_api_client.py
git commit -m "feat: add etl-handoff API client (login/call/peek/reserve/report_outcome)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

**Verify-before-deploy note (not a code placeholder — an operational check):** this task's tests only prove the client's own logic against mocked HTTP. Before pointing this at a real `intelli-wealth-backend` instance, run one live smoke call (`peek_pending(limit=1)`) against a staging deployment to confirm the actual JSON shapes (`/auth/login`'s `data.access_token` path, `/pending`'s bare-array shape) match what was read from source on 2026-08-20 — API response shapes can drift from source between now and deployment.

---

### Task 6: `s3_client.py` — download from `source_s3_uri`

**Files:**
- Create: `python_scripts/etl_pipeline/s3_client.py`
- Test: `python_scripts/tests/etl_pipeline/test_s3_client.py`

**Interfaces:**
- Consumes: `etl_pipeline.config` (Task 1).
- Produces: `parse_s3_uri(uri) -> (bucket, key)`, `download_as_file(source_s3_uri, filename) -> io.BytesIO` (with `.name` set to `filename`, positioned at offset 0) — this is what plugs directly into `raw_ingestion.read_file()` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/etl_pipeline/test_s3_client.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import s3_client  # noqa: E402


def test_parse_s3_uri():
    bucket, key = s3_client.parse_s3_uri("s3://my-bucket/a/b/c.csv")
    assert bucket == "my-bucket"
    assert key == "a/b/c.csv"


def test_parse_s3_uri_rejects_non_s3_scheme():
    try:
        s3_client.parse_s3_uri("https://not-s3/x")
        assert False, "expected ValueError"
    except ValueError:
        pass


@patch("etl_pipeline.s3_client.boto3.client")
def test_download_as_file_returns_named_bytesio(mock_boto_client):
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"a,b\n1,2\n"))}
    mock_boto_client.return_value = mock_s3

    buffer = s3_client.download_as_file("s3://bucket/path/WBR2.csv", "WBR2.csv")

    assert buffer.name == "WBR2.csv"
    assert buffer.read() == b"a,b\n1,2\n"
    mock_s3.get_object.assert_called_once_with(Bucket="bucket", Key="path/WBR2.csv")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_s3_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.s3_client'`

- [ ] **Step 3: Write `python_scripts/etl_pipeline/s3_client.py`**

```python
import re
from io import BytesIO

import boto3

from . import config

_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")


def parse_s3_uri(uri):
    match = _S3_URI_RE.match(uri)
    if not match:
        raise ValueError(f"Not a valid s3:// URI: {uri!r}")
    return match.group(1), match.group(2)


def _client():
    kwargs = {
        "aws_access_key_id": config.AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": config.AWS_SECRET_ACCESS_KEY,
        "region_name": config.AWS_REGION,
    }
    if config.AWS_S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = config.AWS_S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def download_as_file(source_s3_uri, filename):
    bucket, key = parse_s3_uri(source_s3_uri)
    body = _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    buffer = BytesIO(body)
    buffer.name = filename
    buffer.seek(0)
    return buffer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_s3_client.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/s3_client.py python_scripts/tests/etl_pipeline/test_s3_client.py
git commit -m "feat: add S3 download client for etl-handoff source files

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `logging_repo.py` — writer for the `pipeline` tables

**Files:**
- Create: `python_scripts/etl_pipeline/logging_repo.py`
- Test: `python_scripts/tests/etl_pipeline/test_logging_repo.py`

**Interfaces:**
- Consumes: `utils.db.engine` (Task 2), `pipeline.*` tables (Task 3).
- Produces: `new_run_id() -> str`, `log_event(run_id, handoff_id, group_key, rta, arn_code, report_code, filename, source_s3_uri, content_hash, layer, status, total_records_in_report=None, total_processed=None, error_message=None, started_at=None, ended_at=None) -> None`, `upsert_group_hold(group_key, rta, arn_code, s3_date, required_report_codes, members, status) -> None`, `get_groups_with_status(statuses: list[str]) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** (hits the real DB, per repo convention — cleans up after itself)

```python
# python_scripts/tests/etl_pipeline/test_logging_repo.py
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import logging_repo  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(group_key):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline.etl_pipeline_log WHERE group_key = :k"), {"k": group_key}
        )
        conn.execute(
            text("DELETE FROM pipeline.etl_report_group_hold WHERE group_key = :k"), {"k": group_key}
        )


def test_new_run_id_is_a_uuid_string():
    run_id = logging_repo.new_run_id()
    uuid.UUID(run_id)  # raises if not a valid UUID string


def test_log_event_writes_a_row():
    group_key = f"TEST|ARN-1|{uuid.uuid4()}"
    run_id = logging_repo.new_run_id()
    try:
        logging_repo.log_event(
            run_id=run_id, handoff_id=str(uuid.uuid4()), group_key=group_key,
            rta="CAMS", arn_code="ARN-1", report_code="WBR2", filename="WBR2.csv",
            source_s3_uri="s3://bucket/x.csv", content_hash="h1",
            layer="BRONZE", status="COMPLETED", total_processed=10,
        )
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, total_processed FROM pipeline.etl_pipeline_log WHERE group_key = :k"),
                {"k": group_key},
            ).fetchone()
        assert row is not None
        assert row[0] == "COMPLETED"
        assert row[1] == 10
    finally:
        _cleanup(group_key)


def test_upsert_group_hold_insert_then_update():
    group_key = f"TEST|ARN-2|{uuid.uuid4()}"
    try:
        logging_repo.upsert_group_hold(
            group_key=group_key, rta="CAMS", arn_code="ARN-2", s3_date=None,
            required_report_codes=["WBR2", "WBR9", "WBR49"],
            members={"WBR2": {"handoff_id": "h1"}}, status="HOLDING",
        )
        groups = logging_repo.get_groups_with_status(["HOLDING"])
        match = next(g for g in groups if g["group_key"] == group_key)
        assert match["status"] == "HOLDING"
        assert match["members"] == {"WBR2": {"handoff_id": "h1"}}

        logging_repo.upsert_group_hold(
            group_key=group_key, rta="CAMS", arn_code="ARN-2", s3_date="2026-08-19",
            required_report_codes=["WBR2", "WBR9", "WBR49"],
            members={"WBR2": {"handoff_id": "h1"}, "WBR9": {"handoff_id": "h2"}},
            status="READY",
        )
        groups = logging_repo.get_groups_with_status(["READY"])
        match = next(g for g in groups if g["group_key"] == group_key)
        assert match["status"] == "READY"
        assert set(match["members"].keys()) == {"WBR2", "WBR9"}
    finally:
        _cleanup(group_key)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_logging_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.logging_repo'`

- [ ] **Step 3: Write `python_scripts/etl_pipeline/logging_repo.py`**

```python
import json
import uuid

from sqlalchemy import text

from utils.db import engine


def new_run_id():
    return str(uuid.uuid4())


def log_event(run_id, handoff_id, group_key, rta, arn_code, report_code, filename,
              source_s3_uri, content_hash, layer, status,
              total_records_in_report=None, total_processed=None, error_message=None,
              started_at=None, ended_at=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_pipeline_log (
                    log_id, run_id, handoff_id, group_key, rta, arn_code, report_code,
                    filename, source_s3_uri, content_hash, layer, status,
                    total_records_in_report, total_processed, error_message,
                    started_at, ended_at
                ) VALUES (
                    :log_id, :run_id, :handoff_id, :group_key, :rta, :arn_code, :report_code,
                    :filename, :source_s3_uri, :content_hash, :layer, :status,
                    :total_records_in_report, :total_processed, :error_message,
                    :started_at, :ended_at
                )
                """
            ),
            {
                "log_id": str(uuid.uuid4()),
                "run_id": run_id,
                "handoff_id": handoff_id,
                "group_key": group_key,
                "rta": rta,
                "arn_code": arn_code,
                "report_code": report_code,
                "filename": filename,
                "source_s3_uri": source_s3_uri,
                "content_hash": content_hash,
                "layer": layer,
                "status": status,
                "total_records_in_report": total_records_in_report,
                "total_processed": total_processed,
                "error_message": error_message,
                "started_at": started_at,
                "ended_at": ended_at,
            },
        )


def upsert_group_hold(group_key, rta, arn_code, s3_date, required_report_codes, members, status):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_report_group_hold (
                    group_key, rta, arn_code, s3_date, required_report_codes, members, status
                ) VALUES (
                    :group_key, :rta, :arn_code, :s3_date,
                    CAST(:required AS jsonb), CAST(:members AS jsonb), :status
                )
                ON CONFLICT (group_key) DO UPDATE
                SET rta = EXCLUDED.rta,
                    arn_code = EXCLUDED.arn_code,
                    s3_date = COALESCE(EXCLUDED.s3_date, pipeline.etl_report_group_hold.s3_date),
                    required_report_codes = EXCLUDED.required_report_codes,
                    members = EXCLUDED.members,
                    status = EXCLUDED.status,
                    last_updated_at = now(),
                    completed_at = CASE WHEN EXCLUDED.status = 'COMPLETED' THEN now()
                                        ELSE pipeline.etl_report_group_hold.completed_at END
                """
            ),
            {
                "group_key": group_key,
                "rta": rta,
                "arn_code": arn_code,
                "s3_date": s3_date,
                "required": json.dumps(sorted(required_report_codes)),
                "members": json.dumps(members, default=str),
                "status": status,
            },
        )


def get_groups_with_status(statuses):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT group_key, rta, arn_code, s3_date, required_report_codes,
                       members, status, first_seen_at
                FROM pipeline.etl_report_group_hold
                WHERE status = ANY(:statuses)
                """
            ),
            {"statuses": list(statuses)},
        ).mappings().all()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_logging_repo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/logging_repo.py python_scripts/tests/etl_pipeline/test_logging_repo.py
git commit -m "feat: add pipeline log/hold-group DB writer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: `dedup.py` — content_hash idempotency guard

**Files:**
- Create: `python_scripts/etl_pipeline/dedup.py`
- Test: `python_scripts/tests/etl_pipeline/test_dedup.py`

**Interfaces:**
- Consumes: `utils.db.engine`, `pipeline.etl_processed_files` (Task 3).
- Produces: `is_already_processed(content_hash) -> dict|None` (`{"handoff_id": str, "rows_extracted": int}` or `None`), `mark_processed(content_hash, handoff_id, rows_extracted) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/etl_pipeline/test_dedup.py
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import dedup  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(content_hash):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM pipeline.etl_processed_files WHERE content_hash = :h"),
            {"h": content_hash},
        )


def test_is_already_processed_returns_none_for_unknown_hash():
    assert dedup.is_already_processed("no-such-hash") is None


def test_is_already_processed_returns_none_for_empty_hash():
    assert dedup.is_already_processed(None) is None
    assert dedup.is_already_processed("") is None


def test_mark_processed_then_is_already_processed():
    content_hash = f"test-{uuid.uuid4()}"
    handoff_id = str(uuid.uuid4())
    try:
        dedup.mark_processed(content_hash, handoff_id, 42)
        result = dedup.is_already_processed(content_hash)
        assert result == {"handoff_id": handoff_id, "rows_extracted": 42}
    finally:
        _cleanup(content_hash)


def test_mark_processed_is_idempotent_on_reinsert():
    content_hash = f"test-{uuid.uuid4()}"
    handoff_id_1 = str(uuid.uuid4())
    handoff_id_2 = str(uuid.uuid4())
    try:
        dedup.mark_processed(content_hash, handoff_id_1, 10)
        dedup.mark_processed(content_hash, handoff_id_2, 20)
        result = dedup.is_already_processed(content_hash)
        assert result == {"handoff_id": handoff_id_2, "rows_extracted": 20}
    finally:
        _cleanup(content_hash)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_dedup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.dedup'`

- [ ] **Step 3: Write `python_scripts/etl_pipeline/dedup.py`**

```python
from sqlalchemy import text

from utils.db import engine


def is_already_processed(content_hash):
    if not content_hash:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT handoff_id, rows_extracted FROM pipeline.etl_processed_files "
                "WHERE content_hash = :h"
            ),
            {"h": content_hash},
        ).fetchone()
    if row is None:
        return None
    return {"handoff_id": str(row[0]), "rows_extracted": row[1]}


def mark_processed(content_hash, handoff_id, rows_extracted):
    if not content_hash:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline.etl_processed_files (content_hash, handoff_id, rows_extracted)
                VALUES (:h, :hid, :rows)
                ON CONFLICT (content_hash) DO UPDATE
                SET handoff_id = EXCLUDED.handoff_id,
                    rows_extracted = EXCLUDED.rows_extracted,
                    processed_at = now()
                """
            ),
            {"h": content_hash, "hid": handoff_id, "rows": rows_extracted},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_dedup.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/dedup.py python_scripts/tests/etl_pipeline/test_dedup.py
git commit -m "feat: add content_hash idempotency guard

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: `pipeline_lock.py` — single-instance advisory lock

**Files:**
- Create: `python_scripts/etl_pipeline/pipeline_lock.py`
- Test: `python_scripts/tests/etl_pipeline/test_pipeline_lock.py`

**Interfaces:**
- Consumes: `utils.db.engine`.
- Produces: `try_acquire() -> Connection|None`, `release(conn) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/etl_pipeline/test_pipeline_lock.py
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import pipeline_lock  # noqa: E402


def test_acquire_then_second_acquire_fails_until_released():
    conn1 = pipeline_lock.try_acquire()
    assert conn1 is not None

    conn2 = pipeline_lock.try_acquire()
    assert conn2 is None  # already held

    pipeline_lock.release(conn1)

    conn3 = pipeline_lock.try_acquire()
    assert conn3 is not None
    pipeline_lock.release(conn3)


def test_release_of_none_is_a_no_op():
    pipeline_lock.release(None)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_pipeline_lock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_pipeline.pipeline_lock'`

- [ ] **Step 3: Write `python_scripts/etl_pipeline/pipeline_lock.py`**

```python
from sqlalchemy import text

from utils.db import engine

# Arbitrary fixed bigint identifying this pipeline's run lock — must stay
# constant across processes/runs for pg_try_advisory_lock to serialize them.
_LOCK_KEY = 872234561


def try_acquire():
    conn = engine.connect()
    acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}).scalar()
    if not acquired:
        conn.close()
        return None
    return conn


def release(conn):
    if conn is None:
        return
    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
    conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_pipeline_lock.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/pipeline_lock.py python_scripts/tests/etl_pipeline/test_pipeline_lock.py
git commit -m "feat: add postgres advisory lock for single-instance pipeline runs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Gold loaders return real row counts

**Files:**
- Create: `python_scripts/utils/gold_result.py`
- Modify: `python_scripts/etl_gold_amc.py`
- Modify: `python_scripts/etl_gold_scheme.py`
- Modify: `python_scripts/etl_gold_scheme_nav.py`
- Modify: `python_scripts/etl_gold_transaction.py`
- Modify: `python_scripts/etl_gold_holdings.py`
- Modify: `python_scripts/etl_gold_sip.py`
- Modify: `python_scripts/etl_gold_clients.py`
- Modify: `python_scripts/etl_gold_folio_nominees.py`
- Modify: `python_scripts/gold_loader.py`
- Test: `python_scripts/tests/test_gold_result.py`

**Interfaces:**
- Produces: `utils.gold_result.load_result(status, rows_loaded=0, error=None) -> dict` (`{"status": "ok"|"skipped"|"error", "rows_loaded": int, "error": str|None}`). Every `load_*` function in the 8 files now returns this shape instead of `True`/`False`. `gold_loader.load_gold()` now returns `dict[str, dict]` keyed by `"amc"`, `"scheme"`, `"scheme_nav"`, `"transactions"`, `"holdings"`, `"sip"`, `"clients"`, `"folio_nominees"` — every key always present, `load_result("skipped", 0)` for any stage that ran nothing.

- [ ] **Step 1: Write the failing test for `gold_result.py`**

```python
# python_scripts/tests/test_gold_result.py
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from utils.gold_result import load_result  # noqa: E402


def test_load_result_ok():
    assert load_result("ok", 10) == {"status": "ok", "rows_loaded": 10, "error": None}


def test_load_result_defaults():
    assert load_result("skipped") == {"status": "skipped", "rows_loaded": 0, "error": None}


def test_load_result_error():
    r = load_result("error", 0, "boom")
    assert r == {"status": "error", "rows_loaded": 0, "error": "boom"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_result.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.gold_result'`

- [ ] **Step 3: Write `python_scripts/utils/gold_result.py`**

```python
def load_result(status, rows_loaded=0, error=None):
    return {"status": status, "rows_loaded": rows_loaded, "error": error}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_result.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Edit `python_scripts/etl_gold_amc.py`**

Add near its existing imports at the top of the file: `from utils.gold_result import load_result`

Edit (empty-input branch):
```python
# OLD
    if gold_df.empty:

        print(
            "No new AMC records found."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No new AMC records found."
        )

        return load_result("skipped", 0)
```

Edit (success/failure):
```python
# OLD
        print()
        print(
            f"Inserted Rows : {len(gold_df)}"
        )

        print(
            "Silver flag = 0 filter : YES"
        )

        print(
            "Flag inserted into Gold : NO"
        )

        return True


    except Exception:

        print(
            "FAILED LOADING GOLD AMC"
        )

        traceback.print_exc(
            limit=5
        )

        return False

# NEW
        print()
        print(
            f"Inserted Rows : {len(gold_df)}"
        )

        print(
            "Silver flag = 0 filter : YES"
        )

        print(
            "Flag inserted into Gold : NO"
        )

        return load_result("ok", len(gold_df))


    except Exception as e:

        print(
            "FAILED LOADING GOLD AMC"
        )

        traceback.print_exc(
            limit=5
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 6: Edit `python_scripts/etl_gold_scheme_nav.py`**

Add near its existing imports: `from utils.gold_result import load_result`

```python
# OLD
    if gold_df.empty:

        print(
            "No new records found."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No new records found."
        )

        return load_result("skipped", 0)
```

```python
# OLD
        print(
            f"{len(gold_df):,} rows inserted "
            "into gold.scheme_nav."
        )

        return True

    except Exception:

        print(
            "FAILED LOADING GOLD SCHEME NAV"
        )

        traceback.print_exc(
            limit=5
        )

        return False

# NEW
        print(
            f"{len(gold_df):,} rows inserted "
            "into gold.scheme_nav."
        )

        return load_result("ok", len(gold_df))

    except Exception as e:

        print(
            "FAILED LOADING GOLD SCHEME NAV"
        )

        traceback.print_exc(
            limit=5
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 7: Edit `python_scripts/etl_gold_transaction.py`**

Add near its existing imports: `from utils.gold_result import load_result`

```python
# OLD
    if gold_df.empty:

        print(
            "No new records found."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No new records found."
        )

        return load_result("skipped", 0)
```

```python
# OLD
        print(
            f"{len(gold_df)} rows successfully inserted "
            f"into gold.transactions"
        )

        return True

    except Exception:

        print("=" * 80)
        print("FAILED LOADING GOLD TRANSACTIONS")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return False

# NEW
        print(
            f"{len(gold_df)} rows successfully inserted "
            f"into gold.transactions"
        )

        return load_result("ok", len(gold_df))

    except Exception as e:

        print("=" * 80)
        print("FAILED LOADING GOLD TRANSACTIONS")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 8: Edit `python_scripts/etl_gold_sip.py`**

Add near its existing imports: `from utils.gold_result import load_result`

```python
# OLD
    if gold_df.empty:

        print("No SIP rows received.")

        return True

# NEW
    if gold_df.empty:

        print("No SIP rows received.")

        return load_result("skipped", 0)
```

```python
# OLD
        print()
        print("GOLD SIP LOAD SUCCESSFUL")

        return True

    except Exception:

        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return False

# NEW
        print()
        print("GOLD SIP LOAD SUCCESSFUL")

        return load_result("ok", rows_to_insert)

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 9: Edit `python_scripts/etl_gold_holdings.py`**

Add near its existing imports: `from utils.gold_result import load_result`

Three empty-return sites, each `return True` → `return load_result("skipped", 0)`:
1. After `"No holdings to load."`
2. After `"All holdings were removed by the zero-net purchase/switch-out rule."`
3. After `"No new holdings to insert."`

```python
# OLD (site 1)
    if gold_df.empty:

        print(
            "No holdings to load."
        )

        return True

# NEW (site 1)
    if gold_df.empty:

        print(
            "No holdings to load."
        )

        return load_result("skipped", 0)
```

```python
# OLD (site 2)
    if gold_df.empty:

        print()
        print(
            "All holdings were removed by "
            "the zero-net purchase/switch-out rule."
        )

        return True

# NEW (site 2)
    if gold_df.empty:

        print()
        print(
            "All holdings were removed by "
            "the zero-net purchase/switch-out rule."
        )

        return load_result("skipped", 0)
```

```python
# OLD (site 3)
    if gold_df.empty:

        print(
            "No new holdings to insert."
        )

        return True

# NEW (site 3)
    if gold_df.empty:

        print(
            "No new holdings to insert."
        )

        return load_result("skipped", 0)
```

```python
# OLD (success/failure)
        print()
        print(
            "Inserted rows:",
            len(gold_df)
        )

        print(
            "Holdings loaded successfully"
        )

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print(
            "ERROR WHILE LOADING GOLD.HOLDINGS"
        )
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            e
        )

        return False

# NEW
        print()
        print(
            "Inserted rows:",
            len(gold_df)
        )

        print(
            "Holdings loaded successfully"
        )

        return load_result("ok", len(gold_df))

    except Exception as e:

        print()
        print("=" * 80)
        print(
            "ERROR WHILE LOADING GOLD.HOLDINGS"
        )
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            e
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 10: Edit `python_scripts/etl_gold_clients.py`**

Add near its existing imports: `from utils.gold_result import load_result`

```python
# OLD (top guard)
    if gold_df.empty:
        print("No client rows generated.")
        return False

# NEW
    if gold_df.empty:
        print("No client rows generated.")
        return load_result("skipped", 0)
```

```python
# OLD (no valid rows after PAN/dup filtering)
    if gold_df.empty:

        print(
            "No valid client records to load."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No valid client records to load."
        )

        return load_result("skipped", 0)
```

```python
# OLD (all already exist)
    if gold_df.empty:

        print()
        print(
            "All client records already exist in gold.clients."
        )

        count_df = safe_read(
            """
            SELECT COUNT(*) AS total_clients
            FROM gold.clients
            """
        )

        if not count_df.empty:

            print(
                "Current Gold Clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        return True

# NEW
    if gold_df.empty:

        print()
        print(
            "All client records already exist in gold.clients."
        )

        count_df = safe_read(
            """
            SELECT COUNT(*) AS total_clients
            FROM gold.clients
            """
        )

        if not count_df.empty:

            print(
                "Current Gold Clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        return load_result("skipped", 0)
```

```python
# OLD (table not found)
    if table_columns.empty:

        print(
            "ERROR: gold.clients table was not found."
        )

        return False

# NEW
    if table_columns.empty:

        print(
            "ERROR: gold.clients table was not found."
        )

        return load_result("error", 0, "gold.clients table was not found")
```

```python
# OLD (missing columns)
    if missing_database_columns:

        print()
        print(
            "ERROR: These columns exist in the DataFrame "
            "but not in gold.clients:"
        )

        for col in missing_database_columns:
            print(
                " -",
                col
            )

        return False

# NEW
    if missing_database_columns:

        print()
        print(
            "ERROR: These columns exist in the DataFrame "
            "but not in gold.clients:"
        )

        for col in missing_database_columns:
            print(
                " -",
                col
            )

        return load_result(
            "error", 0,
            f"columns missing from gold.clients: {missing_database_columns}"
        )
```

```python
# OLD (final success/failure)
        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT SUCCESSFUL")
        print("=" * 80)

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        # IMPORTANT:
        # Print only the actual error.
        # Do NOT print the complete SQLAlchemy parameter list.

        error_text = str(e)

        print(
            "Database error:"
        )

        print(
            error_text[:3000]
        )

        print()
        print(
            "Rows successfully inserted before failure:",
            inserted_rows
        )

        print(
            "Rows remaining:",
            len(gold_df) - inserted_rows
        )

        print()
        print(
            "The transaction has been rolled back."
        )

        return False

# NEW
        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT SUCCESSFUL")
        print("=" * 80)

        return load_result("ok", inserted_rows)

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        # IMPORTANT:
        # Print only the actual error.
        # Do NOT print the complete SQLAlchemy parameter list.

        error_text = str(e)

        print(
            "Database error:"
        )

        print(
            error_text[:3000]
        )

        print()
        print(
            "Rows successfully inserted before failure:",
            inserted_rows
        )

        print(
            "Rows remaining:",
            len(gold_df) - inserted_rows
        )

        print()
        print(
            "The transaction has been rolled back."
        )

        return load_result("error", inserted_rows, error_text[:2000])
```

- [ ] **Step 11: Edit `python_scripts/etl_gold_scheme.py`**

Add near its existing imports: `from utils.gold_result import load_result`

```python
# OLD (empty input)
    if gold_df.empty:

        print(
            "No scheme records to process."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No scheme records to process."
        )

        return load_result("skipped", 0)
```

```python
# OLD (insert failure)
        except Exception as e:

            print(
                "\nERROR WHILE INSERTING NEW SCHEMES"
            )

            print(
                type(e).__name__
            )

            print(e)

            return False

# NEW
        except Exception as e:

            print(
                "\nERROR WHILE INSERTING NEW SCHEMES"
            )

            print(
                type(e).__name__
            )

            print(e)

            return load_result("error", existing_updates, str(e))
```

```python
# OLD (final return)
    print(
        "New Inserted     :",
        len(new_gold_df)
    )

    return True

# NEW
    print(
        "New Inserted     :",
        len(new_gold_df)
    )

    return load_result("ok", existing_updates + len(new_gold_df))
```

- [ ] **Step 12: Edit `python_scripts/etl_gold_folio_nominees.py`**

Add to the existing imports at the top of the file (after `from utils.db import engine`):
```python
# OLD
from utils.db import engine

# NEW
from utils.db import engine
from utils.gold_result import load_result
```

```python
# OLD (empty input)
    if gold_df.empty:

        print(
            "No new nominee records found."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "No new nominee records found."
        )

        return load_result("skipped", 0)
```

```python
# OLD (all already exist)
    if gold_df.empty:

        print(
            "All nominee records already exist."
        )

        return True

# NEW
    if gold_df.empty:

        print(
            "All nominee records already exist."
        )

        return load_result("skipped", 0)
```

```python
# OLD (duplicate keys found)
    if duplicate_count > 0:

        print(
            "ERROR: Duplicate nominee keys "
            "found before insert."
        )

        return False

# NEW
    if duplicate_count > 0:

        print(
            "ERROR: Duplicate nominee keys "
            "found before insert."
        )

        return load_result("error", 0, "duplicate holding_id+seq keys found before insert")
```

```python
# OLD (final success/failure)
        print(
            f"{len(gold_df)} rows inserted "
            "into Gold Folio Nominees."
        )

        return True

    except Exception:

        print(
            "FAILED LOADING GOLD FOLIO NOMINEES"
        )

        traceback.print_exc(
            limit=5
        )

        return False

# NEW
        print(
            f"{len(gold_df)} rows inserted "
            "into Gold Folio Nominees."
        )

        return load_result("ok", len(gold_df))

    except Exception as e:

        print(
            "FAILED LOADING GOLD FOLIO NOMINEES"
        )

        traceback.print_exc(
            limit=5
        )

        return load_result("error", 0, str(e))
```

- [ ] **Step 13: Rewrite `python_scripts/gold_loader.py`** to aggregate results

```python
# =====================================================
# GOLD LAYER LOADER
# =====================================================

from utils.gold_result import load_result

from etl_gold_amc import (
    extract_amc,
    transform_amc,
    load_amc
)

from etl_gold_scheme import (
    extract_scheme,
    transform_scheme,
    load_scheme
)

from etl_gold_scheme_nav import (
    extract_scheme_nav,
    transform_scheme_nav,
    load_scheme_nav
)

from etl_gold_transaction import (
    extract_transactions,
    transform_transactions,
    load_transactions
)

from etl_gold_holdings import (
    extract_holdings,
    transform_holdings,
    load_holdings
)


try:
    from etl_gold_sip import (
        extract_sip,
        transform_sip,
        load_sip
    )
    SIP_AVAILABLE = True
except ImportError:
    SIP_AVAILABLE = False


try:
    from etl_gold_clients import (
        extract_clients,
        transform_clients,
        load_clients
    )
    CLIENT_AVAILABLE = True
except ImportError:
    CLIENT_AVAILABLE = False


try:
    from etl_gold_folio_nominees import (
        extract_folio_nominees,
        transform_folio_nominees,
        load_folio_nominees
    )
    FOLIO_AVAILABLE = True
except ImportError:
    FOLIO_AVAILABLE = False


def load_gold():

    print("=" * 80)
    print("STARTING GOLD LAYER LOAD")
    print("=" * 80)

    results = {}

    try:
        print("\nLoading Gold AMC")
        amc_df = extract_amc()
        if not amc_df.empty:
            amc_gold_df = transform_amc(amc_df)
            if not amc_gold_df.empty:
                results["amc"] = load_amc(amc_gold_df)
                print("AMC loaded successfully")
            else:
                results["amc"] = load_result("skipped", 0)
        else:
            print("No AMC data found")
            results["amc"] = load_result("skipped", 0)
    except Exception as e:
        print("AMC Gold Failed")
        print(e)
        results["amc"] = load_result("error", 0, str(e))

    try:
        print("\nLoading Gold Scheme")
        transaction_df, investor_df = extract_scheme()
        if not transaction_df.empty or not investor_df.empty:
            scheme_gold_df = transform_scheme(transaction_df, investor_df)
            if not scheme_gold_df.empty:
                results["scheme"] = load_scheme(scheme_gold_df)
                print("Scheme loaded successfully")
            else:
                results["scheme"] = load_result("skipped", 0)
        else:
            print("No Scheme data found")
            results["scheme"] = load_result("skipped", 0)
    except Exception as e:
        print("Scheme Gold Failed")
        print(e)
        results["scheme"] = load_result("error", 0, str(e))

    try:
        print("\nLoading Gold Scheme NAV")
        nav_df = extract_scheme_nav()
        if not nav_df.empty:
            nav_gold_df = transform_scheme_nav(nav_df)
            if not nav_gold_df.empty:
                results["scheme_nav"] = load_scheme_nav(nav_gold_df)
                print("Scheme NAV loaded successfully")
            else:
                results["scheme_nav"] = load_result("skipped", 0)
        else:
            print("No Scheme NAV data found")
            results["scheme_nav"] = load_result("skipped", 0)
    except Exception as e:
        print("Scheme NAV Gold Failed")
        print(e)
        results["scheme_nav"] = load_result("error", 0, str(e))

    try:
        print("\nLoading Gold Transactions")
        transaction_df = extract_transactions()
        if not transaction_df.empty:
            transaction_gold_df = transform_transactions(transaction_df)
            if not transaction_gold_df.empty:
                results["transactions"] = load_transactions(transaction_gold_df)
                print("Transactions loaded successfully")
            else:
                results["transactions"] = load_result("skipped", 0)
        else:
            print("No Transaction data found")
            results["transactions"] = load_result("skipped", 0)
    except Exception as e:
        print("Transaction Gold Failed")
        print(e)
        results["transactions"] = load_result("error", 0, str(e))

    try:
        print("\nLoading Gold Holdings")
        holdings_df = extract_holdings()
        if not holdings_df.empty:
            holdings_gold_df = transform_holdings(holdings_df)
            if not holdings_gold_df.empty:
                results["holdings"] = load_holdings(holdings_gold_df)
                print("Holdings loaded successfully")
            else:
                results["holdings"] = load_result("skipped", 0)
        else:
            print("No Holdings data found")
            results["holdings"] = load_result("skipped", 0)
    except Exception as e:
        print("Holdings Gold Failed")
        print(e)
        results["holdings"] = load_result("error", 0, str(e))

    if SIP_AVAILABLE:
        try:
            print("\nLoading Gold SIP")
            sip_df = extract_sip()
            if not sip_df.empty:
                sip_gold_df = transform_sip(sip_df)
                if not sip_gold_df.empty:
                    results["sip"] = load_sip(sip_gold_df)
                    print("SIP loaded successfully")
                else:
                    results["sip"] = load_result("skipped", 0)
            else:
                print("No SIP data found")
                results["sip"] = load_result("skipped", 0)
        except Exception as e:
            print("SIP Gold Failed")
            print(e)
            results["sip"] = load_result("error", 0, str(e))
    else:
        print("\nGold SIP module not available")
        results["sip"] = load_result("skipped", 0)

    if CLIENT_AVAILABLE:
        try:
            print("\nLoading Gold Clients")
            client_df = extract_clients()
            if not client_df.empty:
                client_gold_df = transform_clients(client_df)
                if not client_gold_df.empty:
                    results["clients"] = load_clients(client_gold_df)
                    print("Clients loaded successfully")
                else:
                    results["clients"] = load_result("skipped", 0)
            else:
                print("No Client data found")
                results["clients"] = load_result("skipped", 0)
        except Exception as e:
            print("Clients Gold Failed")
            print(e)
            results["clients"] = load_result("error", 0, str(e))
    else:
        results["clients"] = load_result("skipped", 0)

    if FOLIO_AVAILABLE:
        try:
            print("\nLoading Gold Folio Nominees")
            folio_df = extract_folio_nominees()
            if not folio_df.empty:
                folio_gold_df = transform_folio_nominees(folio_df)
                if not folio_gold_df.empty:
                    results["folio_nominees"] = load_folio_nominees(folio_gold_df)
                    print("Folio Nominees loaded successfully")
                else:
                    results["folio_nominees"] = load_result("skipped", 0)
            else:
                print("No Folio Nominee data found")
                results["folio_nominees"] = load_result("skipped", 0)
        except Exception as e:
            print("Folio Nominees Gold Failed")
            print(e)
            results["folio_nominees"] = load_result("error", 0, str(e))
    else:
        results["folio_nominees"] = load_result("skipped", 0)

    print("=" * 80)
    print("GOLD LAYER LOAD COMPLETED")
    print("=" * 80)

    return results


if __name__ == "__main__":
    load_gold()
```

- [ ] **Step 14: Run the full existing test suite to confirm this is behavior-neutral**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: same pass/fail counts as before Task 10 (no existing test calls `load_gold()`'s return value today, per the repo's test inventory — this step is the safety net confirming that assumption held).

- [ ] **Step 15: Manually verify `load_gold()`'s new return shape once against the live DB**

Run: `cd python_scripts && source venv/bin/activate && python3 -c "
import gold_loader
result = gold_loader.load_gold()
import json
print(json.dumps(result, indent=2))
"`
Expected: prints a dict with exactly the 8 keys (`amc`, `scheme`, `scheme_nav`, `transactions`, `holdings`, `sip`, `clients`, `folio_nominees`), each `{"status": ..., "rows_loaded": ..., "error": ...}`.

- [ ] **Step 16: Commit**

```bash
git add python_scripts/utils/gold_result.py python_scripts/tests/test_gold_result.py \
        python_scripts/etl_gold_amc.py python_scripts/etl_gold_scheme.py \
        python_scripts/etl_gold_scheme_nav.py python_scripts/etl_gold_transaction.py \
        python_scripts/etl_gold_holdings.py python_scripts/etl_gold_sip.py \
        python_scripts/etl_gold_clients.py python_scripts/etl_gold_folio_nominees.py \
        python_scripts/gold_loader.py
git commit -m "refactor: gold loaders return {status, rows_loaded, error} instead of bool

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: `runner.py` — the orchestrator (cron entry point)

**Files:**
- Create: `python_scripts/etl_pipeline/runner.py`
- Test: `python_scripts/tests/etl_pipeline/test_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 4–10 (`api_client.EtlHandoffClient`, `s3_client.download_as_file`, `hold_groups.*`, `logging_repo.*`, `dedup.*`, `pipeline_lock.*`, `gold_loader.load_gold`, and the existing unmodified `raw_ingestion.read_file`, `etl_trans.process_transactions`, `etl_investor_master.process_investor_master`, `etl_sip.process_sip`).
- Produces: `run_once() -> None` — the sole entry point, called from `if __name__ == "__main__":` for cron.

- [ ] **Step 1: Write `python_scripts/etl_pipeline/runner.py`**

```python
from datetime import datetime, timedelta, timezone

import pandas as pd

from etl_pipeline import config, dedup, hold_groups, logging_repo, pipeline_lock
from etl_pipeline.api_client import EtlHandoffClient
from etl_pipeline.s3_client import download_as_file
from raw_ingestion import read_file
from etl_trans import process_transactions
from etl_investor_master import process_investor_master
from etl_sip import process_sip
import gold_loader


DISPATCH = {
    ("CAMS", "WBR2"): ("transaction", "cams"),
    ("CAMS", "WBR9"): ("investor", "cams"),
    ("CAMS", "WBR49"): ("sip", "cams"),
    ("KFIN", "MFSD211"): ("transaction", "kfin"),
    ("KFIN", "MFSD201"): ("investor", "kfin"),
    ("KFIN", "MFSD243"): ("sip", "kfin"),
}


def run_once():
    lock_conn = pipeline_lock.try_acquire()
    if lock_conn is None:
        print("Another pipeline run is already in progress. Exiting.")
        return

    run_id = logging_repo.new_run_id()

    try:
        client = EtlHandoffClient()
        _discover_and_reserve(client, run_id)
        completed = _run_bronze_for_ready_groups(client, run_id)
        if completed:
            gold_result = gold_loader.load_gold()
            _report_gold_outcomes(client, run_id, completed, gold_result)
        _check_hold_timeouts()
    finally:
        pipeline_lock.release(lock_conn)


def _discover_and_reserve(client, run_id):
    pending = client.peek_pending(limit=config.ETL_PEEK_LIMIT)
    coarse_groups = hold_groups.group_pending_items(pending)
    target_ids = set(hold_groups.ready_handoff_ids(coarse_groups))

    for key, group in coarse_groups.items():
        if group["missing"]:
            members = {
                item["report_code"]: {"handoff_id": item["id"], "bronze_done": False}
                for item in group["items"]
            }
            logging_repo.upsert_group_hold(
                key, group["rta"], group["arn_code"], None,
                group["required"], members, "HOLDING",
            )
            for item in group["items"]:
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["id"], group_key=key,
                    rta=item["rta"], arn_code=item.get("arn_code"),
                    report_code=item["report_code"], filename=None,
                    source_s3_uri=None, content_hash=None,
                    layer="HOLD", status="HOLDING",
                )

    if not target_ids:
        return

    reserved, calls, seen = [], 0, set()
    while (target_ids - seen) and calls < config.ETL_MAX_RESERVATION_CALLS_PER_RUN:
        batch = client.reserve(limit=config.ETL_BATCH_LIMIT)
        calls += 1
        if not batch:
            break
        for item in batch:
            seen.add(item["handoff_id"])
        reserved.extend(batch)

    real_groups = hold_groups.regroup_by_authoritative_key(reserved)
    for key, group in real_groups.items():
        members = {
            code: {**item, "bronze_done": False}
            for code, item in group["members"].items()
        }
        status = "READY" if hold_groups.is_group_complete(group) else "HOLDING"
        logging_repo.upsert_group_hold(
            key, group["rta"], group["arn_code"], group["s3_date"],
            group["required"], members, status,
        )


def _run_bronze_for_ready_groups(client, run_id):
    completed = []

    for hold in logging_repo.get_groups_with_status(["READY", "PROCESSING"]):
        key = hold["group_key"]
        members = hold["members"]
        required = set(hold["required_report_codes"])

        if not required <= set(members.keys()):
            continue  # still incomplete, left as-is for a later run

        files_by_type = {}
        group_failed = False

        for report_code, item in list(members.items()):
            if item.get("bronze_done"):
                continue

            dup = dedup.is_already_processed(item.get("content_hash"))
            if dup is not None:
                item["bronze_done"] = True
                item["rows"] = dup["rows_extracted"]
                item["skipped_duplicate"] = True
                continue

            dtype, vendor = DISPATCH[(hold["rta"], report_code)]
            started = datetime.now(timezone.utc)
            try:
                buffer = download_as_file(item["source_s3_uri"], item["filename"])
                df = read_file(buffer)
            except Exception as exc:
                client.report_outcome(
                    item["handoff_id"], "FAILED",
                    failure_reason="DOWNLOAD_ERROR", error_message=str(exc)[:2000],
                )
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                    rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                    filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                    content_hash=item.get("content_hash"), layer="BRONZE", status="FAILED",
                    error_message=str(exc)[:2000], started_at=started,
                    ended_at=datetime.now(timezone.utc),
                )
                group_failed = True
                del members[report_code]
                continue

            files_by_type.setdefault(dtype, {}).setdefault(vendor, []).append(df)
            item["_rows"] = len(df)
            item["_started_at"] = started
            item["_dtype"] = dtype

        if group_failed:
            logging_repo.upsert_group_hold(
                key, hold["rta"], hold["arn_code"], hold["s3_date"],
                hold["required_report_codes"], members, "HOLDING",
            )
            continue

        bronze_counts = _load_bronze(files_by_type)
        ended = datetime.now(timezone.utc)

        for report_code, item in members.items():
            if item.get("skipped_duplicate"):
                logging_repo.log_event(
                    run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                    rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                    filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                    content_hash=item.get("content_hash"), layer="BRONZE",
                    status="SKIPPED_DUPLICATE", total_processed=item.get("rows"),
                )
                continue

            if "_rows" not in item:
                continue  # was already bronze_done from an earlier run

            item["bronze_done"] = True
            rows = item.pop("_rows")
            item["rows"] = rows
            dtype = item.pop("_dtype")
            logging_repo.log_event(
                run_id=run_id, handoff_id=item["handoff_id"], group_key=key,
                rta=hold["rta"], arn_code=hold["arn_code"], report_code=report_code,
                filename=item.get("filename"), source_s3_uri=item.get("source_s3_uri"),
                content_hash=item.get("content_hash"), layer="BRONZE", status="COMPLETED",
                total_records_in_report=rows, total_processed=bronze_counts.get(dtype, 0),
                started_at=item.pop("_started_at", None), ended_at=ended,
            )

        logging_repo.upsert_group_hold(
            key, hold["rta"], hold["arn_code"], hold["s3_date"],
            hold["required_report_codes"], members, "PROCESSING",
        )
        completed.append((key, hold["rta"], hold["arn_code"], members))

    return completed


def _load_bronze(files_by_type):
    counts = {}
    for dtype, by_vendor in files_by_type.items():
        cams_df = pd.concat(by_vendor["cams"], ignore_index=True) if by_vendor.get("cams") else None
        kfin_df = pd.concat(by_vendor["kfin"], ignore_index=True) if by_vendor.get("kfin") else None
        rows = (len(cams_df) if cams_df is not None else 0) + (len(kfin_df) if kfin_df is not None else 0)
        if dtype == "transaction":
            process_transactions(cams=cams_df, kfin=kfin_df)
        elif dtype == "investor":
            process_investor_master(cams=cams_df, kfin=kfin_df)
        elif dtype == "sip":
            process_sip(cams=cams_df, kfin=kfin_df)
        counts[dtype] = rows
    return counts


def _report_gold_outcomes(client, run_id, completed, gold_result):
    gold_total_rows = sum(r["rows_loaded"] for r in gold_result.values())
    gold_errors = [f"{k}: {r['error']}" for k, r in gold_result.items() if r["status"] == "error"]
    gold_status = "FAILED" if gold_errors else "COMPLETED"
    gold_error_message = "; ".join(gold_errors)[:2000] if gold_errors else None

    for key, rta, arn_code, members in completed:
        real_members = {c: i for c, i in members.items() if not i.get("skipped_duplicate")}
        if real_members:
            primary = next(iter(real_members.values()))
            logging_repo.log_event(
                run_id=run_id, handoff_id=primary["handoff_id"], group_key=key,
                rta=rta, arn_code=arn_code, report_code="GROUP",
                filename=None, source_s3_uri=None, content_hash=None,
                layer="GOLD", status=gold_status,
                total_processed=gold_total_rows, error_message=gold_error_message,
            )

        for report_code, item in members.items():
            if item.get("skipped_duplicate"):
                continue
            if gold_status == "COMPLETED":
                client.report_outcome(item["handoff_id"], "COMPLETED", rows_extracted=item.get("rows", 0))
                dedup.mark_processed(item.get("content_hash"), item["handoff_id"], item.get("rows", 0))
            else:
                client.report_outcome(
                    item["handoff_id"], "FAILED",
                    failure_reason="GOLD_LOAD_ERROR", error_message=gold_error_message,
                )

        logging_repo.upsert_group_hold(
            key, rta, arn_code, None, list(members.keys()), members,
            "COMPLETED" if gold_status == "COMPLETED" else "HOLDING",
        )


def _check_hold_timeouts():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=config.ETL_HOLD_TIMEOUT_MINUTES)
    for hold in logging_repo.get_groups_with_status(["HOLDING"]):
        first_seen = hold["first_seen_at"]
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        if first_seen < cutoff:
            logging_repo.upsert_group_hold(
                hold["group_key"], hold["rta"], hold["arn_code"], hold["s3_date"],
                hold["required_report_codes"], hold["members"], "TIMED_OUT",
            )


if __name__ == "__main__":
    run_once()
```

- [ ] **Step 2: Write the integration test** (mocks the API and S3 boundary only; everything else — DB, bronze functions — is real, per repo convention)

```python
# python_scripts/tests/etl_pipeline/test_runner.py
import sys
import uuid
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from sqlalchemy import text  # noqa: E402
from etl_pipeline import runner  # noqa: E402
from utils.db import engine  # noqa: E402


def _cleanup(group_key):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM pipeline.etl_pipeline_log WHERE group_key = :k"), {"k": group_key})
        conn.execute(text("DELETE FROM pipeline.etl_report_group_hold WHERE group_key = :k"), {"k": group_key})


@patch("etl_pipeline.runner.gold_loader")
@patch("etl_pipeline.runner.download_as_file")
@patch("etl_pipeline.runner.EtlHandoffClient")
def test_run_once_holds_incomplete_group_and_logs_it(mock_client_cls, mock_download, mock_gold):
    arn_code = f"ARN-TEST-{uuid.uuid4()}"
    now = "2026-08-19T10:00:00Z"

    mock_client = MagicMock()
    mock_client.peek_pending.return_value = [
        {"id": "h1", "rta": "CAMS", "report_code": "WBR2", "arn_code": arn_code, "created_at": now},
        {"id": "h2", "rta": "CAMS", "report_code": "WBR9", "arn_code": arn_code, "created_at": now},
        # WBR49 missing — group must be held, not reserved
    ]
    mock_client.reserve.return_value = []
    mock_client_cls.return_value = mock_client

    group_key = f"CAMS|{arn_code}|2026-08-19"
    try:
        runner.run_once()

        mock_client.report_outcome.assert_not_called()
        mock_download.assert_not_called()

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM pipeline.etl_report_group_hold "
                    "WHERE rta = 'CAMS' AND arn_code = :arn"
                ),
                {"arn": arn_code},
            ).fetchone()
        assert row is not None
        assert row[0] == "HOLDING"
    finally:
        _cleanup(group_key)
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM pipeline.etl_report_group_hold WHERE arn_code = :arn"),
                {"arn": arn_code},
            )
```

- [ ] **Step 3: Run the test to verify it fails, then passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_runner.py -v`
Expected first (before Step 1's file exists): FAIL — module not found.
After Step 1 is in place: PASS (1 test). If it fails on the DB assertions, inspect `pipeline.etl_report_group_hold` directly (`SELECT * FROM pipeline.etl_report_group_hold WHERE arn_code = '<arn_code from the failed test output>'`) to see what state actually landed, and adjust `runner.py`'s `_discover_and_reserve` accordingly — this is the one step in this plan most likely to need a real debugging pass since it's the first place every earlier module's contract gets exercised together.

- [ ] **Step 4: Manual smoke run against a real (or staging) etl-handoff API**

Run: `cd python_scripts && source venv/bin/activate && python3 -m etl_pipeline.runner`
Expected: exits cleanly (0 or nothing pending is a valid, successful outcome); check `pipeline.etl_pipeline_log` and `pipeline.etl_report_group_hold` afterward for rows matching whatever was actually pending in the target `intelli-wealth-backend` instance at run time.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/etl_pipeline/runner.py python_scripts/tests/etl_pipeline/test_runner.py
git commit -m "feat: add pipeline runner — reserve, hold, process, report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Cron deployment doc

**Files:**
- Create: `python_scripts/etl_pipeline/README.md`

- [ ] **Step 1: Write the deployment doc**

```markdown
# etl_pipeline — automated ETL handoff worker

## One-time setup

1. `cd python_scripts && source venv/bin/activate && pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in every value (DB creds, AWS creds,
   `INTELLIWEALTH_API_BASE`, `INTELLIWEALTH_RUNNER_EMAIL`/`PASSWORD` — see
   the backend team for the seeded `ETL_RUNNER` service account credentials).
3. Apply the schema once: see `sql_scripts/etl_pipeline_schema.sql` (idempotent,
   safe to re-run) — same instructions as Task 3 Step 2 of the implementation plan.

## Running once, manually

```
cd python_scripts && source venv/bin/activate && python3 -m etl_pipeline.runner
```

## Cron

Example crontab entry — **the interval below is an example, not a fixed
requirement**; tune `*/15` to whatever cadence ops wants without touching any
code (nothing about cadence lives in this repo):

```
*/15 * * * * cd /path/to/intelliwealth_layer_old_code/python_scripts && \
  venv/bin/python3 -m etl_pipeline.runner >> /var/log/etl_pipeline.log 2>&1
```

Overlapping runs are safe — `pipeline_lock.py`'s Postgres advisory lock makes
a second concurrent invocation exit immediately rather than double-process.

## Observability

- `pipeline.etl_pipeline_log` — one row per (file, layer) event: HOLD, BRONZE,
  GOLD (GOLD is logged per-group, not per-file — see "Known simplification"
  in the implementation plan).
- `pipeline.etl_report_group_hold` — current state of every distributor+date
  group: `HOLDING` (waiting on siblings), `READY`/`PROCESSING`, `COMPLETED`,
  `TIMED_OUT` (waiting longer than `ETL_HOLD_TIMEOUT_MINUTES` — needs a human
  to check why a sibling report never arrived).
- Query stuck groups: `SELECT * FROM pipeline.etl_report_group_hold WHERE status IN ('HOLDING', 'TIMED_OUT') ORDER BY first_seen_at;`
```

- [ ] **Step 2: Commit**

```bash
git add python_scripts/etl_pipeline/README.md
git commit -m "docs: add etl_pipeline deployment/cron README

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-review notes (per writing-plans checklist)

- **Spec coverage:** §2 (contract) → Tasks 5,11. §3 (components) → Tasks 4–11. §4 (data model) → Task 3. §5 (hold logic incl. errata) → Tasks 4, 11. §6 (dedup) → Task 8. §7.1 (row counts) → Task 10. §8 (error handling) → Task 11. §9 (concurrency/lock) → Task 9. §10 (config) → Task 1. §11 (testing) → every task's test step. §7.2–§7.4 (ON CONFLICT hardening, drop_duplicates, folio_nominees ARN) → explicitly deferred to **Part B**, not silently dropped.
- **Placeholder scan:** no TBD/TODO left in any code block; the two "Verify-before-deploy"/"manual smoke run" notes (Task 5, Task 11) are operational guidance about external-system drift, not unfinished code — every function they refer to is fully implemented in the same task.
- **Type consistency:** `load_result()`'s `{"status", "rows_loaded", "error"}` shape is identical everywhere it's produced (Task 10) and consumed (Task 11's `_report_gold_outcomes`). `hold_groups`' `group["members"]` dict (`report_code -> item`) is the same shape Task 11 reads/writes through `logging_repo.upsert_group_hold`. `EtlHandoffClient.report_outcome`'s return shape (`{"ok", "reason"}`) matches how Task 11 could check it (not currently branched on — noted below).

**One follow-up worth flagging, not blocking:** `runner.py`'s `_report_gold_outcomes` calls `client.report_outcome(...)` but doesn't inspect its `{"ok": False, "reason": "reservation_reclaimed"}` return — per spec §8, a 409 should be logged as INFO and dropped, not retried, but right now it's simply not acted on either way (harmless — the PATCH attempt itself is idempotent-safe since a 409 changes nothing server-side — but a log line would help ops). Cheap to add in review; not required for correctness.
