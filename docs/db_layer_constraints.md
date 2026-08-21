# Layer Constraint Map

Database: `19_08_2026_intelliwealth_layer_db` (PostgreSQL)
Generated: 2026-08-21, by querying `information_schema.table_constraints`, `pg_constraint`, and `pg_attribute` directly.

Covers all 32 base tables across the 5 schemas: `bronze` (12), `silver` (3), `gold` (8), `pipeline` (3), `public` (6).

## Summary

| Metric | Count |
|---|---|
| Schemas | 5 |
| Tables | 32 |
| Primary keys | 13 |
| Unique constraints | 11 |
| Check constraints | 2 |
| Foreign keys | 0 |
| Not-null columns | 73 |

## Structural notes

- **No foreign keys anywhere in the database.** Zero `FOREIGN KEY` constraints exist across all five schemas — cross-table relationships (e.g. holdings → scheme, transactions → clients) are enforced only in application/ETL logic, not at the database level.
- **7 of 8 gold-layer tables have no primary key.** Only `gold.scheme` declares one; `clients`, `holdings`, `sip`, and `transactions` — the tables most likely to be queried by row — have no PK or unique key at all.
- **Silver and public schemas carry zero constraints.** All 3 silver tables and all 6 public tables have no PK, unique, check, or not-null rule — consistent with silver being an intermediate staging layer and public holding scratch/legacy tables (`tmp_new`, `old_h`, `app_h2`, …).
- **Bronze is the only fully-guarded layer.** 9 of 12 bronze tables have a primary key plus a natural-key unique constraint (e.g. `uq_scheme_mapping (rta, rta_scheme_code)`); the 3 raw-import tables (`investor_master`, `sip_master_new`, `transaction_master_new`) are unconstrained landing tables.

---

## bronze — Raw ingest layer (12 tables)

RTA-fed reference and mapping data. Codes/masters are fully keyed; the three `*_master_new` raw-import tables land unconstrained.

| Table | Primary key | Unique | Check | Not-null columns |
|---|---|---|---|---|
| `amc_master` | `amc_id` | `amc_code` | — | amc_id, amc_code, amc_name |
| `category_code` | `category_id` | `category_name` | — | category_id, category_name, created_at, updated_at |
| `investor_master` | — | — | — | *unconstrained* |
| `occupation_code` | `occupation_id` | `occupation_name` | — | occupation_id, occupation_name, created_at, updated_at |
| `scheme_mapping` | `mapping_id` | `uq_scheme_mapping (rta, rta_scheme_code)` | — | mapping_id, rta, rta_scheme_code |
| `scheme_mapping_audit` | `audit_id` | — | — | audit_id, rta, rta_scheme_code, rule_name, execution_outcome, evaluated_at |
| `scheme_mapping_override` | `override_id` | `uq_scheme_mapping_override (rta, rta_scheme_code)` | — | override_id, rta, rta_scheme_code, reason, mapped_at, is_active |
| `scheme_mapping_review` | `review_id` | `uq_scheme_mapping_review (rta, rta_scheme_code, candidate_rank)` | `reviewer_decision IN ('APPROVED','REJECTED')` | review_id, rta, rta_scheme_code, candidate_rank, rule_name, created_at |
| `scheme_name_alias` | `alias_id` | — | `alias_type IN ('TOKEN','FUND_RENAME')` | alias_id, raw_term, normalized_term, alias_type, is_active, created_at |
| `sip_master_new` | — | — | — | *unconstrained* |
| `state_code` | `state_id` | `state_name` | — | state_id, state_name, created_at, updated_at |
| `transaction_master_new` | — | — | — | *unconstrained* |

---

## silver — Cleansed / conformed layer (3 tables)

All three tables mirror the bronze raw-import set and currently carry no declared constraints of any kind.

| Table | Primary key | Unique | Check | Not-null columns |
|---|---|---|---|---|
| `investor_master` | — | — | — | *unconstrained* |
| `sip_master_new` | — | — | — | *unconstrained* |
| `transaction_master_new` | — | — | — | *unconstrained* |

---

## gold — Serving layer (8 tables)

Natural-key uniques exist on 4 tables; only `scheme` has a surrogate primary key. `clients`, `holdings`, `sip`, and `transactions` have no key at all.

| Table | Primary key | Unique | Check | Not-null columns |
|---|---|---|---|---|
| `amc` | — | `uq_gold_amc_code (amc_code)` | — | amc_code |
| `clients` | — | — | — | pan_verified |
| `folio_nominees` | — | `uq_gold_nominees_natural_key (holding_id, seq)` | — | seq |
| `holdings` | — | — | — | rta, folio_number |
| `scheme` | `id` | `uq_gold_scheme_natural_key (rta, scheme_code)` | — | id, rta |
| `scheme_nav` | — | `uq_gold_scheme_nav_natural_key (scheme_id, nav_date)` | — | nav_date, nav |
| `sip` | — | — | — | rta |
| `transactions` | — | — | — | rta, rta_txn_no, txn_type |

---

## pipeline — Orchestration / audit layer (3 tables)

ETL run bookkeeping — every table has a primary key and a dense set of not-null operational columns.

| Table | Primary key | Unique | Check | Not-null columns |
|---|---|---|---|---|
| `etl_pipeline_log` | `log_id` | — | — | log_id, run_id, group_key, rta, report_code, layer, status, created_at |
| `etl_processed_files` | `content_hash` | — | — | content_hash, handoff_id, processed_at |
| `etl_report_group_hold` | `group_key` | — | — | group_key, rta, required_report_codes, members, status, first_seen_at, last_updated_at |

---

## public — Scratch / legacy schema (6 tables)

Ad-hoc and legacy working tables outside the medallion layers. None carry any constraint.

| Table | Primary key | Unique | Check | Not-null columns |
|---|---|---|---|---|
| `app_h` | — | — | — | *unconstrained* |
| `app_h2` | — | — | — | *unconstrained* |
| `old_h` | — | — | — | *unconstrained* |
| `src_cams` | — | — | — | *unconstrained* |
| `src_nat` | — | — | — | *unconstrained* |
| `tmp_new` | — | — | — | *unconstrained* |
