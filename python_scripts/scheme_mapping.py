# scheme_mapping.py — compatibility shim
# ----------------------------------------
# This file has been moved to mappings/scheme_mapping.py (Phase 4 refactor).
# This shim delegates __main__ execution so running this file directly still works.
import runpy, sys

if __name__ == "__main__":
    sys.argv[0] = __file__.replace("scheme_mapping.py", "mappings/scheme_mapping.py")
    runpy.run_module("mappings.scheme_mapping", run_name="__main__", alter_sys=True)