# Partial File Upload Analysis — Effect of Processing Each RTA File Individually

**Date:** 2026-08-19
**Scope:** `python_scripts/app.py` → `raw_ingestion.py` → `transformations/transform.py` → `gold_loader.py` pipeline
**Sample files analyzed:** `/home/user/Documents/data-files/`

## Why this matters

CAMS and KFintech deliver three separate file types per RTA (transaction, investor master, SIP). Today's pipeline treats each type as an **independent** ingestion path — no file type waits for, or requires, another. That's good for resilience (one missing file type doesn't crash the run) but it means a file processed alone can leave the Gold layer in a state that is silently **incomplete**, not wrong and not duplicated, just missing data that never gets filled in later. This is a *different* problem from the duplication issue already being fixed — it's about **completeness**, and it needs its own fix.

Every finding below is traced directly against the pipeline code (file:line) and, where relevant, cross-checked against the actual sample files.

---

## 1. `10072026104746_216882305R2.csv` — CAMS transaction file, alone

| Layer | Outcome |
|---|---|
| `bronze.transaction_master_new` | ✅ Loads fully |
| `bronze.scheme_mapping` | ✅ Loads fully — sourced purely from this file's scheme codes (`scheme_mapping.py:281-292`), gated only on the transaction file being present (`app.py:299`) |
| `silver.transaction_master_new` | ✅ Loads fully |
| `gold.transactions` | ✅ Loads fully — no investor dependency; `client_id`/`amc_id` are always `NULL` here regardless of investor data, by design (`etl_gold_transaction.py:766,768`) |
| `gold.scheme_nav` | ✅ Loads fully — mined purely from `purprice` in the transaction rows (`etl_gold_scheme_nav.py:160-182`), no investor dependency |
| `gold.holdings` | ⚠️ **Loads, but degraded.** `extract_holdings()` LEFT JOINs investor data (`etl_gold_holdings.py:132-190`) — a folio with no matching `investor_master` row still gets a holdings row, but `nominee_name`, `nominee_relation`, `bank_name`, `demat_flag`, `kyc_status`, and `client_id` all come back `NULL`. |
| `gold.clients` | ❌ **No row at all.** `extract_clients()` is driven `FROM silver.investor_master` (`etl_gold_clients.py:197`) — a PAN that only appears in the new transaction data, with no investor_master row, produces **no client record whatsoever**, not even a partial one. |

**The permanence problem:** `load_holdings()` is insert-only — it anti-joins against existing `(rta, folio_number, scheme_id)` keys already in `gold.holdings` (`etl_gold_holdings.py:2456-2497`) and never updates a row once inserted. There is no `created_at`/watermark filter in `extract_holdings()` at all — it re-reads the entire `silver.transaction_master_new` table every run, but the anti-join means an already-inserted NULL-enriched row is never revisited. **If the matching R9 file arrives in a later run, the holdings row stays NULL forever.**

---

## 2. `10072026104907_216882541R9.csv` — CAMS investor master file, alone

| Layer | Outcome |
|---|---|
| `bronze.investor_master` | ✅ Loads fully |
| `bronze.scheme_mapping` | ⏭️ **Skipped entirely** — `app.py:299` gates `load_scheme_mapping()` on `uploaded_types["transaction"]`; with no transaction file in this batch, the `else` branch at `app.py:343-348` explicitly prints "No transaction file uploaded. Scheme Mapping skipped." |
| `silver.investor_master` | ✅ Loads fully, independent of transaction/SIP blocks |
| `gold.clients` | ✅ **Loads fully** — `extract_clients()` is driven by `silver.investor_master` with transaction/SIP data only as optional LEFT-JOIN enrichment (`etl_gold_clients.py:323-359`); a brand-new investor gets a proper client row from this file alone. |
| `gold.holdings` | ⏭️ **Not revisited.** `extract_holdings()` is transaction-driven, not investor-driven (`etl_gold_holdings.py:132`). New investor rows do not trigger any re-read of existing transactions. If a transaction for this same folio was already loaded earlier (before this R9 file existed), its `gold.holdings` row is **already NULL-enriched and stays that way** — this file arriving later does not fix it. |
| `gold.transactions`, `gold.scheme_nav`, `gold.sip` | ⏭️ Correctly skipped — nothing new for them this run |

**Confirmed via repo-wide search:** there is no backfill/reprocess logic anywhere (`grep -rniE "backfill|reprocess" python_scripts/` → no matches), and the only `UPDATE` statement in any Gold ETL script is in `etl_gold_scheme.py:1582` (unrelated to holdings/clients/sip).

---

## 3. `10072026105002_216882702R49.csv` — CAMS SIP file, alone

| Layer | Outcome |
|---|---|
| `bronze.sip_master_new` | ✅ Loads fully |
| `silver.sip_master_new` | ✅ Loads fully, independent of transaction/investor blocks |
| `gold.sip` — `scheme_id` | ⚠️ **NULL, not dropped.** `scheme_id` is resolved at the *silver* layer via `map_scheme_id()` against `bronze.scheme_mapping` (`transform.py:199-303`). If this SIP file's scheme codes were never seen in a transaction file (so `bronze.scheme_mapping` has no entry for them — remember, scheme mapping is transaction-gated, see file #2 above), the lookup leaves `scheme_id = None` (`transform.py:268-274`) and the row is still inserted — proven by the hard guard at `etl_gold_sip.py:1461-1468` that raises `ValueError` if any row is ever dropped. |
| `gold.sip` — `client_id` | ⚠️ **NULL, not dropped**, for the same reason: PAN lookup against `gold.clients` (`etl_gold_sip.py:793-822`) — unmatched PAN just leaves `client_id` as `NaN`→`None`. No required-field guard exists for SIP (unlike holdings' `required = ["rta","folio_number","scheme_id"]` check) — `load_sip()` explicitly logs "Duplicate filtering: DISABLED", "Existing Gold comparison: DISABLED" (`etl_gold_sip.py:1768-1770`). |
| Permanence | ❌ **Stuck forever**, for the same structural reason as holdings: `extract_sip()`'s incremental watermark is `silver.sip_master_new.created_at > MAX(gold.sip.created_at)` (`etl_gold_sip.py:155-183,372`). Once a SIP row is loaded, its silver `created_at` will never again exceed the new gold watermark — it is never re-selected, so a later-arriving transaction or investor file that *would* resolve the missing `scheme_id`/`client_id` never gets the chance to. |

---

## 4. `MFSD201_WBTRN28912495_428923.csv` — KFIN transaction file, alone

**Identical outcome to file #1 (CAMS R2).** Confirmed via `raw_ingestion.py:716` (`mfsd201` routes to the same `process_transactions()` call as CAMS, `raw_ingestion.py:762-765`) and `etl_trans.py:258` (`apply_transaction_mapping(kfin, TRANSACTION_MASTER_MAPPING, "KFIN")` — same shared function, only the `source` label differs, and no gold-layer script branches on `source` for control flow — it's used only as a data/partition column, e.g. `PARTITION BY UPPER(TRIM(source))` in `etl_gold_holdings.py:47-49`).

`gold.transactions`/`gold.scheme_nav` load fully; `gold.holdings` loads with NULL nominee/bank/KYC/client_id for genuinely new investors; `gold.clients` gets no row for them. Same permanence problem.

---

## 5. `MFSD211_WBMST9217829_386513.csv` — KFIN investor master file, alone

**Identical outcome to file #2 (CAMS R9).** Same shared `apply_investor_mapping()` function (`etl_investor_master.py:238`), same gating, same result: `gold.clients` loads fully from this file alone, `gold.holdings` for pre-existing transactions is not revisited, `bronze.scheme_mapping` is skipped (no transaction file in this batch).

---

## 6. `MFSD243_WSREG8131655_1159890_0.csv` — KFIN SIP file, alone

**Identical outcome to file #3 (CAMS R49).** Same shared `apply_sip_mapping()` function (`etl_sip.py:249`), same `scheme_id`/`client_id` NULL-not-dropped behavior, same permanent-NULL fate if the matching transaction/investor file arrives later.

---

## Root cause (one sentence)

**Gold-layer enrichment across entities is one-directional and insert-only**: `gold.holdings` and `gold.sip` are built by joining outward from their own driving table (`transaction_master_new`, `sip_master_new`) to *whatever investor/scheme/client data happens to exist at that moment*, and once a row is inserted it is never revisited — so the completeness of a Gold row depends entirely on **file arrival order**, and a late-arriving file can never fix a row created before it existed.

## Can we solve this? Yes — two complementary fixes

### Fix 1 (primary): a reconciliation/backfill pass

Add a small, idempotent step — run at the end of `load_gold()` or as a separate scheduled step — that finds rows with fixable NULLs and re-resolves them:

```sql
-- gold.holdings candidates
SELECT id, rta, folio_number FROM gold.holdings
WHERE client_id IS NULL OR nominee_name IS NULL OR kyc_status IS NULL;

-- gold.sip candidates
SELECT id, rta, folio_number FROM gold.sip
WHERE scheme_id IS NULL OR client_id IS NULL;
```
For each candidate, re-run the same enrichment lookup (`investor_base`/`gold.clients`/`gold.scheme` join) that the original load used, and `UPDATE` only the columns that are still NULL and now have a match. This is safe, additive, and doesn't touch any correctly-populated row — it only fills gaps. This turns "permanently stuck" into "self-healing within one automated cycle."

### Fix 2 (secondary): stop gating scheme mapping on transactions only

`load_scheme_mapping()` currently sources scheme codes only from `bronze.transaction_master_new` (`scheme_mapping.py:281-292`) and is only invoked when a transaction file is present (`app.py:299`). Extending it to *also* pull distinct scheme codes from `bronze.sip_master_new` — and triggering it whenever either a transaction **or** SIP file is uploaded — closes most of the `gold.sip.scheme_id IS NULL` gap at the source, rather than relying solely on the backfill pass to catch it later.

### Recommended sequencing

Both fixes are independent of the duplication-prevention work already scoped, and additive to it — implementing the reconciliation pass (Fix 1) is the higher-value, lower-risk piece and can be built alongside the insert-time dedup filters already agreed. Fix 2 is a smaller follow-on once Fix 1 is in place as the safety net.
