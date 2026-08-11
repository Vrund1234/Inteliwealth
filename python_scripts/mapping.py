# mapping.py — compatibility shim
# ---------------------------------
# This file has been moved to mappings/column_mappings.py (Phase 4 refactor).
# This shim re-exports everything so any direct execution or missed import
# still resolves.  Update all imports to:
#     from mappings.column_mappings import <NAME>
from mappings.column_mappings import (
    INVESTOR_MASTER_MAPPING,
    TRANSACTION_MASTER_MAPPING,
    SIP_MASTER_MAPPING,
)

__all__ = [
    "INVESTOR_MASTER_MAPPING",
    "TRANSACTION_MASTER_MAPPING",
    "SIP_MASTER_MAPPING",
]