# etl_trans.py — compatibility shim
# ----------------------------------
# This file has been moved to bronze/transaction.py (Phase A refactor).
# Its five duplicated helpers (clean_columns, clean_identifier_columns,
# clean_value, format_dates, normalize) now live in bronze/bronze_helpers.py.
# This shim re-exports the public entrypoint so any missed import still
# resolves.  Update all imports to:
#     from bronze.transaction import process_transactions
from bronze.transaction import process_transactions

__all__ = [
    "process_transactions",
]
