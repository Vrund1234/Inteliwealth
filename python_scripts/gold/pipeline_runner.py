"""
gold/pipeline_runner.py
=======================
Generic extract -> check -> transform -> load runner for the Gold layer
(introduced in Phase 6 to remove the duplicated try/except blocks).

Phase B change
--------------
`run_gold_pipeline` used to only print().  A caller had no way to tell whether
a domain succeeded, found nothing, or blew up — the pipeline swallowed the
exception and returned None either way.  It now returns a structured result
dict and still never propagates a single domain's exception, so domain
isolation (a deliberate design choice) is preserved while the caller can
finally detect which domains failed.

Result shape
------------
    {
        "name":   str,                              # domain label
        "status": "ok" | "no_data" | "failed",      # (+ "skipped", see loader)
        "rows":   int,                              # rows loaded; 0 unless ok
        "error":  str | None,                       # exception text on failure
    }
"""


def make_result(name, status, rows=0, error=None):
    """Build a Gold pipeline result dict.

    Shared with `gold/loader.py` so the domains that cannot use the generic
    runner (Scheme, whose extract returns two DataFrames) still report in
    exactly the same shape.
    """
    return {
        "name": name,
        "status": status,
        "rows": rows,
        "error": error,
    }


def run_gold_pipeline(name, extract_fn, transform_fn, load_fn):
    """Run one Gold domain end-to-end and report the outcome.

    Never raises — a failing domain is reported as status="failed" so the
    remaining domains still run.
    """
    try:
        print(f"\nLoading Gold {name}")

        raw_df = extract_fn()

        if raw_df.empty:
            print(f"No {name} data found")
            return make_result(name, "no_data")

        gold_df = transform_fn(raw_df)

        if gold_df.empty:
            print(f"No new {name} records to load")
            return make_result(name, "no_data")

        load_fn(gold_df)

        print(f"{name} loaded successfully")
        return make_result(name, "ok", rows=len(gold_df))

    except Exception as e:
        # Phase D will replace these with logger.error(..., exc_info=True)
        print(f"{name} Gold Failed")
        print(e)
        return make_result(
            name,
            "failed",
            error=f"{type(e).__name__}: {e}",
        )
