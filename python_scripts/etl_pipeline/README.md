# etl_pipeline — automated ETL handoff worker

## One-time setup

1. `cd python_scripts && source venv/bin/activate && pip install -r requirements.txt`
2. Copy `.env.example` to `.env`, fill in every value (DB creds, AWS creds,
   `INTELLIWEALTH_API_BASE`, `INTELLIWEALTH_RUNNER_EMAIL`/`PASSWORD` — see
   the backend team for the seeded `ETL_RUNNER` service account credentials).
3. Apply the schema once: `sql_scripts/etl_pipeline_schema.sql` (idempotent,
   safe to re-run).

## File independence

`WBR2`/`WBR9`/`WBR49` (and the KFIN equivalents) no longer wait for each
other — each report code processes to bronze/gold the moment it's
individually reserved. `gold.holdings`/`gold.clients`/`gold.folio_nominees`
self-heal automatically as siblings arrive later (every `gold_loader` run
is a full recompute + upsert). `gold.sip` is the one table whose
enrichment (ARN, client_id, installment counts) can't recompute from a
plain re-run — see `enrichment_pending_since` in
`automated_pipeline_documentation.md` for how that's reconciled instead.

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
  in the implementation plan). `api_response` holds the raw backend response
  body from that event's `report_outcome()` PATCH call (including on a 4xx —
  e.g. a 422 validation error), kept for reference/debugging; `NULL` for
  events with no associated PATCH call (e.g. HOLD).
- `pipeline.etl_report_group_hold` — current state of every distributor+date
  group: `HOLDING` (waiting on siblings), `READY`/`PROCESSING`, `COMPLETED`,
  `TIMED_OUT` (waiting longer than `ETL_HOLD_TIMEOUT_MINUTES` — needs a human
  to check why a sibling report never arrived).
- Query stuck groups: `SELECT * FROM pipeline.etl_report_group_hold WHERE status IN ('HOLDING', 'TIMED_OUT') ORDER BY first_seen_at;`

## Idempotency via `ON CONFLICT` upserts

Both `gold.transactions` and `gold.sip` now use `ON CONFLICT` upsert logic
(via `utils/upsert.py`) against the unique natural-key indexes defined in
`sql_scripts/add_constraints.sql`. Re-processing the same rows is idempotent:
replayed loads will update existing rows rather than fail with a
`UniqueViolation`. This applies to both the automatic pipeline and manual
uploads through the Streamlit app.
