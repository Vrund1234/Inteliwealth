# Phase 6 - Deduplicating Gold Loader

Refactored `gold_loader.py` to remove massive try/except block duplication by introducing a generic pipeline runner.

## Files Created
- **`python_scripts/gold/pipeline_runner.py`**: Introduced `run_gold_pipeline()`, which abstracts the standard `extract -> check -> transform -> load` sequence along with standard printing and error handling.

## File Updates
- **`python_scripts/gold_loader.py`**: Rewritten to utilize `run_gold_pipeline`. 
  - **Line count before**: 588 lines
  - **Line count after**: 129 lines
  - **Special Cases**: The `Scheme` domain did not fit the generic runner because `extract_scheme()` returns two DataFrames (`transaction_df, investor_df`), and `transform_scheme()` correspondingly requires two DataFrame arguments. Thus, the Scheme block was explicitly retained to avoid changing the operational signature.

## Smoke Test ✅
- `gold_loader.py` successfully imports the new runner and all its ETL dependencies without issue.
