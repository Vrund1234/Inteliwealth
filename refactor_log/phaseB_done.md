# Phase B — Fail Loudly Instead of Silently (2026-08-10)

Made real database errors distinguishable from "no new rows", and gave the caller a
structured, machine-readable result per Gold domain instead of console output only.

## Files changed

| File | Change |
|---|---|
| `common/etl_helpers.py` | `safe_read()` logs and **re-raises** instead of returning an empty DataFrame |
| `gold/pipeline_runner.py` | `run_gold_pipeline()` returns a structured result dict; new `make_result()` helper |
| `gold/loader.py` | `load_gold()` returns `list[dict]`; Scheme special case reports in the same shape; optional-import failures are captured, not discarded |
| `app.py` | Surfaces failed / skipped Gold domains via `st.error` / `st.warning` |

## 1. `safe_read()` — log + re-raise

Before, `except Exception: print("SQL ERROR :", e); return pd.DataFrame()`. Since every
caller branches on `df.empty`, a missing table, bad credentials or a dropped connection
looked *exactly* like a legitimate empty result — a broken run reported itself as a
successful no-op.

Now it logs the exception type, the message, and the offending query (whitespace-collapsed
and truncated to 300 chars), then `raise`. The two `print()` calls are deliberately
structured as a single `[ERROR] …` message each so Phase D can swap them for
`logger.error(..., exc_info=True)` — the intended replacement is written out in a comment
at the call site.

### Call-site audit — all 13 `safe_read()` callers checked before changing the contract

| Location | Count | Verdict |
|---|---|---|
| `gold/amc.py` (22, 102) | 2 | **No change.** Both inside `extract_amc`/`transform_amc`, which run under `run_gold_pipeline`'s try/except → now reported as `status="failed"` instead of silently yielding an empty frame. |
| `gold/scheme_nav.py` (42, 116) | 2 | **No change.** Same — covered by the runner. |
| `gold/folio_nominees.py` (44, 101, 634) | 3 | **No change.** Same — covered by the runner. |
| `gold/transaction.py` (46) | 1 | **No change.** Covered by the runner. |
| `gold/transaction.py` (1008) | 1 | **No change needed — already correct.** This one is *already* wrapped in its own `try: existing = safe_read(...) / except Exception: existing = pd.DataFrame()`. That handler was **dead code** while `safe_read` swallowed everything; it now becomes live and preserves exactly the bootstrap behaviour its author intended (no `gold.transactions` rows yet → treat as empty → insert everything). |
| `silver/silver_loader.py` (24, 97, 132) | 3 | **No change.** `load_silver()` is called inside `app.py`'s `try/except` → `st.error` + traceback, so a Silver read failure now surfaces in the UI instead of quietly loading nothing. |
| `silver/silver_helpers.py` (75) | 1 | **No change.** `load_state_dimension()` reaches the same `app.py` handler. |

Per the phase brief, only call sites that *needed* changing to keep working were touched —
which turned out to be **none**: the top-level wrapping (`run_gold_pipeline` for Gold,
`app.py` for Silver) already covers every one, and the single call site that genuinely
wants empty-on-error already declares that intent explicitly.

⚠️ **Intentional behaviour change to be aware of:** secondary lookups that previously
degraded silently now fail their domain. E.g. `gold/amc.py`'s `bronze.amc_master` lookup
used to skip name enrichment if that table was unreachable and still load AMC rows; it now
reports `AMC → failed`. That is the point of the phase — but it means a genuinely optional
lookup, if one is ever wanted, must opt out explicitly at the call site rather than relying
on a global swallow.

## 2. Structured results

`gold/pipeline_runner.make_result()` defines the shape once:

```python
{"name": str, "status": "ok" | "no_data" | "failed" | "skipped", "rows": int, "error": str | None}
```

- `run_gold_pipeline()` returns `ok` (with `rows=len(gold_df)`), `no_data` (empty extract
  **or** empty transform — the latter previously printed nothing at all), or `failed` with
  `error="ExcType: message"`. It still **never propagates** a domain's exception.
- `load_gold()` returns a `list` of these and still does not raise on a single domain's
  failure — domain isolation is a deliberate design choice and was kept. It also prints a
  closing summary line (`ok / no_data / failed / skipped` counts plus a line per failure).
- The **Scheme** domain, which Phase 6 deliberately kept out of the generic runner because
  `extract_scheme()` returns two DataFrames, now reports through `make_result()` so it
  appears in the list with an identical shape.
- **`"skipped"` — one addition beyond the brief's three statuses.** The three optional
  imports (`gold.sip`, `gold.clients`, `gold.folio_nominees`) used to do
  `except ImportError: X_AVAILABLE = False`, discarding the reason and then omitting the
  domain from the run entirely — itself a silent failure of exactly the kind this phase
  targets. The `ImportError` text is now captured into `SIP_IMPORT_ERROR` etc. and reported
  as `status="skipped"` with that message, so an unimportable Gold module is visible instead
  of vanishing.

## 3. Streamlit UI

`app.py` now captures `gold_results = load_gold()` and renders one `st.error` per failed
domain (with the error text) and one `st.warning` per skipped domain. The closing
`st.success("Transformation Completed")` became conditional — it previously claimed
unqualified success even when Gold domains had failed, since `load_gold()` never raised.
Results are also stashed in `st.session_state.gold_results`.

Minor ordering fix while in there: `st.info("Loading Gold Layer...")` sat *after* the
`load_gold()` call, so it appeared once the work was already done. It now precedes it.

## Smoke test ✅

All tests run with **`python_scripts/venv/bin/python`** (Python 3.13, pandas 3.0.5) — the
project's real interpreter. The system `python3` (3.10) has no pandas, and the 3.12 install
lacks `psycopg2`, so neither can run this code.

```
cd python_scripts && PYTHONPATH=$PWD ./venv/bin/python <scratchpad>/phaseB_smoke.py
→ PHASE B SMOKE: ALL CHECKS PASSED
```

**1. `safe_read` — verified against the live dev DB:**
```
[ERROR] safe_read failed: DatabaseError: ... (psycopg2.errors.UndefinedTable)
        relation "schema_that_does_not_exist.nope" does not exist
[ERROR] safe_read query: SELECT * FROM schema_that_does_not_exist.nope
  [PASS] safe_read raises on a bad query — DatabaseError
  [PASS] safe_read still returns a DataFrame on success — shape=(1, 1)
```

**2. `run_gold_pipeline` — all five outcomes produce a well-formed result:** `ok` (rows
counted), `no_data` from an empty extract, `no_data` from an empty transform, `failed` from
a raising `extract_fn`, `failed` from a raising `load_fn`. Every result carried exactly the
four keys, error text was captured, and no exception escaped any of the five calls.

**3. `load_gold()` failure isolation — one domain sabotaged with a simulated
`ConnectionError("server closed the connection unexpectedly")` in its `extract_fn`:**
```
  ok: 0 | no_data: 7 | failed: 1 | skipped: 0
  FAILED  Transactions: ConnectionError: simulated: server closed the connection unexpectedly

    AMC              no_data   Scheme          no_data   Scheme NAV      no_data
    Transactions     FAILED    Holdings        no_data   SIP             no_data
    Clients          no_data   Folio Nominees  no_data
```
- **(a) other domains still complete** — 7 non-failed, and the four domains *ordered after*
  the sabotaged one (`Holdings`, `SIP`, `Clients`, `Folio Nominees`) all still ran.
- **(b) the failure appears in the returned status list** — with its exception type and
  message; exactly one domain reported `failed`.
- **(c) nothing crashed the whole run** — `load_gold()` returned normally.
- Scheme reported in the identical result shape despite bypassing the generic runner.
- A caller can detect the failure from the return value alone, which is what `app.py`
  now does.

`compileall` over `common/`, `gold/` and `app.py` exits 0; `import app` succeeds.
