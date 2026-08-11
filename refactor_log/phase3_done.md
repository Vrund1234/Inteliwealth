# Phase 3 — clean_columns / clean_value Deduplication Audit (2026-08-10)

> **Result: zero deletions.** Every local copy of `clean_columns` and `clean_value`
> contains extra logic absent from `utils/utils.py`. No file was changed.

---

## Reference — `utils/utils.py` canonical signatures

```python
def clean_columns(df):
    df.columns = (
        df.columns.astype(str)
        .str.lower()
        .str.strip()
        .str.replace(" ", "_", regex=False)
        .str.replace("#", "", regex=False)
    )
    return df

def clean_value(x):
    if pd.isna(x):
        return None
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    return x
```

Notable absences in the shared version:
- No null/empty-DataFrame guard
- No `.copy()`
- No quote-stripping (`strip("'")`, `strip('"')`)
- No `-` → `_` or `/` → `_` replacement
- No duplicate-column deduplication
- `clean_value`: no `str()` cast, no lowercase sentinel check, no `None` return for
  blank / `"nan"` / `"none"` / `"<na>"` / `"nat"` strings

---

## etl_investor_master.py — `clean_columns` DIVERGED, `clean_value` NOT DEFINED

### `clean_columns` — extra logic vs utils.py (DO NOT DELETE)

```python
def clean_columns(df):
    if df is None:          # ← null guard absent in utils.py
        return df
    df = df.copy()          # ← defensive copy absent in utils.py
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")     # ← quote-stripping absent in utils.py
        .str.strip('"')     # ← quote-stripping absent in utils.py
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)   # ← absent in utils.py
        .str.replace("/", "_", regex=False)   # ← absent in utils.py
        .str.replace("#", "", regex=False)
    )
    return df
```

**Specific extras:** null guard; `.copy()`; quote-stripping; `-`→`_`; `/`→`_`.

### `clean_value` — **not defined** in this file; no action needed.

---

## etl_sip.py — both `clean_columns` and `clean_value` DIVERGED

### `clean_columns` — extra logic vs utils.py (DO NOT DELETE)

```python
def clean_columns(df):
    if df is None or df.empty:   # ← null/empty guard absent in utils.py
        return df
    df = df.copy()               # ← defensive copy absent in utils.py
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")          # ← quote-stripping absent in utils.py
        .str.strip('"')          # ← quote-stripping absent in utils.py
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)   # ← absent in utils.py
        .str.replace("/", "_", regex=False)   # ← absent in utils.py
        .str.replace("#", "", regex=False)
    )
    # Keep first duplicate column if any      # ← absent in utils.py
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df
```

**Specific extras:** null/empty guard; `.copy()`; quote-stripping; `-`→`_`; `/`→`_`;
**duplicate-column deduplication** (`~df.columns.duplicated(keep="first")`).

### `clean_value` — extra logic vs utils.py (DO NOT DELETE)

```python
def clean_value(value):
    if pd.isna(value):
        return None
    value = str(value).strip()       # ← always casts to str; utils.py returns x as-is
    if value.lower() in (            # ← sentinel check absent in utils.py
        "", "nan", "none", "<na>", "nat"
    ):
        return None
    return value                     # ← returns str; utils.py returns original x
```

**Specific extras:** `str()` cast + `.strip()`; lowercase sentinel check returning `None`
for blank / `"nan"` / `"none"` / `"<na>"` / `"nat"`; always returns a string, not
the original typed value (behavioural divergence from `utils.py`'s `return x`).

---

## etl_trans.py — both `clean_columns` and `clean_value` DIVERGED

### `clean_columns` — extra logic vs utils.py (DO NOT DELETE)

```python
def clean_columns(df):
    if df is None:          # ← null guard absent in utils.py
        return df
    df = df.copy()          # ← defensive copy absent in utils.py
    df.columns = (
        df.columns.astype(str)
        .str.strip("'")     # ← quote-stripping (applied BEFORE .strip()) — absent in utils.py
        .str.strip('"')     # ← quote-stripping — absent in utils.py
        .str.strip()        # NOTE: order differs from etl_investor_master / etl_sip
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)   # ← absent in utils.py
        .str.replace("/", "_", regex=False)   # ← absent in utils.py
        .str.replace("#", "", regex=False)
    )
    # Keep first duplicate column   # ← absent in utils.py
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df
```

**Specific extras:** null guard; `.copy()`; quote-stripping (in different order than
the other two files — strips quotes *before* whitespace); `-`→`_`; `/`→`_`;
**duplicate-column deduplication**. The strip-order difference may matter when
column headers contain leading/trailing spaces *inside* quotes.

### `clean_value` — extra logic vs utils.py (DO NOT DELETE)

Identical to `etl_sip.py`'s version (same extras: `str()` cast, lowercase sentinels).

---

## Recommendation for a future phase

Before merging any of the above into `utils/utils.py`, decide:

1. **Quote-stripping order:** `etl_trans` strips quotes *before* whitespace; the other
   two do it *after*. Pick one canonical order.
2. **Return type of `clean_value`:** `utils.py` returns the original typed value `x`;
   the local copies always return a `str`. Callers expecting Pandas scalars will behave
   differently depending on which version they use.
3. **`clean_value` Timestamp branch:** `utils.py` has `isinstance(x, pd.Timestamp)`
   → `.to_pydatetime()` which the local copies lack entirely.

---

## Summary table

| File | `clean_columns` action | `clean_value` action |
|---|---|---|
| `etl_investor_master.py` | **FLAGGED** — extra logic; kept local | not defined — no action |
| `etl_sip.py` | **FLAGGED** — extra logic; kept local | **FLAGGED** — extra logic; kept local |
| `etl_trans.py` | **FLAGGED** — extra logic; kept local | **FLAGGED** — extra logic; kept local |

No files were modified in Phase 3.
