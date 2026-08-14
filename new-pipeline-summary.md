# Building a New RTA Pipeline — Developer Guide

**Purpose:** everything a developer needs to add a pipeline for a new RTA source or a new data
format, without touching a single existing file.

**Audience:** a developer who has not worked on this repository before.

**Status:** structure, blueprint and templates only. No implementation code.

---

## 1. How the Existing Pipeline Is Built

Read this section before writing anything. The new pipeline should be recognisable to anyone who
knows the existing one, which means copying its shape deliberately — and copying its known defects
deliberately *not at all*. Section 1.5 lists which conventions to keep and which to leave behind.

### 1.1 Folder structure as it exists today

```
Inteliwealth/
├── python_scripts/
│   ├── app.py                     Streamlit UI — the only entry point for the full run
│   ├── raw_ingestion.py           file reading + RTA routing
│   ├── mapping.py                 three column-mapping dictionaries, nothing else
│   ├── etl_investor_master.py     bronze writer, investor entity
│   ├── etl_trans.py               bronze writer, transaction entity
│   ├── etl_sip.py                 bronze writer, SIP entity
│   ├── transformations/
│   │   └── transform.py           the entire silver layer, one file
│   ├── gold_loader.py             gold orchestrator
│   ├── etl_gold_<entity>.py       one gold module per business entity (8 of them)
│   ├── scheme_mapping.py          RTA-to-AMFI matching engine (separate branch)
│   ├── scheme_matching/           supporting package for the matching engine
│   ├── tests/                     pytest suite (matching engine only)
│   ├── utils/
│   │   ├── db.py                  the two SQLAlchemy engines + a preview helper
│   │   ├── utils.py               two small helpers
│   │   └── triggers.py            DDL for updated_at triggers
│   └── requirements.txt
├── sql_scripts/                   hand-run DDL and seed data
├── scheme_mapping_analysis/       generated reports and a standalone validator
└── docs/superpowers/              design docs and plans
```

Observations that matter when copying the layout:

- Layer membership is expressed only by **filename prefix**, never by directory. Bronze writers are
  `etl_*.py`, gold writers are `etl_gold_*.py`, and the whole silver layer lives in one 1,813-line
  file. There is no `bronze/` or `gold/` package.
- `utils/` is shared by every layer and is the only true module boundary.
- There is no `config/` directory. Configuration is hardcoded: credentials in `utils/db.py`,
  column mappings in `mapping.py`, tuning constants scattered across the modules that use them.
- A `python_scripts/.env` file exists with the right keys and is never read. No `os.getenv` call
  exists anywhere in the project.

### 1.2 File naming convention

| Pattern | Layer | Example |
|---|---|---|
| `raw_ingestion.py` | ingestion | single file, all sources |
| `mapping.py` | config | single file, all entities |
| `etl_<entity>.py` | bronze | `etl_trans.py`, `etl_sip.py` |
| `transformations/transform.py` | silver | one file for all entities |
| `etl_gold_<entity>.py` | gold | `etl_gold_holdings.py` |
| `gold_loader.py` | gold orchestrator | — |
| `utils/<concern>.py` | shared | `db.py`, `triggers.py` |

Entity names are inconsistent across layers — `etl_trans.py` writes `transaction_master_new`,
and `etl_gold_transaction.py` writes `transactions`. Pick one entity name per pipeline and use it
in the module name, the function names and the table name.

### 1.3 Function structure

The ETL layer contains **no classes at all** — it is entirely module-level functions plus
module-level dictionaries. Only `scheme_matching/` uses `@dataclass` (`SchemeKey`, `Candidate`,
`MatchContext`). Follow that split: plain functions for the pipeline, dataclasses only where you
need a structured value object.

**Bronze module contract** — each of the three bronze writers implements the same six functions:

```
clean_columns(df)                          normalise header names
normalize(df)                              scrub values to strings
clean_identifier_columns(df)               strip trailing ".0" from IDs
format_dates(df)             or (df, source)   parse date columns
apply_<entity>_mapping(raw_df, mapping, source)   rename to the unified schema
process_<entity>(cams=None, kfin=None)     the public entry point; writes to bronze
```

Note that the first four are **copy-pasted into all three files** with small divergences — SIP
upper-cases values for comparison and the others do not, investor's `clean_columns` omits the
duplicate-column drop. That duplication is a maintenance liability; the new pipeline should put
these in one shared module.

**Silver module contract** — one file, with helpers plus one transform per entity:

```
safe_read(query)                    read wrapper that swallows exceptions
get_last_processed_time(table)      MAX(created_at) watermark
normalize_for_compare(df)           canonicalise before key building
create_row_key(df)                  build the dedupe key
get_table_columns(table)            information_schema lookup for column order
append_new_rows(df, table)          watermark filter + anti-join + insert
transform_<entity>(df)              per-entity cleaning and standardisation
round_decimal_columns(df)           round all floats to 4 places
load_silver()                       the public entry point
```

**Gold module contract** — every gold module is exactly three functions, and the orchestrator
calls them in that order:

```
extract_<entity>()          -> DataFrame     read from silver
transform_<entity>(df)      -> DataFrame     business logic
load_<entity>(gold_df)      -> bool          anti-join + insert
```
cams-WBR36-36H-56-68
`gold_loader.load_gold()` then runs the eight entities sequentially, each wrapped in its own
`try/except Exception`.

**Module tail convention** — every runnable module ends with:

```python
if __name__ == "__main__":
    <entry_point>()
```

13 modules have one. The three bronze writers do not, because they take Streamlit `UploadedFile`
objects rather than paths — which is why there is no way to run ingestion from the command line.
The new pipeline should accept file paths so this is possible.

### 1.4 Database interaction convention

- Two module-level SQLAlchemy engines in `utils/db.py`, imported directly everywhere.
- Reads via `pd.read_sql(query, engine)`.
- Writes via `df.to_sql(name, engine, schema=, if_exists="append", index=False, method="multi", chunksize=N)`.
- Deduplication by pulling the destination table into pandas and anti-joining on a string key.
- No transactions in the layer path; no `ON CONFLICT`; no indexes on any fact table.

### 1.5 Which conventions to keep, and which not to

Keep — these make the new pipeline readable to the existing team:

- The three-function gold contract (`extract_` / `transform_` / `load_`).
- The `if __name__ == "__main__":` tail on every runnable module.
- Layer naming in the module name.
- A single mapping module holding the column configuration, separate from logic.
- The `safe_read` idea of a central read wrapper — but see below for how it should behave.

Do not carry over — each of these is a defect measured in the existing pipeline:

| Existing behaviour | Why not to copy it |
|---|---|
| Merging both RTAs' aliases into one list per target column | Root cause of the largest data loss in the current pipeline. Aliases are matched with `.lower().strip()` while headers are normalised with spaces converted to underscores, so **any alias containing a space can never match**. 10 KFIN SIP columns and 35 KFIN investor columns are lost this way, including PAN, date of birth, mobile, all address lines and every nominee-1 field. Define one explicit mapping block per source instead. |
| `safe_read` returning an empty DataFrame on error | Turns a dropped table or renamed column into "no new records", and the run reports success. Let the exception propagate, or return a result object that the caller must check. |
| `try/except Exception: print(e)` per entity in the orchestrator | A failed dimension load lets dependent entities join stale data while the UI still reports success. Fail the run, or track per-entity status and surface it. |
| Full-table read to answer "does this key exist" | Measured cost: 583 MB and 5.8 s per read of `silver.transaction_master_new`; roughly 4 GB per pipeline run. Use a unique constraint and `ON CONFLICT` instead. |
| No unique constraints on fact tables | 13 of 14 existing tables have none, so `ON CONFLICT` is impossible and every uniqueness guarantee is non-atomic Python. Create the constraints with the tables. |
| Autocommit `to_sql` with no transaction | A mid-load failure leaves committed chunks with no marker and no rollback path. |
| Storing every column as `text` | 114 of 116 columns in `silver.transaction_master_new` are `text`. This is what makes a 128,766-row table cost 583 MB in pandas when PostgreSQL scans it in 26 ms. Declare real types. |
| Hardcoded credentials in `utils/db.py` | Committed to git as `postgres`/`postgres`, with an unread `.env` sitting next to it. Read configuration from the environment. |
| Cast lists naming columns that do not exist | `transform_transaction` casts `trade_date`, `load_amount`, `broker_percent`; the real columns are `traddate`, `load`, `brokperc`. Validate the config against the schema at startup. |
| Module-level `pd.read_sql` at import time | Importing `etl_gold_scheme` queries the database, so starting Streamlit hits it before rendering. Put all reads inside functions. |
| Random `uuid.uuid4()` for durable row IDs | `gold.holdings.id` is regenerated on every run, breaking `folio_nominees.holding_id` references. Use `uuid.uuid5()` over a stable natural key, as `etl_gold_scheme.py` correctly does. |
| Dropping rows with no record of it | 10,862 transactions vanish between silver and gold with no reject table and no counter. Write rejects somewhere. |
| `print()` as the only diagnostic channel | No logging framework exists; all output goes to a terminal the user never sees. Use `logging`. |

The repository already contains correct versions of several of these patterns, all inside
`scheme_matching/` and `scheme_mapping.py`. Read these before designing anything:

| Pattern worth copying | Where it lives |
|---|---|
| Parameterised upsert: `INSERT ... ON CONFLICT (...) DO UPDATE SET ...` | `python_scripts/scheme_mapping.py:840` |
| Atomic write inside `with engine.begin() as conn:` | `python_scripts/scheme_matching/reference.py:58` |
| Bound-parameter list predicate: `WHERE col = ANY(:codes)` | `python_scripts/scheme_matching/nav_verify.py:42` |
| Audit table replaced atomically (`TRUNCATE` + insert in one transaction) | `reference.py:write_audit` |
| Human-review queue that preserves already-decided rows | `reference.py:write_review` |
| Pure-function rule registry with confidence arbitration | `scheme_matching/rules.py` |
| Deterministic ID from a fixed namespace: `uuid.uuid5(NAMESPACE, key)` | `etl_gold_scheme.py:718` |
| A test suite that pins current behaviour before refactoring | `python_scripts/tests/` |

---

## 2. Isolation Rules — Not Disturbing the Existing Code

These are the hard constraints. Everything else in this document is a recommendation.

1. **Do not edit any file under `python_scripts/`.** Not `mapping.py`, not `raw_ingestion.py`, not
   `utils/db.py`. Adding a new RTA by extending `mapping.py` and the routing chain in
   `raw_ingestion.py:247` would work, but it changes the alias-resolution outcome for CAMS and
   KFIN — first-match-wins means a new alias can silently steal a column from an existing source.

2. **Do not import from `python_scripts/`.** Two of its modules run `pd.read_sql` at import time
   (`etl_gold_scheme.py:26`, `etl_gold_holdings.py:17`), so any import pulls in database
   connections and the hardcoded credentials. If you want `scheme_matching/`, copy the package into
   the new tree and adapt its engine import.

3. **Use separate database objects.** Two options, pick one and write it down:

   - **Option A — separate schemas** (recommended). Create `bronze_<src>`, `silver_<src>`,
     `gold_<src>`. Total isolation: no shared tables, no shared constraints, no risk to existing
     data. Consolidation into the shared gold layer becomes a later, deliberate decision.
   - **Option B — separate tables in the existing schemas**, e.g. `bronze.<src>_transactions`.
     Less clutter, but you are now one typo away from writing to a live table, and the shared
     `bronze.update_updated_at()` trigger function is in scope.

   Do **not** write into the existing `bronze.transaction_master_new`, `silver.*` or `gold.*`
   tables. Those tables have no unique constraints, so a mistake cannot be undone by a re-run.

4. **Use your own connection module.** `new_pipeline/utils/db.py`, reading from the environment.
   Do not import `python_scripts/utils/db.py`.

5. **Use your own virtual environment** if your dependency versions differ. The existing
   `python_scripts/venv/` is Python 3.10.12 with pandas 2.3.3, SQLAlchemy 2.0.51, unpinned in
   `requirements.txt`. Pin your versions.

6. **Own your DDL.** `new_pipeline/sql/` with numbered migration files. Do not add to
   `sql_scripts/`, which is hand-run and partly schema-less
   (`investor_master.sql` and `transaction.sql` have no schema qualifier and land in whatever
   `search_path` provides).

7. **Do not modify `.claude/settings.local.json`, `.gitignore`, or the existing `requirements.txt`.**

---

## 3. Blueprint

Work through the steps in order. Each has an explicit definition of done. Do not start step N+1
until step N's check passes — the existing pipeline's problems nearly all come from a later layer
assuming an earlier one did something it did not.

### Step 0 — Profile the source before designing anything

Not optional. Half the defects in the existing pipeline exist because the mapping was written from
a specification rather than from the files.

For every file type the new source produces, record:

- Exact header row, verbatim, including case, spaces, `#` characters and trailing blanks.
- File encoding, delimiter, quote character.
- Row count and file size.
- For `.xlsx`: sheet count, sheet names, header row index, whether any preamble rows exist.
- Sample values per column — especially date format (`DD/MM/YYYY` vs `MM/DD/YYYY` is not
  inferable from one row), decimal separator, and how nulls are represented.
- The natural key: which columns uniquely identify a row.

Write this into `new_pipeline/docs/<source>-profile.md` and treat it as the contract. Every entry
in the mapping config must be traceable to a line in this document.

**Done when:** the profile document lists every column of every file type, and you can state the
natural key for each entity.

### Step 1 — Ingestion module

Build: `new_pipeline/ingestion/reader.py` and `new_pipeline/ingestion/router.py`.

`reader.py` responsibilities — read a file path into a raw DataFrame, doing nothing else:

- Accept a `pathlib.Path`, not a Streamlit upload object. This is what makes the pipeline runnable
  from the command line, cron and tests. Add a thin adapter later if you want a UI.
- Dispatch on **detected format**, not on the string after the last dot. The existing code sends
  anything that is not `.csv`/`.txt` to `pd.read_excel`, so an `.xls` fails on a missing `xlrd` and
  an unknown extension fails obscurely.
- Excel: read all columns as strings, keep blanks as empty strings, and **name the sheet
  explicitly**. `pd.read_excel` defaults to the first sheet only, so a multi-sheet workbook loses
  everything after sheet 0 with no warning. If the source ever ships multiple sheets, iterate
  `pd.ExcelFile(path).sheet_names`.
- Excel cannot be chunked — `read_excel` has no `chunksize`. If files will exceed available memory,
  convert to CSV first and stream, or read per sheet.
- CSV/TXT: prefer `pd.read_csv` with explicit `encoding`, `sep`, `quotechar`, `dtype=str`,
  `keep_default_na=False`. Only hand-roll a `csv.reader` loop if the source is genuinely malformed.
  Take the encoding and delimiter from config, not from sniffing line 1.
- Return the raw frame plus a metadata record: source path, row count, column count, sha256 of the
  file bytes, ingested-at timestamp. Persist that record — it becomes your `source_file_id`, which
  the existing pipeline declares as a column and never populates.

`router.py` responsibilities — decide what a file *is*:

- Match on config-declared patterns, compiled regex, not `str.endswith`. The existing CAMS routing
  requires the literal suffix `r2.csv`, so a CAMS `.xlsx` is read and then silently discarded.
- Match format and entity independently: entity comes from the filename pattern, format from the
  file itself.
- An unrecognised file must **raise or be recorded as a rejection**. The existing code prints
  `Unknown file type` to a terminal nobody reads and continues.

**Done when:** given a directory of sample files, the module returns `(entity, format, DataFrame,
metadata)` for each recognised file and a typed rejection for each unrecognised one — with no
database connection involved.

### Step 2 — Column mapping config

Build: `new_pipeline/config/mapping_<source>.py` from the template in section 5.

Design rules, each one a direct response to a measured failure:

1. **One explicit block per source and entity.** Not a merged alias list. Merged lists with
   first-match-wins are why the existing pipeline silently maps the wrong column when a new source
   is added, and why `branch` holds two different business fields for CAMS and KFIN despite a
   comment in `mapping.py:590` warning against exactly that.

2. **Store the source header verbatim.** Do the normalisation in code, applying the *same*
   function to the config value and the file header. The existing bug is that headers get
   `" " -> "_"` and aliases do not.

3. **Declare the target type.** `text`, `date`, `numeric(p,s)`, `integer`, `boolean`, `uuid`. This
   value drives three things: the generated DDL, the cast in the silver transformer, and a
   startup assertion that the config matches the live schema. The existing pipeline casts columns
   by hand-written name lists, three of which name columns that do not exist.

4. **Declare `required`.** A required column missing from an incoming file is a hard failure, not a
   silently NULL column. This one field would have caught every one of the 45 lost KFIN columns.

5. **Declare `date_format` per column.** Never rely on `pd.to_datetime` inference — it will read
   `03/04/2026` as either March or April depending on the rest of the column.

6. **Declare the natural key per entity**, and generate the unique constraint from it.

7. **Keep the config free of logic.** A pure data structure, importable and assertable without a
   database. That makes it testable, which `mapping.py` currently is not.

**Done when:** a config validator can load the config, compare it against the profile document
from step 0 and against the live schema, and report any column that is in one and not the others.

### Step 3 — Bronze writer

Build: `new_pipeline/bronze/writer.py` plus `new_pipeline/bronze/cleaners.py`.

Bronze here means: **structurally conformed, semantically untouched.** Rename columns, fix format
artefacts, add provenance. Do not standardise business values — that belongs in silver. Be aware
that the existing "bronze" already does date parsing and value mapping, which is why its name is
misleading.

`cleaners.py` — the shared helpers, written **once** rather than copied per entity:

- `normalize_header(name)` — the single function applied to both config values and file headers.
- `strip_float_artifacts(series)` — remove the trailing `.0` Excel adds to numeric-looking IDs.
  Drive this from the config's identifier flag, not a hardcoded column list.
- `blank_to_null(df)` — one convention for missing values, applied once. The existing code converts
  between `""`, `None`, `pd.NA`, `"nan"` and `"None"` at least four times per row.
- `parse_dates(df, spec)` — explicit format per column, from config.

`writer.py` responsibilities:

- Apply the mapping for the declared `(source, entity)`.
- **Validate before writing.** Required columns present; no unexpected columns silently dropped;
  row count matches the ingestion metadata. Raise or reject; do not proceed with NULLs.
- Attach provenance: `source`, `source_file_id`, `ingested_at`, `row_number_in_file`. That last one
  is what makes a rejected row traceable back to a spreadsheet line.
- Write inside a transaction, with `ON CONFLICT` on the declared natural key. The table must have
  the matching unique constraint — create it in the DDL, not later.
- Write rejects to `bronze_<src>.rejects` with the raw row, the rule that failed and the file id.
- Emit a load summary row: file id, rows read, rows inserted, rows updated, rows rejected. This is
  the record the existing pipeline has no equivalent of.

On re-uploads: an upsert on the natural key makes a re-upload idempotent. This is the single
biggest behavioural improvement over the existing bronze, which flags duplicates and inserts them
anyway, so re-uploading a file doubles the table.

**Done when:** loading the same file twice leaves the row count unchanged, loading a file with one
corrected value updates exactly that row, and a file with a missing required column loads nothing
and produces a reject record.

### Step 4 — Silver transformer

Build: `new_pipeline/silver/transformer.py` plus `new_pipeline/silver/rules.py`.

Silver means: **correctly typed and semantically standardised.**

Type casting — drive it entirely from the config's `type` field. Never a hand-maintained column
list. A cast failure on a non-nullable column is a rejection, not a silent `NaT`/`NaN`:

- `date` — explicit format, then reject unparseable values with the original string recorded.
- `numeric(p,s)` — reject unparseable; do not coerce to NULL.
- `integer` — reject non-integral.
- `text` — trim; empty string becomes NULL.

Value standardisation — put every lookup in `new_pipeline/config/lookups.py`, or better, in
database reference tables:

- One dictionary per business concept, used by every entity. The existing pipeline maps tax status
  `N` to `"N"` in the investor transform and to `"NRI"` in the transaction transform — the same
  input, two different silver values, because the dictionaries are duplicated per function.
- Unmapped values must be **recorded**, not silently passed through with `.fillna(original)`. An
  unmapped value is either a new legitimate code that belongs in the lookup, or bad data. Both
  need a human.
- Do not duplicate a database reference table in a Python dict. `transform.py:421` hardcodes an
  occupation map while `bronze.occupation_code` sits unused in the database, and the two have never
  been reconciled.

Business rules — `rules.py`, one pure function per rule, following the pattern in
`scheme_matching/rules.py`: each rule takes a row and context, returns a verdict, and is
independently testable. Cover at least:

- Null checks on natural-key columns.
- Range checks: dates within plausible bounds, amounts and units non-negative where required.
- Referential checks against reference tables.
- Cross-field consistency: for example `units * nav` reconciling to `amount` within tolerance.

Every rejection lands in `silver_<src>.rejects` with the rule name and the offending value.

Incremental loading — do not copy the existing watermark. `transform.py` sets silver's
`created_at` to the silver load time and then uses `MAX(created_at)` as the bronze cutoff; because
silver's timestamp is always later than the bronze row it came from, a slow run can advance the
watermark past rows that were never processed. Instead:

- Keep the source `ingested_at` unchanged through silver, and add a separate
  `silver_processed_at`, **or**
- Maintain an explicit `<src>_load_state` table recording the last successfully processed file id
  or watermark, updated in the same transaction as the data.

Write with `ON CONFLICT` on the natural key.

**Done when:** every column in silver has its declared type in `information_schema`, a
deliberately corrupted input row appears in `silver_<src>.rejects` with the rule name, and
re-running the transformer twice produces no change.

### Step 5 — Gold aggregator

Build: `new_pipeline/gold/<entity>.py` per entity, plus `new_pipeline/gold/loader.py`.

Gold means: **business entities at the grain a consumer needs.** Two things to settle before
writing code, both of which the existing pipeline got wrong:

**Declare the grain of every table, in writing, before implementing it.** The existing
`gold.holdings` is documented as a position table but is built one row per transaction: 128,766
rows for 3,591 distinct `(rta, folio_number, scheme_id)` combinations, a 36× inflation, with
`invested_amount`, `avg_cost_nav`, `current_value`, `unrealised_gain` and `xirr` present as columns
and never populated. `gold.folio_nominees` then joins nominees to that inflated ledger and reaches
378,735 rows where roughly 10,000 are real — the largest table in the database, about 97%
redundant. The downstream consumer re-aggregates it itself; see
`intelli-wealth-backend/app/modules/gold_sync/sync.py:130`.

So: for each gold table write down the grain, the natural key, and the measured row count you
expect. Then assert it after the load. A row count more than a small factor above the distinct
count of the natural key means the grain is wrong.

**Separate ledgers from positions.** A transaction ledger is one row per event, append-only. A
position table is one row per `(client, folio, scheme)` derived by aggregating the ledger. If you
need both, build both, and give them different names.

Per entity:

- `extract_<entity>()` — read silver with the incremental filter **in the SQL**, selecting only the
  columns needed. The existing `etl_gold_transaction.py:133` computes a watermark and then issues
  `SELECT *` with no `WHERE`, filtering afterwards in pandas.
- `transform_<entity>(df)` — the aggregation. Prefer SQL `GROUP BY` over `pandas.groupby` for
  anything that fits in a query; the existing gold layer contains no SQL aggregation at all beyond
  two `MAX(pan)` subqueries, and one of those resolves a client's PAN lexicographically.
- `load_<entity>(gold_df)` — upsert on the declared natural key, inside a transaction.

Deterministic IDs — `uuid.uuid5(NAMESPACE, natural_key)` with a module-level fixed namespace,
never `uuid4()`. Random IDs break every downstream reference on each reload.

Dimension load order — declare dependencies explicitly and **stop on failure**. The existing
orchestrator catches each of eight exceptions separately, so a failed `gold.scheme` lets
`scheme_nav`, `transactions` and `holdings` join a stale dimension while the UI reports success.

**Done when:** each gold table's row count matches its declared grain within the asserted
tolerance, a re-run changes nothing, and a forced failure in a dimension aborts the run with a
non-zero exit status.

### Step 6 — Wire it together and prove it works

- `main.py` — argparse CLI: `--source`, `--entity`, `--layer`, `--path`, `--dry-run`. Exit non-zero
  on failure. This is the piece the existing pipeline lacks entirely; its bronze writers can only
  be reached through the Streamlit UI.
- `logging` with a level from the environment. Not `print`.
- Tests, at minimum:
  - Mapping config validates against the profile document and the live schema.
  - Reader returns the expected shape for one sample file per format.
  - Idempotency: load twice, assert no change.
  - Rejection: corrupt one row, assert it lands in `rejects` with the right rule name.
  - Grain: assert each gold table's row count against its declared natural key.

The existing `python_scripts/tests/` suite is a good model for regression pinning — it freezes 223
known-good mappings in a CSV and fails if any changes. Note that it requires a live database
because `conftest.py` imports the engine at module scope; prefer fixtures that can run without one.

---

## 4. Starter Folder Structure

```
new_pipeline/
├── config/
│   ├── __init__.py
│   ├── settings.py                  env-driven config: DB URL, paths, batch sizes, log level
│   ├── mapping_<source>.py          column mapping — one block per entity (template in §5)
│   ├── lookups.py                   value-standardisation dictionaries, shared by all entities
│   └── file_patterns.py             filename regex -> (entity, format), and format specs
│
├── ingestion/
│   ├── __init__.py
│   ├── reader.py                    path -> raw DataFrame + file metadata; no DB access
│   ├── router.py                    classify file into (entity, format); reject unknowns
│   └── validators.py                required-column and shape checks before bronze
│
├── bronze/
│   ├── __init__.py
│   ├── cleaners.py                  normalize_header, strip_float_artifacts, blank_to_null, parse_dates
│   ├── writer.py                    apply mapping, validate, attach provenance, upsert
│   └── schema.py                    bronze table definitions generated from the mapping config
│
├── silver/
│   ├── __init__.py
│   ├── transformer.py               type casting + standardisation, driven by config
│   ├── rules.py                     one pure function per business rule
│   └── schema.py                    silver table definitions with real types
│
├── gold/
│   ├── __init__.py
│   ├── loader.py                    orchestrator: dependency order, fail-fast, per-entity status
│   ├── <entity>.py                  extract_/transform_/load_ per entity
│   └── schema.py                    gold table definitions, natural keys, declared grain
│
├── utils/
│   ├── __init__.py
│   ├── db.py                        engine factory from settings; NOT module-level credentials
│   ├── logging.py                   logger setup, level from env
│   ├── upsert.py                    the shared ON CONFLICT helper used by all three layers
│   └── audit.py                     load-summary and reject-table writers
│
├── sql/
│   ├── 001_schemas.sql              CREATE SCHEMA bronze_<src>, silver_<src>, gold_<src>
│   ├── 002_bronze_tables.sql        tables + UNIQUE on each natural key
│   ├── 003_silver_tables.sql        typed tables + UNIQUE + indexes on join/filter columns
│   ├── 004_gold_tables.sql          typed tables + UNIQUE + indexes
│   └── 005_audit_tables.sql         rejects, load_summary, load_state
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  fixtures that do not require a live DB
│   ├── fixtures/                    small sample files, one per format
│   ├── test_mapping_config.py       config vs profile vs live schema
│   ├── test_reader.py               shape and dtype per format
│   ├── test_idempotency.py          load twice, assert no change
│   ├── test_rejections.py           corrupt row lands in rejects with the right rule
│   └── test_gold_grain.py           row count vs distinct natural key
│
├── docs/
│   ├── <source>-profile.md          step 0 output: verbatim headers, formats, natural keys
│   └── grain.md                     declared grain and expected row count per gold table
│
├── main.py                          argparse CLI entry point
├── requirements.txt                 pinned versions
└── README.md
```

Notes on the structure:

- `config/`, `sql/`, `tests/` and `docs/` have no counterpart in the existing pipeline. They are
  where most of the fixes live.
- `utils/upsert.py` is the single most valuable file in the tree: one correct
  `INSERT ... ON CONFLICT DO UPDATE` helper, used by all three layers, replaces the
  full-table-download anti-join that costs roughly 4 GB per existing run.
- `schema.py` per layer keeps table definitions next to the layer that owns them and lets the DDL
  in `sql/` be generated rather than hand-written and drift.
- One module per gold entity, matching the existing `etl_gold_<entity>.py` convention so the
  three-function contract stays recognisable.

---

## 5. Column Mapping Config Template

Copy to `new_pipeline/config/mapping_<source>.py` and fill in. Every field is there because its
absence caused a measured defect in the existing pipeline.

```python
"""Column mapping for <SOURCE_NAME>.

Pure data. No imports beyond typing, no database access, no logic — so it can be
imported and asserted in tests without a connection.

Every entry must be traceable to a line in docs/<source>-profile.md.

SOURCE_COLUMN holds the header EXACTLY as it appears in the file, including case,
spaces and '#' characters. Normalisation is applied in code, by the same function,
to both this value and the incoming header. Never pre-normalise it here.
"""

# =====================================================================
# SOURCE IDENTITY
# =====================================================================

SOURCE_NAME = ""          # e.g. "FRANKLIN" — stored in the `source` column
SOURCE_VERSION = ""       # bump when the provider changes its file layout
PROFILE_DOC = "docs/<source>-profile.md"


# =====================================================================
# FILE RECOGNITION
# =====================================================================
# Regex, not str.endswith — the existing pipeline's endswith("r2.csv") check
# makes it impossible to ever route an Excel file for that entity.

FILE_PATTERNS = {
    # "entity_name": {
    #     "pattern":   r"",         # regex against the lowercased filename
    #     "formats":   [],          # ["csv", "xlsx"] — formats this entity may arrive in
    #     "required":  True,        # is this entity mandatory in a complete delivery?
    # },
}


# =====================================================================
# FORMAT SPECIFICATIONS
# =====================================================================
# Declared, never sniffed. Delimiter detection from line 1 misreads any file
# whose header happens to contain the wrong character.

FORMAT_SPECS = {
    "csv": {
        "encoding":       "",       # e.g. "utf-8" — from the profile, not a fallback chain
        "delimiter":      "",
        "quotechar":      "",       # note: existing CAMS/KFIN files use "'", not '"'
        "header_row":     0,
        "skiprows":       0,
        "strip_nulls":    True,     # remove \x00 — present in some RTA extracts
    },
    "xlsx": {
        "sheet_name":     "",       # ALWAYS explicit. Default 0 silently ignores every other sheet
        "all_sheets":     False,    # True to iterate pd.ExcelFile(path).sheet_names
        "header_row":     0,
        "skiprows":       0,
    },
}


# =====================================================================
# COLUMN MAPPING — one block per entity
# =====================================================================
# Keys are the TARGET column names. One block per entity per source; do NOT
# merge multiple sources' headers into an alias list. First-match-wins across
# merged sources is the single largest source of silent data loss in the
# existing pipeline.
#
# Field reference
#   source        str   verbatim header from the file. None = derived/injected, not read
#   type          str   "text" | "date" | "numeric(p,s)" | "integer" | "boolean" | "uuid"
#   nullable      bool  False -> a NULL after casting is a rejection, not a silent NULL
#   required      bool  True  -> column absent from the file aborts the load
#   identifier    bool  True  -> strip Excel's trailing ".0" (folios, PANs, phones, pincodes)
#   date_format   str   strptime format. Mandatory when type == "date". Never infer
#   trim          bool  strip surrounding whitespace
#   case          str   None | "upper" | "lower" | "title"
#   lookup        str   key into config/lookups.py, applied in SILVER not bronze
#   layer         str   "bronze" | "silver" | "gold" — earliest layer this appears in
#   notes         str   anything a future reader needs, especially provider quirks

TRANSACTION_MAPPING = {

    # ---------- provenance (injected, not read from the file) ----------
    "source": {
        "source": None, "type": "text", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": f"Literal {SOURCE_NAME!r}, injected by the bronze writer",
    },
    "source_file_id": {
        "source": None, "type": "uuid", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "From ingestion metadata. Populate it — the existing pipeline "
                 "declares this column on two gold tables and never fills it",
    },
    "row_number_in_file": {
        "source": None, "type": "integer", "nullable": False, "required": False,
        "identifier": False, "date_format": None, "trim": False, "case": None,
        "lookup": None, "layer": "bronze",
        "notes": "Makes a rejected row traceable to a spreadsheet line",
    },

    # ---------- natural key ----------
    # "folio_no": {
    #     "source": "", "type": "text", "nullable": False, "required": True,
    #     "identifier": True, "date_format": None, "trim": True, "case": "upper",
    #     "lookup": None, "layer": "bronze", "notes": "",
    # },
    # "txn_no": { ... },

    # ---------- dates ----------
    # "txn_date": {
    #     "source": "", "type": "date", "nullable": False, "required": True,
    #     "identifier": False, "date_format": "%d/%m/%Y", "trim": True, "case": None,
    #     "lookup": None, "layer": "bronze",
    #     "notes": "Confirm DD/MM vs MM/DD against the profile. CAMS uses "
    #              "'%m/%d/%Y %I:%M %p' and KFIN '%d/%m/%Y' for the same concept",
    # },

    # ---------- amounts ----------
    # "amount": {
    #     "source": "", "type": "numeric(20,4)", "nullable": True, "required": True,
    #     "identifier": False, "date_format": None, "trim": True, "case": None,
    #     "lookup": None, "layer": "bronze",
    #     "notes": "Declare the real type. Storing amounts as text is what makes "
    #              "a 128k-row table cost 583 MB in pandas",
    # },

    # ---------- coded values ----------
    # "txn_type_raw": {
    #     "source": "", "type": "text", "nullable": True, "required": True,
    #     "identifier": False, "date_format": None, "trim": True, "case": "upper",
    #     "lookup": "txn_type", "layer": "bronze",
    #     "notes": "Raw code kept as-is in bronze; standardised in silver via lookup",
    # },
}


INVESTOR_MAPPING = {
    # same field structure
}


SIP_MAPPING = {
    # same field structure
}


# =====================================================================
# ENTITY REGISTRY
# =====================================================================
# Natural keys generate the UNIQUE constraints that make ON CONFLICT possible
# and give the planner an index. 13 of 14 existing tables have neither.

ENTITIES = {
    # "transaction": {
    #     "mapping":       TRANSACTION_MAPPING,
    #     "bronze_table":  "bronze_<src>.transactions",
    #     "silver_table":  "silver_<src>.transactions",
    #     "natural_key":   ["source", "folio_no", "txn_no"],
    #     "date_columns":  [],   # DERIVED from mapping, never hand-listed.
    #                            # transform_transaction casts "trade_date" and
    #                            # "load_amount"; the real columns are "traddate"
    #                            # and "load". Generate this list, do not type it
    #     "chunksize":     5000,
    # },
}


# =====================================================================
# GOLD GRAIN DECLARATIONS
# =====================================================================
# Write the grain down before implementing, and assert it after each load.
# gold.holdings is documented as a position table and built at transaction
# grain: 128,766 rows for 3,591 distinct positions. gold.folio_nominees
# inherits that and reaches 378,735 rows where ~10,000 are real.

GOLD_GRAIN = {
    # "transactions": {
    #     "grain":            "one row per (source, folio_no, txn_no)",
    #     "natural_key":      ["source", "folio_no", "txn_no"],
    #     "kind":             "ledger",         # "ledger" | "position" | "dimension"
    #     "max_row_ratio":    1.0,              # rows / distinct(natural_key); assert after load
    #     "derived_from":     ["silver_<src>.transactions"],
    # },
    # "positions": {
    #     "grain":            "one row per (client_id, folio_no, scheme_id)",
    #     "natural_key":      ["client_id", "folio_no", "scheme_id"],
    #     "kind":             "position",
    #     "max_row_ratio":    1.0,
    #     "derived_from":     ["gold_<src>.transactions"],
    #     "notes":            "AGGREGATED from the ledger. Never one row per txn",
    # },
}


# =====================================================================
# VALIDATION CONTRACT
# =====================================================================
# Checked at startup by tests/test_mapping_config.py, before any data moves.

VALIDATION = {
    "assert_required_present":     True,   # required column missing -> abort
    "assert_no_unmapped_columns":  True,   # file column not in mapping -> abort, do not drop
    "assert_schema_match":         True,   # every target exists in the live table with this type
    "assert_lookups_resolve":      True,   # every `lookup` key exists in config/lookups.py
    "reject_table":                "bronze_<src>.rejects",
    "load_summary_table":          "bronze_<src>.load_summary",
    "on_cast_failure":             "reject",   # "reject" | "null" — never silently "null"
}
```

---

## 6. Order of Work

| # | Task | Output | Check before moving on |
|---|---|---|---|
| 1 | Profile every sample file | `docs/<source>-profile.md` | Every column of every file type listed verbatim; natural key stated per entity |
| 2 | Scaffold the tree in §4 | empty modules with docstrings | `python -c "import new_pipeline"` succeeds with no DB connection |
| 3 | Fill the mapping config | `config/mapping_<source>.py` | Config validator passes against the profile doc |
| 4 | Write the DDL | `sql/001`–`005` | Tables exist with declared types, UNIQUE on every natural key, indexes on join and filter columns |
| 5 | Build ingestion | `ingestion/` | Returns `(entity, format, df, metadata)` per file; unknown files rejected; no DB access |
| 6 | Build bronze | `bronze/` | Load twice → no change. Missing required column → nothing loaded, reject recorded |
| 7 | Build silver | `silver/` | Every column has its declared type in `information_schema`; corrupt row appears in rejects with the rule name |
| 8 | Build gold | `gold/` | Row counts match `GOLD_GRAIN` ratios; dimension failure aborts with non-zero exit |
| 9 | CLI and logging | `main.py`, `utils/logging.py` | Full run from the command line, non-zero exit on failure, no `print` |
| 10 | Tests | `tests/` | All pass; idempotency and grain assertions included |

Only after step 10 should you consider connecting the new gold tables to anything shared. That is a
separate decision with its own design discussion — the existing gold schema has no unique
constraints, so merging into it is not reversible by a re-run.

---

## 7. Reference — Where to Look in the Existing Code

| To understand | Read |
|---|---|
| File reading and RTA routing | `python_scripts/raw_ingestion.py:13` (`read_file`), `:229` (`extract_and_push`) |
| Column mapping shape | `python_scripts/mapping.py` |
| Bronze writer contract | `python_scripts/etl_trans.py:264` (`process_transactions`) |
| Silver layer, all of it | `python_scripts/transformations/transform.py:395` (`load_silver`) |
| Gold three-function contract | `python_scripts/etl_gold_sip.py:15,73,705` |
| Gold orchestration | `python_scripts/gold_loader.py:112` (`load_gold`) |
| The only correct upsert | `python_scripts/scheme_mapping.py:840` |
| Atomic write with transaction | `python_scripts/scheme_matching/reference.py:58` |
| Parameterised list predicate | `python_scripts/scheme_matching/nav_verify.py:42` |
| Testable rule registry | `python_scripts/scheme_matching/rules.py` |
| Deterministic UUID generation | `python_scripts/etl_gold_scheme.py:17,718` |
| Regression-pinning test suite | `python_scripts/tests/test_regression.py` |
| Layer-to-layer field mapping | `architecture_documentation.md` §4 |
| A worked design-and-plan pair | `docs/superpowers/specs/`, `docs/superpowers/plans/` |
| Downstream consumer of gold | `intelli-wealth-backend/app/modules/gold_sync/` (read-only, re-aggregates holdings) |
