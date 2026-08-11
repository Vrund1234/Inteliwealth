# Phase A — Bronze Deduplication + Relocation into a `bronze/` Package (2026-08-10)

Deduplicated the five helpers that were copy-pasted across the three flat Bronze
ETL files and relocated all Bronze logic into a `bronze/` package that mirrors the
existing `silver/` package structure 1:1.

## New file paths

| Old path | New canonical path |
|---|---|
| `python_scripts/etl_investor_master.py` | `python_scripts/bronze/investor_master.py` |
| `python_scripts/etl_trans.py` | `python_scripts/bronze/transaction.py` |
| `python_scripts/etl_sip.py` | `python_scripts/bronze/sip.py` |
| *(new)* | `python_scripts/bronze/bronze_helpers.py` |
| *(new)* | `python_scripts/bronze/__init__.py` (empty, matching `silver/__init__.py`) |

Old files replaced with compatibility shims (same pattern as `mapping.py` from Phase 4),
each re-exporting only its public entrypoint:

- `etl_investor_master.py` → `from bronze.investor_master import process_investor_master`
- `etl_trans.py` → `from bronze.transaction import process_transactions`
- `etl_sip.py` → `from bronze.sip import process_sip`

Each shim went from ~640–870 lines to 13. Verified the shims re-export the *same*
function objects (`is` identity), not copies.

## Files whose imports were updated

| File | Old import | New import |
|---|---|---|
| `raw_ingestion.py` | `from etl_investor_master import process_investor_master` | `from bronze.investor_master import process_investor_master` |
| `raw_ingestion.py` | `from etl_trans import process_transactions` | `from bronze.transaction import process_transactions` |
| `raw_ingestion.py` | `from etl_sip import process_sip` | `from bronze.sip import process_sip` |

`raw_ingestion.py` uses the real `bronze/` package, not the shims — asserted in the
smoke test. A full-tree grep confirms **zero** live callers of the old flat module
names remain outside the shims themselves; `test_run.py` reaches Bronze only via
`raw_ingestion`, so it needed no change.

## Helper deduplication — identical vs. parameterised vs. kept separate

Every one of the five helpers was diffed line-by-line across all three files first.
This both confirms and extends the Phase 3 audit, which had flagged these same
functions as diverging but made zero changes.

### FULLY SHARED — moved verbatim

- **`clean_value(value)`** — `etl_trans` tested membership against a list `[...]`,
  `etl_sip` against a tuple `(...)`. That is the only textual difference and it has
  no effect on the result. `etl_investor_master` never defined it.
  ⚠️ **Note:** this function has **no call sites anywhere in the tree** — it was
  already dead code in both originals. Preserved as-is; deleting it is a separate
  cleanup, not a relocation concern.

- **`clean_identifier_columns`** — body **byte-identical** in `etl_investor_master`
  and `etl_trans`. `etl_sip` differed only in its empty-guard (below).

### DIVERGED — parameterised to resolve divergence

Same approach Phase 2 used for `get_last_processed_time(gold_table)`: one shared
implementation with the varying behaviour passed in explicitly, rather than forcing
a bad merge or keeping duplicates.

| Parameter | Divergence it resolves | Who passes it |
|---|---|---|
| `guard_empty` | `etl_sip` guarded with `if df is None or df.empty`; the other two with `if df is None`. Returning early on an empty frame is a real behavioural difference (otherwise the frame is `.copy()`-ed and column-renamed). | sip only |
| `quotes_before_whitespace` | **`etl_trans` strips quotes BEFORE whitespace; the other two strip whitespace BEFORE quotes.** Flagged by Phase 3. Matters for headers like `"' folio no '"`, where the two orders yield different column names — so neither order could be imposed on the other file. | transaction only |
| `dedupe_columns` | `etl_trans` and `etl_sip` drop duplicate columns (`~df.columns.duplicated(keep="first")`); `etl_investor_master` does not. | transaction + sip |
| `numeric_columns` | `etl_sip`'s `normalize` additionally coerced its `NUMERIC_COLUMNS` via `pd.to_numeric(errors="coerce")` and skipped string cleaning for them. When `None`, the numeric branch can never fire, so the other two keep their exact original behaviour. Branch order preserved exactly: date skip → numeric coerce → datetime skip → string clean. | sip only |
| `date_format` | `etl_sip` parses with an **explicit per-source format** (CAMS `%m/%d/%Y %I:%M %p` vs KFIN `%d/%m/%Y`); the other two let pandas infer. | sip only |

Two apparent divergences turned out to be **non-differences**, verified by the
parity test rather than assumed:

- `normalize` in `etl_investor_master` vs `etl_trans` differed only by **trailing
  whitespace on one blank line**.
- `format_dates` in `etl_investor_master` had `# dayfirst=False` commented out while
  `etl_trans` passed `dayfirst=False` explicitly. `dayfirst=False` is pandas' default,
  so the two were functionally identical; both now pass `date_format=None`, which omits
  the argument entirely. The parity fixtures deliberately include an ambiguous date
  (`01/02/2026`) so any real difference here would have failed the test.

### KEPT SEPARATE — deliberately local to each domain module

- **`DATE_COLUMNS`, `IDENTIFIER_COLUMNS`, `NUMERIC_COLUMNS`, `PERIODICITY_MAPPING`** —
  every one is domain-specific (investor_master's dates are `dob`/`folio_date`/…,
  transaction's are `traddate`/`postdate`/…, sip's are `from_date`/`to_date`/…).
  Centralising them would require either a union (making each domain clean columns that
  do not belong to it) or a per-domain lookup dict — both worse than a module-level list
  next to the code that uses it. Mirrors how `silver/investor_master.py` keeps domain
  logic local while importing only shared helpers.
- **The CAMS-vs-KFIN date-format choice** stays in `bronze/sip.py` as a thin wrapper —
  it is domain knowledge about how each RTA writes its files, not a generic helper concern.
- **`apply_*_mapping()` and `process_*()`** — one per domain, never shared.

Each domain module keeps thin same-named wrappers (`clean_columns(df)`, `normalize(df)`, …)
that pin its own column lists and flags, so the `process_*()` bodies are textually
unchanged from the originals.

## pyparsing cleanup

`etl_investor_master.py` line 4 carried a dead `from pyparsing import col`.

Confirmed safe before deleting:
- `grep -rn "col("` across the whole tree returns **zero** matches — pyparsing's `col()`
  is never invoked as a function. Every use of the name `col` is a `for col in ...` loop
  variable, which is function-local and shadows the module global, so nothing ever read
  the imported name.
- Import deleted; `pyparsing` is no longer in `sys.modules` after importing the tree.
- `pip show pyparsing` reports an empty `Required-by:` in the project venv → nothing
  depends on it transitively, so `pyparsing==3.3.2` was removed from `requirements.txt`
  (the package is still physically installed in the venv; nothing needs uninstalling).

## Other notes

- The ~160-line block of commented-out dead code trailing `etl_sip.py` (after its
  `return len(df)`, a stale duplicate of the same insert logic) was **not** carried over
  to `bronze/sip.py`. Comments only — zero behavioural effect. Commented-out blocks
  *inside* the functions were preserved verbatim.
- No changes to `utils/utils.py`. Its `clean_columns`/`clean_value` remain the weaker
  versions Phase 3 flagged; the Bronze helpers are a separate, richer lineage.

## Environment note

`python3` on this box (3.10) has no pandas — the project's real interpreter is the venv at
`python_scripts/venv` (Python 3.13, pandas 3.0.5 matching `requirements.txt`). All tests
below were run with `python_scripts/venv/bin/python`.

## Smoke test ✅

**1. Import test (proves both the new package and the shims work):**
```
venv/bin/python -c "import raw_ingestion; import etl_investor_master; import etl_trans; import etl_sip"
→ IMPORTS OK
→ shims re-export the SAME function objects as bronze/ — OK
→ raw_ingestion imports canonical bronze/ (not the shims) — OK
→ pyparsing imported anywhere? False
```
`compileall` over `bronze/`, the three shims, `raw_ingestion.py` and `app.py` exits 0.
`import app` succeeds (connects to `intelli_wealth_28_07_2026`).

**2. Helper-level parity — 45/45 byte-identical.** Captured every helper's output on
representative messy fixtures (nulls, whitespace, mixed case, `nan`/`None`/`<NA>`/`NaT`
sentinel strings, quoted/dashed/slashed header names, colliding duplicate columns, `.0`
identifier suffixes, unparseable dates, `None`/empty-DataFrame inputs) **before** the
change, then re-ran against `bronze/` and compared with
`pd.testing.assert_frame_equal(check_dtype=True, check_exact=True)`:
```
PARITY OK — all 45 captured results byte-identical (pre-change vs post-change)
```
Coverage included all three `apply_*_mapping()` functions and the domain constants.

**3. `process_*()` end-to-end parity — 21/21 identical.** Ran all three full pipelines
against in-memory samples with `pd.read_sql` stubbed and `to_sql` intercepted (no real DB
writes), comparing the exact DataFrame each version *would have inserted*. Reference side
was the pre-change implementation recovered from `git show HEAD:` (which differs from the
working tree only in the Phase 4 import lines, and still resolves through the live
`mapping.py` shim):
```
PROCESS PARITY OK — all 21 results identical
   im.payload: (10, 10)   im.cams_only.payload: (5, 10)
   tr.payload: (10, 10)   tr.kfin_only.payload: (5, 10)
   sp.payload: (10, 11)   sp.cams_only.payload: (5, 11)
```
This exercised the non-empty-`existing` duplicate-flag branch, the CAMS-only /
KFIN-only single-source paths, the `to_sql` kwargs (`chunksize`, `method`, `schema`),
the return values, and the "no files" early-return paths (confirmed to write nothing).

Zero behaviour changes. Every call site produces identical output to before.
