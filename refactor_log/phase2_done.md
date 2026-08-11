# Phase 2 — ETL Helper Deduplication (2026-08-10)

1. **New file `common/etl_helpers.py`** — canonical home for shared Gold ETL helpers.
   `common/__init__.py` also created to make it a proper Python package.

2. **`safe_read(query)`** — identical across all four files → moved verbatim.
   All four files now import it from `common.etl_helpers`.

3. **`normalize_for_compare(df)`** — byte-for-byte identical in `etl_gold_folio_nominees`,
   `etl_gold_scheme_nav`, and `etl_gold_transaction` (absent in `etl_gold_amc`, which
   never used it) → moved verbatim. All three importing files now use the shared copy.

4. **`get_last_processed_time()` — DIVERGED (table name only).**
   Each file queried a different Gold table:
   - `etl_gold_amc`            → `gold.amc`
   - `etl_gold_folio_nominees` → `gold.folio_nominees`
   - `etl_gold_scheme_nav`     → `gold.scheme_nav`
   - `etl_gold_transaction`    → `gold.transactions`
   Logic was otherwise identical. **Resolution:** shared function parameterised as
   `get_last_processed_time(gold_table: str)`. Each file passes its own table name,
   preserving behaviour exactly.

5. **`create_row_key(df)` — DIVERGED (key columns differ per ETL).**
   - `etl_gold_folio_nominees` keys on `[holding_id, seq]`
   - `etl_gold_scheme_nav`     keys on `[scheme_id, nav_date]`
   - `etl_gold_transaction`    keys on `[rta, rta_txn_no]`
   - `etl_gold_amc`            never defined this function
   A single shared signature would silently use the wrong columns. **Not moved.**
   Each file retains its local copy; each now calls the shared `normalize_for_compare`
   from `common.etl_helpers`. A future phase may introduce `create_row_key(df, key_cols)`.

6. **Zero logic changes** — only the local bodies were deleted and replaced with imports;
   all behaviour is identical to before.

7. **Import smoke test ✅:**
   `python3 -c "import etl_gold_amc; import etl_gold_folio_nominees;
   import etl_gold_scheme_nav; import etl_gold_transaction;
   print('ALL FOUR ETL FILES IMPORT CLEANLY')"`
   → Output: `ALL FOUR ETL FILES IMPORT CLEANLY`

8. No other ETL, transform, or app files were touched in this phase.

9. **Functions moved to `common/etl_helpers.py`:** `safe_read`, `normalize_for_compare`,
   `get_last_processed_time` (parameterised). `create_row_key` intentionally left local.

10. **Files modified:** `etl_gold_amc.py`, `etl_gold_folio_nominees.py`,
    `etl_gold_scheme_nav.py`, `etl_gold_transaction.py`, + new `common/etl_helpers.py`.
