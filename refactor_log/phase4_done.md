# Phase 4 — Mapping File Relocation (2026-08-10)

## New file paths

| Old path | New canonical path |
|---|---|
| `python_scripts/mapping.py` | `python_scripts/mappings/column_mappings.py` |
| `python_scripts/scheme_mapping.py` | `python_scripts/mappings/scheme_mapping.py` |
| *(new)* | `python_scripts/mappings/__init__.py` |

Old files replaced with compatibility shims:
- `mapping.py` → re-exports `INVESTOR_MASTER_MAPPING`, `TRANSACTION_MASTER_MAPPING`,
  `SIP_MASTER_MAPPING` from the new location.
- `scheme_mapping.py` → delegates `__main__` execution to `mappings.scheme_mapping`
  via `runpy.run_module`.

## Files whose imports were updated

| File | Old import | New import |
|---|---|---|
| `etl_investor_master.py` | `from mapping import INVESTOR_MASTER_MAPPING` | `from mappings.column_mappings import INVESTOR_MASTER_MAPPING` |
| `etl_sip.py` | `from mapping import SIP_MASTER_MAPPING` | `from mappings.column_mappings import SIP_MASTER_MAPPING` |
| `etl_trans.py` | `from mapping import TRANSACTION_MASTER_MAPPING` | `from mappings.column_mappings import TRANSACTION_MASTER_MAPPING` |

No file imported `scheme_mapping` — it is only ever run as `__main__`.

## Import smoke test — all PASS ✅

```
mappings.column_mappings OK — keys: ['source', 'folio_no', 'investor_name'] ...
mappings.scheme_mapping  OK — exports: engine, fuzz, load_scheme_mapping,
                               normalize_scheme_name, pd, process, re, restore_engine, text, uuid
etl_investor_master      OK
etl_sip                  OK
etl_trans                OK
restore_engine import    OK — type: <class 'function'>
```

## restore_engine import — RESOLVED ✅ (with runtime bug flagged)

`from utils.db import engine, restore_engine` in `mappings/scheme_mapping.py`
resolves cleanly — `restore_engine` is present as Phase 1 added it.

⚠️ **Runtime bug (not touched in this phase):**
`restore_engine` is passed directly to `pd.read_sql(...)` as a connection object
on lines 148 and 184 of `mappings/scheme_mapping.py`:

```python
amfi_df     = pd.read_sql(amfi_query,       restore_engine)   # line 148
amc_mapping = pd.read_sql(amc_mapping_query, restore_engine)  # line 184
```

`restore_engine` is a **function**, not an engine — this will raise a `TypeError`
at runtime when `load_scheme_mapping()` is called.  The correct call is
`restore_engine()` (i.e., call it to obtain a fresh engine).  Fix in a later phase.
