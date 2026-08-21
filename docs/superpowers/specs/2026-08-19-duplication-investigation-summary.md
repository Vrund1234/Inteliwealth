# Duplication Investigation — Full Summary

**Date:** 2026-08-19
**Original question:** if `app.py`'s pipeline (or an automated version of it) is run multiple times, does data duplicate in bronze/silver/gold — and what should be done about it?
**Current status:** no changes have been made to the live database. Two scripts exist (`sql_scripts/dedup_cleanup.sql`, `sql_scripts/add_constraints.sql`) but neither has been successfully run — see "What's still open" at the end.

---

## 1. Initial code-level finding: yes, partially

Tracing every insert path in the pipeline (`app.py` → `raw_ingestion.py` → `transformations/transform.py` → `gold_loader.py`):

- **Safe today**: `gold.amc`, `gold.scheme`, `gold.holdings`, `gold.clients`, `gold.folio_nominees` all filter against existing natural keys before inserting. `bronze.scheme_mapping` does a real `INSERT ... ON CONFLICT DO UPDATE`.
- **Not safe**: bronze tables compute a duplicate `flag` but never filter on it before insert; silver's `append_new_rows()` has the identical bug; `gold.transactions`/`gold.sip` have duplicate-checking explicitly disabled in code comments ("NO DUPLICATE CHECK... NO NATURAL KEY CHECK"); `gold.scheme_nav` relies on a watermark only.
- No scheduling/automation existed in the repo at the time — the "run it again automatically" risk was theoretical until validated against real data.

## 2. Real-file validation (the 6 sample files in `/home/user/Documents/data-files/`)

| File | Type | Rows |
|---|---|---|
| `10072026104746_216882305R2.csv` | CAMS transactions | 90,536 |
| `10072026104907_216882541R9.csv` | CAMS investor master | 2,098 |
| `10072026105002_216882702R49.csv` | CAMS SIP | 738 |
| `MFSD201_WBTRN28912495_428923.csv` | KFIN transactions | 38,230 |
| `MFSD211_WBMST9217829_386513.csv` | KFIN investor master | 1,444 |
| `MFSD243_WSREG8131655_1159890_0.csv` | KFIN SIP | 658 |

**Row counts line up almost exactly with what's already in the DB** (90,536+38,230=128,766 ≈ `gold.transactions`'s 128,767; 2,098+1,444=3,542 = `investor_master`'s row count exactly; 738+658=1,396 = `sip_master_new`'s row count exactly) — strong evidence these exact files (or equivalents) are already loaded, so re-running them would duplicate most of the current dataset.

Key validation finding: `TRXNNO` alone is **not** a safe identifier — even within the single R2 file, the same `TRXNNO` appears against 4 completely unrelated folios/schemes/dates/amounts.

## 3. Cross-checked against the real production app (`intelli-wealth-backend`, DB on port 5434)

This is a separate, actively-developed backend repo with its own database (`intelliwealth`) that already has real `UNIQUE` constraints. Verified directly:

| Table | App's real constraint |
|---|---|
| `transactions` | `uq_transactions_org_rta_txn_value` = `(organization_id, rta, rta_txn_no, folio_number, amount, units)`, `NULLS NOT DISTINCT` |
| `sip` | `uq_sip_master_org_rta_reg` = `(organization_id, rta, sip_reg_no)` — no null handling |
| `clients` | `uq_client_org_pan` — a **partial** unique index `(organization_id, pan) WHERE pan IS NOT NULL AND is_deleted = false` |

Read the actual migration (`n2b3c4d5e6f7_widen_transactions_unique_key.py`) and sync code (`app/modules/gold_sync/service.py`) — its measured numbers matched ours almost exactly (KFIN `38,230 → 36,718` distinct on `folio+trno+amount+units`, identical to our own measurement). Confirmed the sync uses `ON CONFLICT (constraint="uq_transactions_org_rta_txn_value") DO UPDATE`, meaning **gold's key must not be wider than the app's 5 columns** — a wider gold key would let two gold rows survive that the app's sync treats as one, silently overwriting on sync. Also confirmed `SipMaster.sip_reg_no` is `nullable=False` in the app's model and the sync query filters `sip_reg_no IS NOT NULL` — with `sip_reg_no` blank in 869/1,397 (62%) of `gold.sip` rows, most SIPs never reach the app today.

**Two things I had told you earlier turned out to be wrong**, caught by this cross-check:
- I'd said `gold.clients`/the app had no PAN uniqueness — it does, as a partial index my first `information_schema.table_constraints`-only query missed.
- I'd said the user's proposed 7-column `gold.transactions` key (`+ scheme_code, txn_date`) was "fine either way" — it's not; it risks the silent-overwrite-on-sync problem above. Corrected to the 5-column key.

## 4. Final natural keys (as of the last validated state)

| Layer | Table | Key |
|---|---|---|
| Bronze | `transaction_master_new` | `(source, trxnno, folio_no, amount, units)` |
| Bronze | `investor_master` | `(source, folio_no, product_code)` |
| Bronze | `sip_master_new` | `(source, folio_no, auto_trno, scheme_code, inv_iin)` |
| Silver | `transaction_master_new` | same as bronze |
| Silver | `investor_master` | same as bronze |
| Silver | `sip_master_new` | **`(source, folio_no, scheme_code, inv_iin, reg_date, auto_amount)`** — differs from bronze, see §6 |
| Gold | `transactions` | `(rta, rta_txn_no, folio_number, amount, units)` |
| Gold | `sip` | `(rta, folio_number, scheme_code, registered_date, amount)` |
| Gold | `holdings`, `scheme_nav`, `scheme`, `amc`, `folio_nominees` | unchanged, but none had a real DB constraint before — all first-ever additions |
| Gold | `clients` | `(pan)`, partial index `WHERE pan IS NOT NULL` |

## 5. PostgreSQL version problem, found and fixed

The live DB (`17_08_2026_intelliwealth_layer_db`, port 5432) is **PostgreSQL 14.23** — `NULLS NOT DISTINCT` (used throughout the app's own constraints) requires PG15 and doesn't exist here. Fixed with the standard pre-PG15 workaround: `COALESCE(col, chr(0))` for text columns (a NUL byte can never be stored in real Postgres text, so it's a guaranteed-safe sentinel), and an `(col IS NULL, COALESCE(col, 0))` pair for numeric/date columns (no numeric sentinel is fully safe here — this data has legitimate negative amounts from reversal transactions).

## 6. A real bug found while trying to run the script, and the fix

`silver.sip_master_new` does **not** have an `auto_trno` column — I'd assumed silver mirrors bronze's SIP columns exactly without ever checking the full column list. The bronze→silver transform (`append_new_rows()` in `transform.py`) filters the dataframe down to silver's actual columns right before insert, silently dropping `auto_trno`. Corrected key, validated directly against the live 1,396-row table: `(source, folio_no, scheme_code, inv_iin, reg_date, auto_amount)` → 1,392/1,396 distinct, only 4 true-duplicate groups left (same folios already known duplicated in `gold.sip` — consistent, not a new surprise).

## 7. Script execution attempt, and the revert

Ran the (then-single-file) script against the live DB. Part 0 (read-only preflight) succeeded and confirmed all the numbers above. Part 1 hit the `auto_trno` error on `silver.sip_master_new` (failed safely — no data touched for that table) but `psql -f` doesn't stop on error by default, so it kept going. The `bronze.transaction_master_new` `DELETE` (a large self-join across 128K rows) kept running **server-side** even after the client process was killed — this blocked a subsequent `DROP SCHEMA` for several minutes until it was properly stopped with `pg_terminate_backend()`.

**Reverted the database** by dropping `bronze`/`silver`/`gold` and restoring from a `pg_dump` backup taken before the run (`/home/user/db_backups/intelliwealth_layer_pre_dedup_20260819_154639.dump`). Verified afterward: all row counts match the pre-script baseline exactly, and the constraint list shows only the 7 original constraints (scheme_mapping family + reference tables) — nothing from the script survived. **The database is currently in its original, unmodified state.**

Split the script into two files afterward so "run the constraints script" is never ambiguous again:
- `sql_scripts/dedup_cleanup.sql` — Part 0 (read-only preflight) + Part 1 (destructive normalize + dedupe). Nothing about constraints.
- `sql_scripts/add_constraints.sql` — only `CREATE UNIQUE INDEX`/`ADD CONSTRAINT`. No `DELETE`, no `UPDATE`. Assumes cleanup already ran.

## 8. The most recent finding: KFIN transaction "duplicates" are often not duplicates at all

Pulled a real duplicate pair from `bronze.transaction_master_new` (`trxnno 10246082`, KFIN, "Lateral Shift Out") to manually verify. Every key column matched (`source`, `folio_no`, `trxnno`, `amount`, `units`) but two columns differed:

| Column | Row 1 | Row 2 | Meaning |
|---|---|---|---|
| `purdate` | 09/09/2015 | 16/09/2015 | Original purchase date of the specific unit lot being redeemed |
| `td_ptrno` | 12932360 | 12932362 | KFIN's reference to the specific original purchase transaction the lot came from |

**This is not a duplicate** — it's a fund switch reporting one row per underlying purchase lot, all sharing the switch's aggregate `trxnno`/`amount`/`units` but each citing a different original purchase.

Checked how widespread this is: **all 331 duplicate groups in `bronze.transaction_master_new` are KFIN-only — zero from CAMS.** Breakdown by transaction type: Systematic Investment (76 groups), Redemption (27), Lateral Shift In (8), Pledging (7), Lateral Shift Out (5), Switch Over Out (3), Unpledging (2), STP In/Out (2), Systematic Withdrawal (1), Purchase (1). Adding `purdate` to the key resolves some but not all of it (179 groups remain). Sampling those 179: it's a **mix** — some (e.g. a "Systematic Investment" pair with identical `td_ptrno=0` and blank `purdate` on both rows, nothing distinguishing them at all) look like genuine duplicates; others (e.g. a "Pledging" pair where `td_ptrno` differs in every sub-group) look like the same different-lot pattern as the switch example.

**No narrow key reliably separates true duplicates from legitimate multi-lot records** — every column we've added (`amount`, `units`, `purdate`) has narrowed but not closed the gap.

**Recommended fix (not yet implemented)**: switch bronze/silver's transaction dedup from a narrow business key to a **full-row hash** comparison — exactly what the code's existing `flag` column already computes but never enforces before insert. A row is only ever called a duplicate when it's byte-identical to another, which can't falsely merge a legitimate different-lot record.

**Scope note on gold**: `gold.transactions` has no `purdate`/`td_ptrno`-equivalent column at all, so it structurally cannot distinguish these lot-level records regardless of key design — and this is the same limitation the app's own `uq_transactions_org_rta_txn_value` constraint has. Fixing that for real means adding columns to gold (and the app), which is new scope beyond a constraint script.

## 9. A correction to an earlier number

I'd stated `bronze/silver.investor_master` had "1,383 duplicate rows." That was a stale number from an early check using only `(source, folio_no)` — before `product_code` was added to the key. Re-verified directly against the live table with the corrected key `(source, folio_no, product_code)`: **3,542 total, 3,542 distinct — zero duplicates.** `investor_master` needs no dedupe at all.

---

## Final outcome (updated 2026-08-19, end of day)

**Decision on §8**: after weighing it, the narrow 5-column key was kept deliberately for bronze/silver transactions — the KFIN lot-record collapsing (§8) is an accepted, understood trade-off, not a bug to fix. Full-row-hash was considered and explicitly rejected in favor of the already-validated key.

**Scripts split in two**, so "run the constraints" is never ambiguous:
- `sql_scripts/dedup_cleanup.sql` — preflight + normalize + destructive dedupe `DELETE`s only.
- `sql_scripts/add_constraints.sql` — index/constraint creation only, no data modified.

**Two more real bugs found by actually running the scripts (not by inspection), both fixed:**
1. **Self-join performance**: the original `DELETE ... USING ... WHERE a.ctid < b.ctid AND ... IS NOT DISTINCT FROM ...` pattern produced a nested-loop plan (cost ~969 million, ~128,766² comparisons) on the 128K-row transaction table — confirmed via `EXPLAIN`, and confirmed hung at 100% CPU for 6+ minutes on a real run. Rewritten to `ROW_NUMBER() OVER (PARTITION BY <key> ORDER BY ctid)` — a single scan + sort, cost ~37,846. `PARTITION BY` also treats NULLs as equal the same way `GROUP BY` does, so no `IS NOT DISTINCT FROM` was needed either.
2. **`chr(0)` sentinel, twice broken**: the PG14 workaround for `NULLS NOT DISTINCT` first used `COALESCE(col, chr(0))` for text columns, reasoning Postgres refuses to *store* a NUL byte so it's a safe sentinel — wrong, because Postgres also refuses to *construct* one, so the expression itself errored (`null character not permitted`) on the first NULL row. Fixed by switching to the two-part `(col IS NULL), COALESCE(col, '')` technique used for numeric columns. Separately, `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX` turned out to reject expression-based and partial indexes outright (confirmed by the server's own error, not assumed) — fixed by keeping those 10 as plain unique indexes (which fully enforce uniqueness on their own) instead of trying to formalize them as named constraints. Only the 4 indexes on plain non-nullable columns became formal constraints.

**Executed successfully against `19_08_2026_intelliwealth_layer_db`** (a second, newer copy of the same dataset — not `17_08_2026_intelliwealth_layer_db`, which was never touched and remains unprotected):
- Backup taken first (`pg_dump`, custom format, bronze/silver/gold schemas).
- `dedup_cleanup.sql`: removed 1,512 / 0 / 0 / 1,512 / 0 / 4 / 1,512 / 9 / 0 / 0 duplicate rows across the 10 tables — every number matching what had been validated in advance.
- `add_constraints.sql`: all 14 unique indexes built and valid, 0 left invalid.
- **Smoke-tested**: a real duplicate `INSERT` into `gold.transactions` was rejected with `duplicate key value violates unique constraint "uq_gold_txn_natural_key"` — confirmed working, not assumed.

## Still open / not done in this pass

1. `17_08_2026_intelliwealth_layer_db` has **not** had either script run — still has its original duplicates, no constraints. Only `19_08` was fixed, per explicit instruction.
2. A CAMS-side equivalent to `td_ptrno`/lot-tracking was never investigated — moot now that the narrow-key approach was kept deliberately (§8's decision above).
3. `gold.sip`'s real fix (making `sip_reg_no`-independent matching work end-to-end for the app) requires an app-repo migration that hasn't been made — out of scope for this pass.
4. The separate file-arrival-order completeness issue (`2026-08-19-partial-file-upload-analysis.md`) is unrelated to duplication and still unaddressed.
5. The staleness/freshness gap found via the new `R2.dbf`/`R9.dbf` samples (gold ~5.5 weeks behind the RTA's own data, already visibly wrong in the live app DB) is flagged but not fixed — the actual fix is getting the automation pipeline (the original ask that started this whole conversation) running on a regular cadence.
6. `bronze/silver/gold.transactions`'s `created_at`/`updated_at`/`load_batch_id` provenance columns (from the teammate's original doc) were never added — out of scope, not requested.
