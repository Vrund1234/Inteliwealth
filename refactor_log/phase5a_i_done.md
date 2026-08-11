# Phase 5a (Part I) - Silver Helpers Initial Extraction

Extracted four base helper functions from `python_scripts/transformations/transform.py` into `python_scripts/silver/silver_helpers.py`.

## Function Analysis vs Gold Equivalents (`common/etl_helpers.py`)

1. **`safe_read`**
   - **Action**: RE-EXPORTED from `common.etl_helpers`.
   - **Reason**: The logic was identical to the Gold version.

2. **`get_last_processed_time`**
   - **Action**: KEPT AS SEPARATE SILVER VERSION.
   - **Divergence**: Queries `silver.{table_name}` instead of taking a fully-qualified `gold_table` string, and returns a timezone-aware timestamp `pd.Timestamp("1900-01-01", tz="UTC")` as a fallback instead of a timezone-naive one.

3. **`normalize_for_compare`**
   - **Action**: KEPT AS SEPARATE SILVER VERSION.
   - **Divergence**: The Silver version explicitly drops three columns `["created_at", "updated_at", "flag"]` whereas the Gold version only drops `["created_at"]`.

4. **`create_row_key`**
   - **Action**: KEPT AS SEPARATE SILVER VERSION.
   - **Divergence**: The Silver version creates a hash key across ALL columns in the DataFrame (`df.fillna("").astype(str).agg("|".join, axis=1)`). In the Gold layer, `create_row_key` is specific to each ETL file and subsets to a specific composite natural key (e.g., `holding_id` + `seq` or `rta` + `rta_txn_no`).

## File Changes
- `python_scripts/silver/silver_helpers.py`: Created with the extracted definitions.
- `python_scripts/transformations/transform.py`: Replaced the 4 local function bodies with:
  ```python
  from silver.silver_helpers import (
      safe_read,
      get_last_processed_time,
      normalize_for_compare,
      create_row_key
  )
  ```

## Smoke Test ✅
`transform.py` still imports cleanly:
```
transform.py OK
```
