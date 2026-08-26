# Bronze Duplicate-Flag Performance Fix — Spec

**Date:** 2026-08-26
**Database:** `25_08_2025_intelliwealth_layer_db` (PostgreSQL 17.10, port 5433)
**Builds on:** [2026-08-25-bronze-silver-gold-dedup-constraints.md](2026-08-25-bronze-silver-gold-dedup-constraints.md), which left bronze deliberately unconstrained and out of scope. This spec does not revisit that decision — bronze stays append-only, no unique constraint, no upsert, no rejected inserts. It only fixes *how fast* bronze's existing duplicate-flag mechanism runs.

## Problem

All three bronze loaders (`etl_trans.py`, `etl_investor_master.py`, `etl_sip.py`) compute a `flag` column (1 = already seen, 0 = new) the same way: on every ingestion run, read the **entire** target bronze table into pandas (`pd.read_sql("SELECT * FROM bronze.<table>", engine)`), normalize every row, concatenate the non-metadata columns into a string key per row, and check the new batch's keys against that set with `.isin()`.

This is `O(total rows ever received)` in Python, on every single run, and bronze is append-only by design — it only ever grows. Live numbers as of 2026-08-26: `bronze.transaction_master_new` has 129,568 rows and **zero indexes**; ingestion is daily, multi-distributor, and headed to millions of rows. Left as-is, ingestion time grows without bound.

`flag` is not cosmetic: `transformations/transform.py` pulls into silver via `WHERE flag = 0`, so this is the actual gate between bronze and silver.

## What "duplicate" means today (must not change)

Each loader's `compare_cols` is **every non-metadata column** (all columns minus `flag`, `created_at`, `updated_at`, `source`) — a full-row comparison, not the narrower natural key used in the silver/gold spec. This is deliberately looser than the natural key: two transaction rows sharing `(source, trxnno, folio_no, amount, units)` but differing in `td_ptrno`/`rep_date`/`postdate`/`purdate`/`puramt`/`trflag` (all confirmed to vary across ~50%, 28%, 15%, 30%, 23%, 15% of today's 507 bronze duplicate groups respectively) currently get `flag=0` ("new"), reach silver, and refresh the row there via silver's own `ON CONFLICT DO UPDATE`. `trxnstat` itself never varies across any of today's 507 duplicate groups — but the mechanism must keep comparing the full row anyway, because narrowing to the natural key would flip those rows to `flag=1` and silently stop them from ever reaching silver again, breaking the resend-refresh behavior silver was built for.

**Consequence for this fix:** the replacement mechanism must reproduce full-row-duplicate semantics exactly. It must not key on the natural key alone.

## Design: a hashed, indexed duplicate check

Add one new column, `row_hash TEXT`, to each of the three bronze tables. It holds a SHA-256 hash of the same normalized, concatenated `compare_cols` string each loader already builds today (same normalization functions, same column set — no new normalization logic, no behavior change to what counts as a duplicate). A plain (non-unique) btree index on `row_hash` makes "does this row already exist" an indexed lookup instead of a full-table read.

**Per ingestion run, per table:**

1. Build the new batch's mapped/normalized dataframe exactly as today (`apply_*_mapping` → `normalize` → `clean_identifier_columns` → `format_dates`) — unchanged.
2. Compute `row_hash` for each new row: `sha256("|".join(normalized compare_cols))`, using the same `compare_cols` derivation and the same value-normalization each loader already has (matching `prepare_for_comparison`/`normalize_compare_value` for transactions; the equivalent inline normalization for investor/SIP).
3. Query Postgres for only the new batch's hashes: `SELECT row_hash FROM bronze.<table> WHERE row_hash = ANY(:hashes)` — an indexed lookup returning only matches, never the whole table.
4. `flag = 1` where the new row's hash came back, `0` otherwise.
5. Insert the batch as today (`to_sql(..., if_exists="append")`), now including `row_hash`.

Cost per run becomes `O(new batch size)` (hash computation in Python + one indexed round-trip), independent of how large bronze's history has grown.

**One-time backfill (part of this migration, not a recurring cost):** read each bronze table once, apply the same normalization + hashing, `UPDATE` every existing row's `row_hash`, then add the index `CONCURRENTLY` and set the column `NOT NULL` once confirmed fully populated. This is the last full-table read bronze will ever need for this purpose.

**Hash collisions:** SHA-256 over normalized row content is the same technique used for surrogate/dedup keys elsewhere (e.g. dbt, Snowflake `HASH()`); collision probability at these row counts is not a practical concern and is accepted rather than engineered around.

## Per-table specifics

| Table | `compare_cols` (unchanged) | New index |
|---|---|---|
| `bronze.transaction_master_new` | all columns except `flag`, `created_at`, `updated_at`, `source` | `idx_bronze_txn_row_hash` on `row_hash` |
| `bronze.investor_master` | same pattern | `idx_bronze_investor_row_hash` on `row_hash` |
| `bronze.sip_master_new` | same pattern | `idx_bronze_sip_row_hash` on `row_hash` |

All indexes built `CONCURRENTLY` (no write lock), matching the convention already established in `sql_scripts/dedup_constraints_migration_2026-08-25.sql`.

## `created_at` — unchanged, verified by test

Every new row keeps getting the current per-batch `pd.Timestamp.now(tz="Asia/Kolkata")` stamped before insert, exactly as today. Since bronze only ever `INSERT`s (never `UPDATE`s an existing row — that stays true after this change), no row's `created_at` can ever be rewritten once set. A test asserts: newly inserted rows get a non-null, current `created_at`; re-running ingestion over already-seen data never modifies an existing row's `created_at` (only ever appends new rows with new timestamps).

## `flag` — unchanged semantics, verified by test

A regression test builds a small fixture (a mix of exact duplicates and near-duplicates that differ only in a non-key column, mirroring the real `td_ptrno`/`rep_date` pattern found in bronze) and asserts the new hash-based flag computation produces byte-identical `flag` values to the current Python full-row-compare method on the same data — proving this is a performance change, not a behavior change.

## Investor duplicates: append + flag, never update

Confirmed decision (see prior turn): bronze never updates an existing investor row in place, even though investor attributes (address, bank details, nominee) do change over time in the real world. Bronze's job is preserving exactly what was received, when; `silver.investor_master`'s own `ON CONFLICT DO UPDATE` (from the 2026-08-25 migration) already owns "what does this investor look like right now." Uniform append-only + flag treatment across all three bronze tables, no special case.

## Out of scope

- No unique constraint on any bronze table; no rejected inserts.
- No change to bronze's raw/audit-copy philosophy.
- No change to silver/gold (already addressed in the 2026-08-25 spec).
- No change to the SIP date-parsing fix (already shipped separately, ahead of this spec).

## Testing plan

- Unit tests: `row_hash` computation is deterministic for identical normalized rows, and differs whenever any compared column differs (proves full-row semantics carried over from the old mechanism).
- Regression test: hash-based flag output matches today's Python full-table-compare output on a shared fixture (exact duplicates + near-duplicates-by-metadata-only).
- `created_at` invariant test (above).
- Backfill test: after backfill, `row_hash` is non-null for every existing row, and rows already known to differ only in `td_ptrno`/`rep_date` (real bronze duplicate groups) get *different* hashes — confirming the backfill didn't accidentally narrow to the natural key either.

## Rollout order

1. Add nullable `row_hash` column to each bronze table.
2. Backfill `row_hash` for all existing rows (one-time full read).
3. Build the three indexes `CONCURRENTLY`.
4. Set `row_hash NOT NULL` on each table once backfill is confirmed complete.
5. Switch the three loaders to the hash-based check.
6. Regression tests green before/after, on a fixture — not on production bronze.
