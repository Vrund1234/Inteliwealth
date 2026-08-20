# Automated ETL Handoff Pipeline — Design Spec

**Date:** 2026-08-19
**Repo:** `intelliwealth_layer_old_code` (this repo — the "DE side")
**Reference repo (API contract + style only, not modified here):** `intelli-wealth-backend`
**Status:** Draft for review

## 1. Goal

Replace manual/Streamlit file upload with a cron-driven worker that:

1. Claims files from `intelli-wealth-backend`'s `/etl-handoff` queue.
2. Downloads each file from its `source_s3_uri` with our own AWS credentials.
3. Holds CAMS `WBR2`/`WBR9`/`WBR49` (and the KFIN equivalent `MFSD201`/`MFSD211`/`MFSD243`) files for the **same distributor (arn_code) + same date** until all three have arrived, then processes them together through the existing bronze → silver → gold functions.
4. Reports outcome back via `PATCH /etl-handoff/{handoff_id}`.
5. Logs everything — per file, per layer (bronze/silver/gold), per hold state — to a new tracking table, so a cron run's full history is auditable.
6. Runs safely and quickly on a fixed interval with no duplicate processing, single-instance-safe, ready to scale later.

Backend-side work (ETL_RUNNER service account + seeder, IP allowlist, `report_outcome()`→gold-sync wiring) is **out of scope** — already owned by the backend team in `intelli-wealth-backend`. This spec only documents the exact env/contract this pipeline depends on there.

## 2. Confirmed API contract (from `intelli-wealth-backend`)

- `POST /api/v1/etl-handoff/reservations` `{"runner", "limit" (1-50)}` → reserves up to `limit` oldest claimable rows (`FOR UPDATE SKIP LOCKED`), sets `status=RESERVED`, **increments `attempt_count` at reservation time** (not at outcome time).
- `GET /api/v1/etl-handoff/pending?limit=1-200` → **non-mutating peek** at claimable rows, no reservation. This is the mechanism the hold logic uses to look ahead without burning attempts.
- `PATCH /api/v1/etl-handoff/{handoff_id}` `{"runner", "status": COMPLETED|FAILED, "rows_extracted"?, "failure_reason"?, "error_message"?}` — 404 unknown id, 409 if not `RESERVED` or `reserved_by != runner` (treat 409 as "another runner already reclaimed it, drop this attempt silently, do not retry the PATCH"). `COMPLETED`/`ABANDONED`/`SKIPPED` are terminal. `FAILED` re-enters the queue until `attempt_count >= max_attempts` (3), then auto-`ABANDONED`.
- Reservation TTL default 60 min: a `RESERVED` row past TTL becomes claimable again (self-healing, but re-reservation increments `attempt_count` again — see §5.3 for why this matters to the hold logic).
- Auth: JWT bearer via `POST /auth/login`, using the `ETL_RUNNER_EMAIL`/`ETL_RUNNER_PASSWORD` service account seeded on the backend (per the agreed design — `login()` + `call()` wrapper with 401-triggered re-login).
- `source_s3_uri` format is deterministic: `s3://{bucket}/{mailback|test/mailback}/org_{org}/arn_{arn}/{YYYY-MM-DD}/msg_{id}/{artifact_type}/{filename}` — the date segment is what we parse for grouping (§5.1).
- Report codes are exactly `WBR2`, `WBR9`, `WBR49` (CAMS) and `MFSD201`, `MFSD211`, `MFSD243` (KFIN) — filename is `report_code + "." + extension` verbatim.

## 3. Components

New package `python_scripts/etl_pipeline/`:

| File | Responsibility |
|---|---|
| `config.py` | env-based config (new pattern for this repo) — DB creds, AWS creds, API base URL, runner credentials/name, batch limit, hold timeout. `.env` + `.env.example`, loaded via `python-dotenv`. |
| `api_client.py` | `login()` / `call()` wrapper (per agreed backend design) for `GET /pending`, `POST /reservations`, `PATCH /{id}`. |
| `s3_client.py` | Parses `s3://bucket/key`, downloads via boto3 with our own creds, returns a `BytesIO` with `.name` set — plugs directly into the existing `raw_ingestion.read_file()` unchanged. |
| `hold_groups.py` | R2/R9/R49 (and KFIN equivalent) grouping + hold/ready decision (§5). |
| `dedup.py` | `content_hash` idempotency check against `etl_processed_files` (§6). |
| `pipeline_lock.py` | Single-instance advisory lock (Postgres `pg_advisory_lock`) — recreates the deleted utility `tests/test_pipeline_lock.py` already expects. |
| `logging_repo.py` | Writes to `pipeline.etl_pipeline_log` / `pipeline.etl_report_group_hold` (§4). |
| `runner.py` | Entry point (`if __name__ == "__main__":`) — orchestrates one batch end-to-end, invoked by cron. |

Existing files touched (mechanical, no behavior change to what data gets loaded):

- `python_scripts/utils/db.py` — credentials moved to env, read through `config.py`.
- `python_scripts/gold_loader.py` + each `etl_gold_*.py` `load_*()` — return `{"rows_loaded": int, "status": "ok"|"skipped"|"error", "error": str|None}` instead of `print()` + swallow, so the pipeline can log real row counts and report accurate `rows_extracted` (§7.1).
- `python_scripts/etl_trans.py` — re-enable the commented-out `drop_duplicates()` block (§7.2).
- `python_scripts/etl_gold_folio_nominees.py` — add `arn` to the gold output (§7.3).

## 4. Data model

New schema `pipeline` (kept separate from `bronze`/`silver`/`gold` data schemas since this is operational metadata, not report data).

### 4.1 `pipeline.etl_pipeline_log` — one row per (file, layer, or hold event)

```sql
CREATE TABLE pipeline.etl_pipeline_log (
    log_id                  UUID PRIMARY KEY,
    run_id                  UUID NOT NULL,           -- groups every row from one cron invocation
    handoff_id              UUID,                    -- null only never — populated from first peek
    group_key               VARCHAR NOT NULL,        -- rta|arn_code|s3_date, links R2/R9/R49 siblings
    rta                     VARCHAR NOT NULL,
    arn_code                VARCHAR,                  -- distributor ARN number
    report_code             VARCHAR NOT NULL,         -- WBR2 / WBR9 / WBR49 / MFSD...
    filename                VARCHAR NOT NULL,
    source_s3_uri           VARCHAR NOT NULL,
    content_hash            VARCHAR,
    layer                   VARCHAR NOT NULL,         -- HOLD | BRONZE | SILVER | GOLD
    status                  VARCHAR NOT NULL,         -- HOLDING | PROCESSING | COMPLETED | FAILED | SKIPPED_DUPLICATE | HOLD_TIMEOUT
    total_records_in_report INTEGER,                  -- rows read from the source file
    total_processed         INTEGER,                  -- rows written at this layer
    error_message           VARCHAR,
    started_at              TIMESTAMPTZ,
    ended_at                TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_etl_pipeline_log_group_key ON pipeline.etl_pipeline_log (group_key);
CREATE INDEX ix_etl_pipeline_log_handoff_id ON pipeline.etl_pipeline_log (handoff_id);
CREATE INDEX ix_etl_pipeline_log_status ON pipeline.etl_pipeline_log (status);
CREATE INDEX ix_etl_pipeline_log_created_at ON pipeline.etl_pipeline_log (created_at);
```

One file that's held and then processed through 3 layers produces 4 rows over time (HOLD → BRONZE → SILVER → GOLD), each with its own start/end timestamp and row count — giving the exact layer-wise audit trail asked for.

### 4.2 `pipeline.etl_report_group_hold` — cross-run memory of what's waiting on siblings

```sql
CREATE TABLE pipeline.etl_report_group_hold (
    group_key               VARCHAR PRIMARY KEY,      -- rta|arn_code|s3_date
    rta                     VARCHAR NOT NULL,
    arn_code                VARCHAR,
    s3_date                 DATE NOT NULL,
    required_report_codes   JSONB NOT NULL,            -- e.g. ["WBR2","WBR9","WBR49"]
    members                 JSONB NOT NULL DEFAULT '{}', -- report_code -> {handoff_id, source_s3_uri, filename, content_hash, reserved_at}
    status                  VARCHAR NOT NULL,           -- HOLDING | READY | PROCESSING | COMPLETED | TIMED_OUT
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ
);
```

This is what lets a later cron run know "I already hold WBR2 and WBR9 for this distributor+date, still waiting on WBR49" without re-deriving it from `/pending` (which stops showing an item the moment it's reserved).

### 4.3 `pipeline.etl_processed_files` — idempotency guard

```sql
CREATE TABLE pipeline.etl_processed_files (
    content_hash    VARCHAR PRIMARY KEY,
    handoff_id      UUID NOT NULL,
    rows_extracted  INTEGER,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 5. Hold / group logic — R2 + R9 + R49 together

### 5.1 Grouping key

**Errata (2026-08-20, confirmed against `intelli-wealth-backend/app/modules/etl_handoff/router.py`):** `GET /pending` returns `list[EtlHandoffRead]`, not `EtlHandoffItem` — it has `rta`, `report_code`, `arn_code`, `created_at`, and `id` (not `handoff_id`), but **no `source_s3_uri`**. Only `POST /reservations`'s `EtlHandoffItem` carries `source_s3_uri`. So grouping is necessarily two-stage:

- **Stage 1 (peek, coarse key):** `group_key_coarse = f"{rta}|{arn_code}|{created_at.date()}"` — `created_at` is the enqueue timestamp, a proxy for the S3 partition date (they coincide except in the rare case processing crosses midnight between enqueue and the mailback pipeline's `partition_date`). Used only to decide which groups look complete enough to attempt reserving.
- **Stage 2 (post-reservation, authoritative key):** once reserved, `EtlHandoffItem.source_s3_uri` is available; `group_key = f"{rta}|{arn_code}|{s3_date}"` with `s3_date` parsed via regex (`\d{4}-\d{2}-\d{2}`) out of the URI. This is the key actually used for `etl_report_group_hold`/`etl_pipeline_log`, and for the final "is this group really complete" decision. If a member's true `s3_date` disagrees with its coarse-stage group (rare skew), it's re-filed under its real `group_key` and held there instead — never processed under the wrong grouping.

Required-set-per-RTA is a config dict, not hardcoded inline:
```python
REQUIRED_REPORT_GROUPS = {
    "CAMS": {"WBR2", "WBR9", "WBR49"},
    "KFIN": {"MFSD201", "MFSD211", "MFSD243"},
}
```
**Assumption to confirm:** you asked specifically about R2/R9/R49 (CAMS); I've generalized the same "hold until complete" rule to KFIN's equivalent trio since the existing `raw_ingestion.py` dispatch already treats those three as siblings of the same three data types (investor/transaction/sip). If KFIN files should NOT be held (process each independently), tell me and I'll drop that entry — it's a one-line config change either way.

### 5.2 Per-run flow

```
1. Acquire pipeline_lock (Postgres advisory lock). If already held → log "skipped: prior run still active", exit.
2. login() (cached token, 401 triggers re-login).
3. peek = GET /pending?limit=200 — each item's `id` field is treated as its `handoff_id`.
4. Group `peek` items by group_key_coarse (`rta|arn_code|created_at.date()`, §5.1). For each
   coarse group, required - present = missing.
     - missing empty  → mark group READY (target reservation set)
     - missing non-empty → upsert etl_report_group_hold (status=HOLDING, merge members),
                             write a HOLDING row per file to etl_pipeline_log, do nothing else.
5. Also load any group already HOLDING in etl_report_group_hold from a PRIOR run whose
   members are all now accounted for (some members reserved earlier, last one just
   appeared in `peek`) → also READY.
6. Reserve: POST /reservations in a loop (limit=50 per call, repo A's max), accumulating
   results, until either (a) every handoff_id in the READY target set has been reserved,
   or (b) a call returns fewer than `limit` items (queue drained), or (c) a safety cap of
   5 calls/run is hit (bounds worst-case work per tick — logged if hit).
7. For each reserved item (now an `EtlHandoffItem` with `source_s3_uri`):
     - compute the authoritative `group_key` from its real `s3_date` (§5.1 stage 2) — may
       differ from the coarse key that got it reserved; file it under the real one.
     - dedup check by content_hash (§6) — if already processed, PATCH COMPLETED immediately
       with the prior rows_extracted, log SKIPPED_DUPLICATE, done.
     - else update etl_report_group_hold.members for its (authoritative) group_key.
8. For every group now fully reserved (all members present in `members` with a handoff_id):
     - status → PROCESSING
     - download all member files (§ s3_client), run bronze → silver → gold together (§5.3),
       writing BRONZE/SILVER/GOLD rows to etl_pipeline_log per file
     - PATCH each member handoff_id COMPLETED with its own rows_extracted (or FAILED — §8)
     - group status → COMPLETED, completed_at = now()
9. For groups still incomplete after this run (including incidental reservations — see
   Known Limitation below): leave them RESERVED/un-PATCHed, status stays HOLDING in
   etl_report_group_hold; a later run picks them up via step 5.
10. If a HOLDING group's first_seen_at is older than ETL_HOLD_TIMEOUT_MINUTES (env,
    default suggestion: 240 min / 4 hours — configurable, not hardcoded): mark
    status=HOLD_TIMEOUT in our log for visibility/alerting. Do NOT proactively PATCH
    FAILED on the arrived members — that would burn one of their 3 attempts for a
    problem that isn't the file's fault (its siblings just haven't arrived). Let repo
    A's own 60-min reservation TTL reclaim-and-recycle naturally; HOLD_TIMEOUT is a
    signal for ops to chase the RTA/distributor, not an auto-failure.
11. Release pipeline_lock.
```

### 5.3 Known limitation — no ID-targeted reservation

The contract's `POST /reservations` only accepts a `limit`, not explicit handoff_ids — it reserves oldest-N claimable rows FIFO. If an unrelated old singleton file (whose own siblings haven't arrived) sits ahead of our target trio in the queue, draining up to and including our target's position will incidentally reserve it too, consuming one of its `attempt_count`. Mitigation: size each `POST /reservations` call using the target group's position in the `peek` ordering (both endpoints share the same `created_at ASC` order) rather than always maxing out `limit`, so incidental reservations stay rare; and treat any incidental reservation exactly like a normal HOLDING member — tracked in `etl_report_group_hold`, safe to leave un-PATCHed across runs, self-heals via TTL if truly stuck. This is a real tradeoff of the given API contract, not a bug — flagging it explicitly rather than hiding it.

## 6. Idempotency / dedup

- Before processing a reserved file: check `pipeline.etl_processed_files` by `content_hash`. Hit → skip reprocessing, PATCH `COMPLETED` with the stored `rows_extracted`, log `SKIPPED_DUPLICATE`. This covers the case where a worker crashes after committing bronze/silver/gold writes but before the `PATCH` lands (at-least-once delivery from our side), and the case where the same content is resent under a new `handoff_id`.
- After a group completes successfully, insert one `etl_processed_files` row per member `content_hash`.
- Repo A already prevents duplicate *active* enqueues of identical content per org (`uq_etl_handoff_active_content`), so this table is specifically about protecting *our* write path, not the queue itself.

## 7. Idempotency hardening in the existing gold layer

Required so concurrent/repeated processing can never double-write, and so `rows_extracted` reported to the API is real:

### 7.1 Row-count-based success (`gold_loader.py`, all `etl_gold_*.py`)
Each `load_*()` returns `{"rows_loaded": int, "status": "ok"|"skipped"|"error", "error": str|None}` instead of `print()`-and-swallow. `load_gold()` aggregates these into one result dict. The pipeline uses this to populate `total_processed` in `etl_pipeline_log` (GOLD layer) and `rows_extracted` in the `PATCH` call — real numbers, not inferred from "no exception was raised."

### 7.2 `ON CONFLICT` / unique keys on gold loaders
Add the natural-key unique constraints each gold table is missing (e.g. `(scheme_code, amc_code)`, `(folio_no, arn_code)` etc. — enumerated per-table in the implementation plan) and switch each `load_*()` insert to `ON CONFLICT ... DO UPDATE`/`DO NOTHING` as appropriate. This is what allows safely re-running gold load for the same distributor+date (retry after partial failure, or a stale-reservation reprocessing) without creating duplicate gold rows — the actual mechanism behind "no duplication."

### 7.3 Re-enable `drop_duplicates()` in `etl_trans.py:782`
Currently commented out — re-enable exact-duplicate-row removal before the final column-order step, matching the surrounding code's already-written intent.

### 7.4 Add `arn` to `gold.folio_nominees`
Currently absent from that table's output entirely; add it (joined the same way `etl_gold_holdings.py`/`etl_gold_clients.py`/`etl_gold_sip.py` already join `public.arn` via `master_engine`) so folio-nominee gold rows carry distributor attribution like every other gold table.

## 8. Error handling

- **Per-file processing exception** (bad file, unexpected schema, DB error): caught, logged to `etl_pipeline_log` (`status=FAILED`, `error_message`), `PATCH FAILED` with a `failure_reason`. Retried automatically by repo A up to `max_attempts=3`, then `ABANDONED` — visible via `GET /etl-handoff/status`.
- **Whole-group failure** (one member's processing fails after siblings succeeded): only the failed member is `PATCH FAILED`; the successful siblings are `PATCH COMPLETED` independently — each `handoff_id` reports its own true outcome, never bundled.
- **409 on PATCH** (reservation reclaimed by TTL/another runner): logged as `INFO`, not retried — that attempt is no longer ours.
- **API/network errors** (login, reserve, patch): exponential backoff within the run (a handful of retries), then abort the run cleanly, release the lock, let the next cron tick retry — no partial-lock, no partial-`RESERVED`-without-tracking state, since every reservation is written to `etl_report_group_hold`/`etl_pipeline_log` immediately, not batched to the end.

## 9. Concurrency & scale

- **This spec**: one instance at a time, enforced by `pipeline_lock.py` (Postgres advisory lock) — matches your "cron, fixed interval" decision and the file-lock item from the agreed plan.
- **What unlocks true horizontal scale later** (not built now, but this design doesn't block it): once §7.2's `ON CONFLICT` keys are in place, multiple runners with distinct `runner` names could poll concurrently — repo A's `SKIP LOCKED` reservation already supports that on the queue side; the gold-layer idempotency is what currently makes that unsafe.
- Cadence is a crontab line, not app code — documented as an example (`*/15 * * * *` per the team's existing agreement) in the deployment doc, not hardcoded anywhere in this repo.

## 10. Config (`.env`, new to this repo)

```
# DB (migrated off hardcoded utils/db.py values)
DB_HOST=, DB_PORT=, DB_USER=, DB_PASSWORD=, DB_NAME=, MASTER_DB_NAME=

# AWS (new — no boto3 usage existed before this pipeline)
AWS_ACCESS_KEY_ID=, AWS_SECRET_ACCESS_KEY=, AWS_REGION=, AWS_S3_ENDPOINT_URL=(optional)

# intelli-wealth-backend API
INTELLIWEALTH_API_BASE=https://<host>/api/v1
INTELLIWEALTH_RUNNER_EMAIL=etl-runner@intelliwealth.com
INTELLIWEALTH_RUNNER_PASSWORD=<real value, never committed>
ETL_RUNNER_NAME=de-etl-worker-1
ETL_BATCH_LIMIT=50          # per POST /reservations call
ETL_HOLD_TIMEOUT_MINUTES=240 # example only, tune with ops
```

`requirements.txt` additions: `boto3`, `requests`, `python-dotenv`.

## 11. Testing approach

- Unit tests (pytest, matching existing `python_scripts/tests/` convention): `hold_groups.py` grouping/readiness logic (pure functions, no I/O) — the highest-value tests, since this is the trickiest logic. `dedup.py` against a fake DB. `s3_client.py`/`api_client.py` with mocked boto3/`requests`.
- Integration test: a fake `/etl-handoff` server (or `responses`/`requests-mock`) driving `runner.py` through a full HOLDING → READY → COMPLETED cycle for a 3-file group, asserting the exact `etl_pipeline_log` rows produced.
- Gold-loader hardening: existing `python_scripts/tests/` gets new cases asserting re-running `load_*()` twice on identical input produces no duplicate rows (the concrete proof of "no duplication").
- `pipeline_lock.py`: revive/extend `tests/test_pipeline_lock.py` (currently orphaned — source deleted, test still references it).

## 12. Assumptions — confirmed 2026-08-20

1. **Confirmed.** KFIN trio (`MFSD201/211/243`) held together like CAMS, per §5.1.
2. **Confirmed.** New dedicated schema (`pipeline`) holds all three new tracking tables (§4) — not folded into `bronze`.
3. **Confirmed.** `ETL_HOLD_TIMEOUT_MINUTES` default stays 240 min (4 hours), tunable via env, not hardcoded.
4. Exact natural-key unique constraints for §7.2 per gold table — to be enumerated concretely in the implementation plan after inspecting each `etl_gold_*.py` insert.
