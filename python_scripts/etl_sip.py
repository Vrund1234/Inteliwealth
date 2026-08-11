# etl_sip.py — compatibility shim
# --------------------------------
# This file has been moved to bronze/sip.py (Phase A refactor).
# Its five duplicated helpers (clean_columns, clean_identifier_columns,
# clean_value, format_dates, normalize) now live in bronze/bronze_helpers.py.
# This shim re-exports the public entrypoint so any missed import still
# resolves.  Update all imports to:
#     from bronze.sip import process_sip
from bronze.sip import process_sip

__all__ = [
    "process_sip",
]
