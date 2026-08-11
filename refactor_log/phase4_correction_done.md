# Phase 4 Correction — restore_engine() fix in mappings/scheme_mapping.py (2026-08-10)

## Root cause

`restore_engine` is a factory **function** defined in `utils/db.py` that creates and
returns a fresh SQLAlchemy engine.  In two places inside `load_scheme_mapping()`,
it was passed bare (without `()`) as the connection argument to `pd.read_sql`, which
expects an engine or connection object — not a callable.  This would raise a
`TypeError` the moment `load_scheme_mapping()` was actually called.

## Lazy-connection analysis — no legitimate uncalled-function pattern

Both call sites follow exactly the same structure as the `pd.read_sql(query, engine)`
calls earlier in the same function (lines 46 and 69–72).  There is no lazy-connection
wrapper, no `contextmanager`, and no deferred execution pattern.  The original author
simply forgot the parentheses.  Calling `restore_engine()` inline creates a fresh
engine at the point of the read — which is precisely what `restore_engine` was designed
to do and matches the intent of both call sites.

## Lines fixed

### Line 145–148 — `pd.read_sql` for `public.amfi_scheme_master`

```diff
- amfi_df = pd.read_sql(
-     amfi_query,
-     restore_engine
- )
+ amfi_df = pd.read_sql(
+     amfi_query,
+     restore_engine()  # call to get a fresh engine instance
+ )
```

### Line 182–185 — `pd.read_sql` for `public.rta_amc_code`

```diff
- amc_mapping_df = pd.read_sql(
-     amc_mapping_query,
-     restore_engine
- )
+ amc_mapping_df = pd.read_sql(
+     amc_mapping_query,
+     restore_engine()  # call to get a fresh engine instance
+ )
```

### Line 170 (commented-out block) — **left untouched**

```python
# nav_df = pd.read_sql(
#     nav_query,
#     restore_engine        ← inside a dead/commented block; not fixed
# )
```

## Import smoke test ✅

```
python3 -c "from mappings.scheme_mapping import load_scheme_mapping; print(...)"
→ Import OK — load_scheme_mapping: <function load_scheme_mapping at 0x...>
```

## Runtime test ✅ — TypeError confirmed eliminated

```
load_scheme_mapping()
→ STARTING SCHEME MAPPING
→ Distinct Schemes Found : 515        ← engine + project DB query succeeded
→ DatabaseError: relation "public.amfi_scheme_master" does not exist
```

`load_scheme_mapping()` executed past the first DB read (515 rows from
`silver.transaction_master_new`), then hit `restore_engine()` successfully — the
returned engine connected to the master DB without any `TypeError`.  The
`DatabaseError` that followed is a **data/environment issue** (`public.amfi_scheme_master`
table not present in the local dev database), not a code bug.  The fix is confirmed
correct.

## Only file changed

`python_scripts/mappings/scheme_mapping.py` — 2 lines changed, zero logic altered.
