# CAMS WBR Report Pipeline

Ingests the four CAMS WBR report files, models them through bronze → silver → gold, and
regenerates the reports from the gold tables.

Completely isolated from `python_scripts/`: own virtualenv, own database schemas, no
imports across the boundary. Nothing in the existing pipeline is read or written.

---

## Quick start

```bash
cd new_pipeline
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

./venv/bin/python main.py migrate                       # create schemas + tables
./venv/bin/python main.py plan   --input <dir>          # what would be routed, no DB access
./venv/bin/python main.py run    --input <dir> --period 2025
./venv/bin/pytest tests/ -q
```

Default input directory is `/home/user/Inteliwealth-pipeline/files/gold`; output goes to
`new_pipeline/output/`. Both are overridable — see Configuration.

Per-layer commands: `main.py bronze --input <dir>`, `main.py silver`,
`main.py gold --period 2025 --formats xlsx,csv,xls`.

Every command exits non-zero on failure.

---

## Verified behaviour

Measured against the four sample files on 2026-08-13.

| Check | Result |
|---|---|
| Routing | 4/4 files, and WBR36H correctly separated from WBR36 |
| Column match | 8/8, 8/8, 40/40, 31/31 — no missing required, no undeclared |
| Bronze → silver → gold | 152 / 101 / 9 rows, zero rejects |
| Grain assertion | ratio 1.00 on all three gold tables |
| Reports vs source | all four reproduce the source **cell for cell**, including row order |
| Idempotency | three consecutive runs → byte-identical CSVs, no table growth |
| Rejection path | 4 injected corruptions → 4 different rules, each traceable to a file row |
| Tests | 18 passed |

---

## Layout

```
config/     settings.py  mapping_cams_wbr.py  lookups.py  file_patterns.py
ingestion/  reader.py  router.py  validators.py
bronze/     cleaners.py  writer.py
silver/     transformer.py  rules.py
gold/       reports.py  exporter.py  loader.py
utils/      db.py  logging.py  upsert.py  audit.py
sql/        001_schemas.sql  002_tables.sql  003_audit.sql
docs/       cams-wbr-profile.md
tests/      test_pipeline.py
main.py
```

`docs/cams-wbr-profile.md` is the source of truth for what the files contain. Every entry
in `mapping_cams_wbr.py` traces to a line in it. Read it before changing any mapping.

---

## Database objects

| Schema | Tables |
|---|---|
| `bronze_wbr` | `brokerage_by_scheme`, `investor_kyc_status`, `invalid_euin` — all data columns `text` |
| `silver_wbr` | same three, real types, plus standardised `*_std` and derived columns |
| `gold_wbr` | same three, report-shaped, one row per declared natural key |
| `audit_wbr` | `source_files`, `load_summary`, `rejects`, plus a `last_load` view |

Natural keys, which are also the `UNIQUE` constraints and the `ON CONFLICT` targets:

| Table | Key |
|---|---|
| `brokerage_by_scheme` | bronze/silver `(report_variant, product_code)`; gold `(report_period, report_variant, product_code)` |
| `investor_kyc_status` | `(amc_code, folio)` |
| `invalid_euin` | `(amc_code, trxn_no)` |

Gold is **report-shaped, not entity-shaped**: one table per WBR report, because the
deliverable is the report. That is a deliberate difference from `gold.*`, which models
business entities, and it means `gold_wbr` never competes with it.

Useful queries:

```sql
SELECT * FROM audit_wbr.last_load;                       -- last outcome per entity/layer
SELECT rule, count(*) FROM audit_wbr.rejects GROUP BY 1; -- what got refused and why
SELECT * FROM audit_wbr.source_files ORDER BY ingested_at DESC;
```

---

## Configuration

Environment variables, all optional.

| Variable | Default | Purpose |
|---|---|---|
| `WBR_DB_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_NAME` | `localhost` / `5432` / `postgres` / `postgres` / `master_tables_db` | connection |
| `WBR_BRONZE_SCHEMA` / `_SILVER_` / `_GOLD_` / `_AUDIT_` | `bronze_wbr` / `silver_wbr` / `gold_wbr` / `audit_wbr` | schema names |
| `WBR_INPUT_DIR` | `/home/user/Inteliwealth-pipeline/files/gold` | where files are read from |
| `WBR_OUTPUT_DIR` | `new_pipeline/output` | where reports are written |
| `WBR_LOG_LEVEL` | `INFO` | logging level |
| `WBR_CHUNKSIZE` | `2000` | rows per upsert batch |
| `WBR_STRICT` | `true` | `false` drops undeclared columns instead of aborting |
| `WBR_SOFFICE` | `soffice` | binary used for optional `.xls` export |

---

## Things worth knowing before you change anything

Each of these was found by reading the files or by running the pipeline, and each is
encoded in config or in a test.

1. **The files are legacy BIFF `.xls`**, not `.xlsx`. Reading them needs `xlrd>=2.0`,
   which is not in `python_scripts/venv`. That is the main reason for a separate venv.

2. **WBR36 and WBR36H share 10 of their 11 product codes.** `report_variant` must stay in
   the natural key or the H variant overwrites the standard one.

3. **A `.xls` date cell is typed**, and pandas renders it as `2026-07-16 00:00:00` under
   `dtype=str` — not in the display format a converted CSV shows. `DATE_FALLBACKS` in the
   mapping handles all observed forms. The first profiling pass read LibreOffice-converted
   CSVs and got four date columns wrong as a result; profile the `.xls` directly.

4. **`euin_valid` has two invalid values, `N` and `F`**, both carrying
   `reason = 'Invalid EUIN'`. Every filter is `<> 'Y'`, never `= 'N'`.

5. **WBR56 carries three date columns in two formats** in the same file: `%d-%b-%Y` for
   `rep_from_date`/`rep_to_date`, a typed date cell for `rep_date`.

6. **A bare `/` means unknown** in the compound `location` and `state` columns. It is not
   a global blank — the report must reproduce it verbatim, so it is interpreted only
   inside `split_compound`.

7. **Columns feeding a lookup keep the provider's casing.** The report has to emit
   `KYC Not Verified` exactly; the standardised value lives in `<column>_std`.

8. **`ORDER BY` in the exporter is not optional.** A bare `SELECT *` returns heap order,
   which changes after an `UPDATE`; two runs produced byte-different CSVs before
   `source_row` was added.

9. **Five WBR36 measures and 13 WBR56 columns exist nowhere in the current
   bronze/silver/gold** — no upfront/AFE/trailer/clawback/incentives breakdown, no KYC
   status descriptions, no Aadhaar-link status. The WBR files are therefore inputs as
   well as output templates. This is an assumption; see the end of
   `docs/cams-wbr-profile.md`. If the intent was to derive all four reports from the
   R-series files alone, that needs a new CAMS feed.

---

## Adding another report

1. Profile the file and add a section to `docs/cams-wbr-profile.md`.
2. Add a `FILE_PATTERNS` entry (regex, ordered so a longer prefix wins) and a mapping
   block in `config/mapping_cams_wbr.py`.
3. Add the entity to `ENTITIES` with its natural key, and to `GOLD_GRAIN` with its grain.
4. Add the three tables to `sql/002_tables.sql` with a `UNIQUE` on the natural key.
5. Add an `OUTPUT_LAYOUTS` entry with the exact column order.
6. Add `extract_`/`transform_`/`load_` to `gold/reports.py` and register in
   `gold/loader.py`.
7. Add a sample file to the `test_mapping_matches_the_real_file_headers` parametrisation.

Steps 1–5 are configuration. Only step 6 is code.
