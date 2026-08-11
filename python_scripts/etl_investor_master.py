# etl_investor_master.py — compatibility shim
# --------------------------------------------
# This file has been moved to bronze/investor_master.py (Phase A refactor).
# Its five duplicated helpers (clean_columns, clean_identifier_columns,
# clean_value, format_dates, normalize) now live in bronze/bronze_helpers.py.
# This shim re-exports the public entrypoint so any missed import still
# resolves.  Update all imports to:
#     from bronze.investor_master import process_investor_master
from bronze.investor_master import process_investor_master

__all__ = [
    "process_investor_master",
]
