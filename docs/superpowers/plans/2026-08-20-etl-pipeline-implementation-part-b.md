# Gold-Layer Idempotency Hardening Implementation Plan (Part B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every gold-layer `load_*` function a true, atomic `INSERT ... ON CONFLICT` upsert instead of Python-side anti-join-then-append (or, for `transactions`/`sip`, no dedup at all) — the mechanism that makes "no duplication" actually true under retries and concurrent runs, not just true by convention. Also: re-enable the commented-out `drop_duplicates()` in `etl_trans.py`, and populate `arn`/`sub_arn` on `gold.folio_nominees` (columns already exist, just never written).

**Architecture:** A tiny shared helper (`utils/upsert.py`) does the mechanical part — stage a DataFrame into a session-scoped `TEMP TABLE ON COMMIT DROP`, then one `INSERT ... SELECT ... ON CONFLICT` — while each of the 8 `load_*` functions supplies its own conflict target and update-column list, because those genuinely differ per table (some are formal named constraints, some are nullable-safe two-part expression indexes, one is a partial index). Runs entirely on top of `sql_scripts/add_constraints.sql`, which your team already wrote, validated against the live PG14.23 DB on 2026-08-19, and left untracked in the repo — this plan applies it, it doesn't rewrite it.

**Tech Stack:** Same as Part A — Python 3.13.14, SQLAlchemy 2.x + psycopg2, pandas, pytest against the real dev DB.

**Spec:** `docs/superpowers/specs/2026-08-19-etl-pipeline-design.md` §7.2–§7.4. This plan also directly consumes (unmodified) `sql_scripts/dedup_cleanup.sql` and `sql_scripts/add_constraints.sql`.

**Depends on:** Part A's Task 10 (`utils/gold_result.py` and the `load_result()` return-value convention) — every `load_*` edit in this plan builds on that shape. Run Part A through at least Task 10 before starting this plan.

## Global Constraints

- Postgres 14.23 — no `NULLS NOT DISTINCT`. Every nullable-key conflict target must use the same `(col IS NULL), (COALESCE(col, sentinel))` two-part expression `add_constraints.sql` already built the index with — the `ON CONFLICT` target must repeat those exact expressions verbatim, or Postgres won't match it to the index.
- `sql_scripts/dedup_cleanup.sql` is destructive (deletes duplicate rows) — never run it without confirming a backup exists first, per its own header comment.
- Every `load_*` function keeps returning the `load_result()` shape from Part A Task 10 (`{"status", "rows_loaded", "error"}`) — this plan changes *how* rows land, not the return contract.
- No behavior change to *which* rows are considered duplicates beyond what's already encoded in `add_constraints.sql`'s index definitions — this plan is a mechanism change (Python anti-join → DB-level `ON CONFLICT`), not a new deduplication policy.

---

### Task 1: Apply the existing dedup + constraints SQL (prerequisite)

**Files:**
- Read (not modified): `sql_scripts/dedup_cleanup.sql`, `sql_scripts/add_constraints.sql`

**Interfaces:**
- Produces: the unique indexes/constraints every later task's `ON CONFLICT` clause targets — this task must complete successfully before any other task in this plan.

- [ ] **Step 1: Confirm a backup exists**

Before running anything destructive, confirm with whoever owns the target DB (`19_08_2026_intelliwealth_layer_db`) that a current backup/snapshot exists. `dedup_cleanup.sql`'s own header repeats this — do not skip it.

- [ ] **Step 2: Run the read-only preflight (Part 0 of `dedup_cleanup.sql`)**

Run:
```
psql "postgresql://<user>:<password>@<host>:<port>/19_08_2026_intelliwealth_layer_db" \
  -f sql_scripts/dedup_cleanup.sql --single-transaction -v ON_ERROR_STOP=1 2>&1 | tee /tmp/dedup_cleanup_output.log
```
Since the whole file is one script, actually run Part 0 in isolation first by extracting it (everything before `-- PART 1`) into a scratch file and running just that, so you can review the duplicate-census output before anything destructive runs:
```
sed -n '/PART 0/,/PART 1/p' sql_scripts/dedup_cleanup.sql | head -n -1 > /tmp/dedup_preflight.sql
psql "postgresql://<user>:<password>@<host>:<port>/19_08_2026_intelliwealth_layer_db" -f /tmp/dedup_preflight.sql
```
Expected: the duplicate-group census queries print counts matching the file's own comments (e.g. "expect 8 groups / 17 rows / 9 rows to delete" for `gold.sip`) — if actual counts differ meaningfully from the comments, STOP and investigate before proceeding (data may have changed since the script was written).

- [ ] **Step 3: Run the full `dedup_cleanup.sql`**

Run:
```
psql "postgresql://<user>:<password>@<host>:<port>/19_08_2026_intelliwealth_layer_db" \
  -f sql_scripts/dedup_cleanup.sql -v ON_ERROR_STOP=1
```
Expected: completes without error; re-run Part 0's census queries afterward (same command as Step 2) and confirm every table now reports 0 duplicate groups, per the file's own closing instruction.

- [ ] **Step 4: Run `add_constraints.sql`**

Run (note: `CONCURRENTLY` builds cannot run inside a transaction block, so no `--single-transaction` here):
```
psql "postgresql://<user>:<password>@<host>:<port>/19_08_2026_intelliwealth_layer_db" \
  -f sql_scripts/add_constraints.sql -v ON_ERROR_STOP=1
```
Expected: completes without error; Part 3's verification queries at the end return 4 formal constraints and 14 total unique indexes across `bronze`/`silver`/`gold`, with the "invalid index" query returning 0 rows.

- [ ] **Step 5: Verify from Python**

```bash
cd python_scripts && source venv/bin/activate && python3 -c "
from utils.db import engine
import pandas as pd
df = pd.read_sql(
    \"SELECT schemaname, tablename, indexname FROM pg_indexes \"
    \"WHERE schemaname IN ('bronze','silver','gold') AND indexname LIKE 'uq_%' ORDER BY 1,2,3\",
    engine
)
print(df.to_string(index=False))
assert len(df) == 14, f'expected 14 unique indexes, found {len(df)}'
print('OK: 14 unique indexes confirmed')
"
```
Expected: prints all 14 index names, then `OK: 14 unique indexes confirmed`.

- [ ] **Step 6: No commit for this task** — it applies existing, already-committed-elsewhere SQL against the database; there's no repo file change to commit. Note completion in your task tracker and proceed to Task 2.

---

### Task 2: Shared `utils/upsert.py` helper

**Files:**
- Create: `python_scripts/utils/upsert.py`
- Test: `python_scripts/tests/test_upsert.py`

**Interfaces:**
- Consumes: `utils.db.engine`.
- Produces: `upsert_dataframe(engine, df, schema, table, conflict_target_sql, update_set_sql, insert_columns=None) -> int` (returns rows affected). `conflict_target_sql` is a literal SQL fragment (e.g. `"(amc_code)"` or the full two-part expression list), `update_set_sql` is a literal `SET`-clause fragment or `None` for `DO NOTHING`.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_upsert.py
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from utils.upsert import upsert_dataframe  # noqa: E402

TEST_TABLE = "test_upsert_target"


def setup_module(module):
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS pipeline.{TEST_TABLE}"))
        conn.execute(
            text(
                f"CREATE TABLE pipeline.{TEST_TABLE} ("
                f"id INTEGER, value VARCHAR, "
                f"CONSTRAINT uq_test_upsert_id UNIQUE (id))"
            )
        )


def teardown_module(module):
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS pipeline.{TEST_TABLE}"))


def _rows():
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT id, value FROM pipeline.{TEST_TABLE} ORDER BY id")
        ).fetchall()


def test_upsert_inserts_new_rows():
    df = pd.DataFrame({"id": [1, 2], "value": ["a", "b"]})
    affected = upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert affected == 2
    assert _rows() == [(1, "a"), (2, "b")]


def test_upsert_updates_on_conflict():
    df = pd.DataFrame({"id": [1], "value": ["updated"]})
    upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert _rows() == [(1, "updated"), (2, "b")]


def test_upsert_do_nothing_on_conflict():
    df = pd.DataFrame({"id": [2], "value": ["should-not-apply"]})
    upsert_dataframe(
        engine, df, schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql=None,
    )
    assert _rows() == [(1, "updated"), (2, "b")]


def test_upsert_empty_dataframe_is_a_noop():
    affected = upsert_dataframe(
        engine, pd.DataFrame(columns=["id", "value"]), schema="pipeline", table=TEST_TABLE,
        conflict_target_sql="(id)", update_set_sql="value = EXCLUDED.value",
    )
    assert affected == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_upsert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.upsert'`

- [ ] **Step 3: Write `python_scripts/utils/upsert.py`**

```python
import uuid

from sqlalchemy import text


def upsert_dataframe(engine, df, schema, table, conflict_target_sql,
                      update_set_sql, insert_columns=None):
    """
    Loads `df` into a session-scoped TEMP TABLE (ON COMMIT DROP, so it never
    leaks past this transaction), then runs one
        INSERT ... SELECT ... ON CONFLICT <conflict_target_sql> <action>
    from the temp table into `schema.table`. Returns rows affected.

    `conflict_target_sql`: literal SQL fragment, e.g. "(amc_code)" for a plain/
    formal unique constraint, or the full two-part expression list for a
    nullable-safe expression index (see add_constraints.sql for the exact
    expressions each gold table's index uses).
    `update_set_sql`: literal `SET` fragment (e.g. "name = EXCLUDED.name, ...")
    or None for `DO NOTHING`.
    `insert_columns`: explicit column order; defaults to df.columns.
    """
    if df.empty:
        return 0

    columns = insert_columns or list(df.columns)
    staging_table = f"_stg_{table}_{uuid.uuid4().hex[:12]}"
    col_list = ", ".join(f'"{c}"' for c in columns)

    with engine.begin() as conn:
        conn.execute(
            text(
                f'CREATE TEMP TABLE "{staging_table}" '
                f'(LIKE {schema}.{table} INCLUDING DEFAULTS) ON COMMIT DROP'
            )
        )

        df[columns].to_sql(
            staging_table, con=conn, if_exists="append",
            index=False, method="multi", chunksize=1000,
        )

        action = f"DO UPDATE SET {update_set_sql}" if update_set_sql else "DO NOTHING"
        result = conn.execute(
            text(
                f"""
                INSERT INTO {schema}.{table} ({col_list})
                SELECT {col_list} FROM "{staging_table}"
                ON CONFLICT {conflict_target_sql}
                {action}
                """
            )
        )
        return result.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_upsert.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add python_scripts/utils/upsert.py python_scripts/tests/test_upsert.py
git commit -m "feat: add shared staging-table upsert helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Rewire the 4 formal-constraint tables (`amc`, `scheme`, `scheme_nav`, `folio_nominees`)

**Files:**
- Modify: `python_scripts/etl_gold_amc.py`
- Modify: `python_scripts/etl_gold_scheme_nav.py`
- Modify: `python_scripts/etl_gold_folio_nominees.py`
- Modify: `python_scripts/etl_gold_scheme.py`
- Test: `python_scripts/tests/test_gold_upsert_formal_constraints.py`

**Interfaces:**
- Consumes: `utils.upsert.upsert_dataframe` (Task 2), `utils.gold_result.load_result` (Part A Task 10).
- Produces: same `load_amc(gold_df)`, `load_scheme_nav(gold_df)`, `load_folio_nominees(gold_df)`, `load_scheme(gold_df)` signatures and `load_result()` return shape — internals now use real `ON CONFLICT`, no more Python-side anti-join.

- [ ] **Step 1: Write the failing tests** (run twice against the real DB to prove no duplication on re-run — this is the concrete proof called for in the spec)

```python
# python_scripts/tests/test_gold_upsert_formal_constraints.py
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_amc import load_amc  # noqa: E402
from etl_gold_scheme_nav import load_scheme_nav  # noqa: E402


def _count(table, where_sql, params):
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM gold.{table} WHERE {where_sql}"), params).scalar()


def test_load_amc_upsert_is_idempotent_on_rerun():
    amc_code = f"TEST-AMC-{uuid.uuid4().hex[:8]}"
    df = pd.DataFrame([{
        "amc_code": amc_code, "name": "Test AMC", "short_name": "TAMC",
        "rta": "CAMS", "logo_url": None, "status": "ACTIVE",
        "arn": "ARN-1", "sub_arn": None, "created_at": pd.Timestamp.now(),
    }])
    try:
        r1 = load_amc(df.copy())
        assert r1["status"] == "ok"
        assert r1["rows_loaded"] == 1
        assert _count("amc", "amc_code = :c", {"c": amc_code}) == 1

        df2 = df.copy()
        df2["name"] = "Test AMC Renamed"
        r2 = load_amc(df2)
        assert r2["status"] == "ok"
        assert _count("amc", "amc_code = :c", {"c": amc_code}) == 1  # still exactly 1, not 2

        with engine.connect() as conn:
            name = conn.execute(
                text("SELECT name FROM gold.amc WHERE amc_code = :c"), {"c": amc_code}
            ).scalar()
        assert name == "Test AMC Renamed"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.amc WHERE amc_code = :c"), {"c": amc_code})


def test_load_scheme_nav_upsert_is_idempotent_on_rerun():
    scheme_id = str(uuid.uuid4())
    nav_date = pd.Timestamp("2026-08-19").date()
    df = pd.DataFrame([{
        "scheme_id": scheme_id, "nav_date": nav_date, "nav": 10.5,
        "repurchase_nav": None, "source": "TEST", "created_at": pd.Timestamp.now(),
        "arn": "ARN-1", "sub_arn": None,
    }])
    try:
        r1 = load_scheme_nav(df.copy())
        assert r1["status"] == "ok"
        assert _count("scheme_nav", "scheme_id = :s AND nav_date = :d", {"s": scheme_id, "d": nav_date}) == 1

        df2 = df.copy()
        df2["nav"] = 11.0
        load_scheme_nav(df2)
        assert _count("scheme_nav", "scheme_id = :s AND nav_date = :d", {"s": scheme_id, "d": nav_date}) == 1

        with engine.connect() as conn:
            nav = conn.execute(
                text("SELECT nav FROM gold.scheme_nav WHERE scheme_id = :s AND nav_date = :d"),
                {"s": scheme_id, "d": nav_date},
            ).scalar()
        assert float(nav) == 11.0
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM gold.scheme_nav WHERE scheme_id = :s AND nav_date = :d"),
                {"s": scheme_id, "d": nav_date},
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_upsert_formal_constraints.py -v`
Expected: FAIL — current `load_amc`/`load_scheme_nav` still use anti-join+append, so the second call in each test either duplicates the row (breaking the `COUNT == 1` assertion) or the constraint from Task 1 makes the plain append throw an `IntegrityError` instead of updating.

- [ ] **Step 3: Rewrite `load_amc` in `python_scripts/etl_gold_amc.py`**

Replace the entire "CHECK EXISTING GOLD AMC" + "INSERT" sections (from the `existing_amc = safe_read(...)` block through the final `except Exception:` block) with:

```python
    gold_columns = [
        "amc_code", "name", "short_name", "rta", "logo_url",
        "status", "arn", "sub_arn", "created_at",
    ]
    gold_df = gold_df[gold_columns]

    if "flag" in gold_df.columns:
        gold_df = gold_df.drop(columns=["flag"])

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="amc",
            conflict_target_sql="(amc_code)",
            update_set_sql=(
                "name = EXCLUDED.name, short_name = EXCLUDED.short_name, "
                "rta = EXCLUDED.rta, logo_url = EXCLUDED.logo_url, "
                "status = EXCLUDED.status, arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn"
            ),
        )
        print(f"Upserted rows : {affected}")
        return load_result("ok", affected)

    except Exception as e:
        print("FAILED LOADING GOLD AMC")
        traceback.print_exc(limit=5)
        return load_result("error", 0, str(e))
```

Add the import at the top of the file: `from utils.upsert import upsert_dataframe` (alongside the `from utils.gold_result import load_result` import already added in Part A Task 10).

- [ ] **Step 4: Rewrite `load_scheme_nav` in `python_scripts/etl_gold_scheme_nav.py`**

Replace the `try: ... gold_df.to_sql(...) ... except Exception: ... return load_result(...)` block with:

```python
    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="scheme_nav",
            conflict_target_sql="(scheme_id, nav_date)",
            update_set_sql=(
                "nav = EXCLUDED.nav, repurchase_nav = EXCLUDED.repurchase_nav, "
                "source = EXCLUDED.source, arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn"
            ),
        )
        print(f"{affected:,} rows upserted into gold.scheme_nav.")
        return load_result("ok", affected)

    except Exception as e:
        print("FAILED LOADING GOLD SCHEME NAV")
        traceback.print_exc(limit=5)
        return load_result("error", 0, str(e))
```

Add the import: `from utils.upsert import upsert_dataframe`.

- [ ] **Step 5: Rewrite `load_folio_nominees` in `python_scripts/etl_gold_folio_nominees.py`**

Replace the "LOAD EXISTING DATA" / "REMOVE ALREADY EXISTING KEYS" / "FINAL DUPLICATE CHECK" / "INSERT" sections (everything from `try: existing = pd.read_sql(...)` through the final `except Exception:` block) with:

```python
    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="folio_nominees",
            conflict_target_sql="(holding_id, seq)",
            update_set_sql=(
                "name = EXCLUDED.name, relationship = EXCLUDED.relationship, "
                "percentage = EXCLUDED.percentage, dob = EXCLUDED.dob, "
                "is_minor = EXCLUDED.is_minor, guardian_name = EXCLUDED.guardian_name, "
                "id_type = EXCLUDED.id_type, id_no = EXCLUDED.id_no, "
                "address = EXCLUDED.address, arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn"
            ),
        )
        print(f"{affected} rows upserted into Gold Folio Nominees.")
        return load_result("ok", affected)

    except Exception as e:
        print("FAILED LOADING GOLD FOLIO NOMINEES")
        traceback.print_exc(limit=5)
        return load_result("error", 0, str(e))
```

Add imports at the top: `import traceback` and `from utils.upsert import upsert_dataframe` (this file currently has no `traceback` import — its except blocks print bare messages; add it since the new code follows the same `traceback.print_exc` convention as the other files).

- [ ] **Step 6: Rewrite `load_scheme` in `python_scripts/etl_gold_scheme.py`**

This one has ISIN-exclusion behavior worth preserving exactly: ISIN is never written by this loader (left for a separate process). Replace the "READ EXISTING GOLD SCHEMES" / "EXISTING KEYS" / "UPDATE EXISTING" / "FIND NEW SCHEMES" / "INSERT NEW SCHEMES" sections (everything from `existing_scheme = pd.read_sql(...)` through the final `return True`) with:

```python
    insert_columns = [c for c in expected_columns if c != "isin"]

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="scheme",
            conflict_target_sql="(rta, scheme_code)",
            update_set_sql=(
                "scheme_name = EXCLUDED.scheme_name, category = EXCLUDED.category, "
                "plan = EXCLUDED.plan, amfi_code = EXCLUDED.amfi_code, "
                "category_id = EXCLUDED.category_id, plan_type = EXCLUDED.plan_type, "
                "option_type = EXCLUDED.option_type, rta_scheme_code = EXCLUDED.rta_scheme_code, "
                "benchmark_id = EXCLUDED.benchmark_id, expense_ratio = EXCLUDED.expense_ratio, "
                "exit_load_json = EXCLUDED.exit_load_json, lock_in_months = EXCLUDED.lock_in_months, "
                "riskometer = EXCLUDED.riskometer, status = EXCLUDED.status, "
                "arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn, amc_id = EXCLUDED.amc_id"
                # ISIN intentionally excluded — never written or updated by this loader.
            ),
            insert_columns=insert_columns,
        )
        print(f"Scheme rows upserted : {affected}")
        return load_result("ok", affected)

    except Exception as e:
        print("\nERROR WHILE UPSERTING SCHEMES")
        print(type(e).__name__)
        print(e)
        return load_result("error", 0, str(e))
```

Add the import: `from utils.upsert import upsert_dataframe`. Note this drops the `text`-based manual `UPDATE ... WHERE id = :id` loop and the `existing_scheme`/`existing_keys` lookups entirely — `id` stays in `expected_columns`/`gold_df` for the INSERT path (new rows need an id), but is never in `update_set_sql`, so an existing row's `id` is never touched, matching the original's `WHERE id = :id` semantics of "find by natural key, never change the surrogate key."

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_upsert_formal_constraints.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: all pass, including Part A's `test_gold_result.py` (the `load_result()` shape is unchanged) and any pre-existing scheme-mapping tests that read `gold.scheme`/`gold.amc`.

- [ ] **Step 9: Commit**

```bash
git add python_scripts/etl_gold_amc.py python_scripts/etl_gold_scheme_nav.py \
        python_scripts/etl_gold_folio_nominees.py python_scripts/etl_gold_scheme.py \
        python_scripts/tests/test_gold_upsert_formal_constraints.py
git commit -m "refactor: real ON CONFLICT upserts for amc/scheme/scheme_nav/folio_nominees

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Rewire the 4 expression/partial-index tables (`transactions`, `sip`, `holdings`, `clients`)

**Files:**
- Modify: `python_scripts/etl_gold_transaction.py`
- Modify: `python_scripts/etl_gold_sip.py`
- Modify: `python_scripts/etl_gold_holdings.py`
- Modify: `python_scripts/etl_gold_clients.py`
- Test: `python_scripts/tests/test_gold_upsert_expression_indexes.py`

**Interfaces:**
- Consumes: `utils.upsert.upsert_dataframe` (Task 2). Conflict-target expressions here must match `sql_scripts/add_constraints.sql`'s index definitions verbatim — these are the 10 index-only (non-formal-constraint) unique indexes from that file's Part 1.

- [ ] **Step 1: Write the failing tests**

```python
# python_scripts/tests/test_gold_upsert_expression_indexes.py
import sys
import uuid
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_transaction import load_transactions  # noqa: E402
from etl_gold_holdings import load_holdings  # noqa: E402


def test_load_transactions_do_nothing_on_conflict_never_duplicates():
    rta_txn_no = f"TEST-{uuid.uuid4().hex[:8]}"
    df = pd.DataFrame([{
        "rta": "CAMS", "rta_txn_no": rta_txn_no, "pan": None, "folio_number": "F1",
        "txn_type": "PURCHASE", "txn_type_raw": None, "txn_desc": None,
        "txn_date": None, "post_date": None, "amount": 100.0, "units": 5.0,
        "nav": None, "load_amount": None, "stt": None, "stamp_duty": None, "gst": None,
        "arn": None, "euin": None, "sip_ref": None, "status": None,
        "client_id": None, "amc_id": None, "scheme_id": None, "txn_sub_type": None,
        "rta_txn_id": None, "arn_id": None, "sip_id": None, "source": "TEST",
        "source_file_id": None, "created_at": pd.Timestamp.now(),
        "scheme_code": None, "sub_arn": None,
    }])
    try:
        r1 = load_transactions(df.copy())
        assert r1["status"] == "ok"
        assert r1["rows_loaded"] == 1

        r2 = load_transactions(df.copy())  # identical row again
        assert r2["status"] == "ok"
        assert r2["rows_loaded"] == 0  # DO NOTHING — nothing new landed

        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM gold.transactions WHERE rta_txn_no = :t"),
                {"t": rta_txn_no},
            ).scalar()
        assert count == 1
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.transactions WHERE rta_txn_no = :t"), {"t": rta_txn_no})


def test_load_holdings_updates_existing_on_conflict():
    folio_number = f"TEST-{uuid.uuid4().hex[:8]}"
    scheme_id = str(uuid.uuid4())
    base = {
        "id": str(uuid.uuid4()), "rta": "CAMS", "pan": None, "folio_number": folio_number,
        "units": 10.0, "market_value": 1000.0, "as_on_date": None, "folio_date": None,
        "arn": "ARN-1", "holding_nature": None, "nominee_name": None, "nominee_relation": None,
        "nominee_pct": None, "kyc_status": None, "bank_name": None, "bank_ac_last4": None,
        "demat_flag": None, "client_id": None, "amc_id": None, "scheme_id": scheme_id,
        "purchase_date": None, "arn_id": None, "avg_cost_nav": None, "invested_amount": None,
        "current_nav": None, "current_value": None, "nav_date": None, "unrealised_gain": None,
        "xirr": None, "first_purchase_date": None, "source_file_id": None,
        "last_synced_at": None, "subarn": None, "created_at": pd.Timestamp.now(),
    }
    df = pd.DataFrame([base])
    try:
        r1 = load_holdings(df.copy())
        assert r1["status"] == "ok"

        df2 = pd.DataFrame([{**base, "id": str(uuid.uuid4()), "units": 20.0, "market_value": 2200.0}])
        r2 = load_holdings(df2)
        assert r2["status"] == "ok"

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT units, market_value FROM gold.holdings WHERE folio_number = :f AND scheme_id = :s"),
                {"f": folio_number, "s": scheme_id},
            ).fetchall()
        assert len(rows) == 1  # still exactly one row
        assert float(rows[0][0]) == 20.0  # updated, not duplicated
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.holdings WHERE folio_number = :f"), {"f": folio_number})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_upsert_expression_indexes.py -v`
Expected: FAIL — current `load_transactions` has no dedup at all (second call would insert a literal duplicate row, `rows_loaded == 0` assertion fails), and `load_holdings`'s anti-join treats the second row as a new insert rather than an update.

- [ ] **Step 3: Rewrite `load_transactions` in `python_scripts/etl_gold_transaction.py`**

Replace the `try: gold_df.to_sql(...) ... except Exception: ... return load_result(...)` block with:

```python
    conflict_target_sql = (
        "((rta IS NULL), (COALESCE(rta, '')), "
        "(rta_txn_no IS NULL), (COALESCE(rta_txn_no, '')), "
        "(folio_number IS NULL), (COALESCE(folio_number, '')), "
        "(amount IS NULL), (COALESCE(amount, 0)), "
        "(units IS NULL), (COALESCE(units, 0)))"
    )

    try:
        print(f"Upserting {len(gold_df)} rows...")
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="transactions",
            conflict_target_sql=conflict_target_sql,
            update_set_sql=None,  # transactions are immutable once recorded — DO NOTHING on conflict
        )
        print(f"{affected} new rows inserted into gold.transactions ({len(gold_df) - affected} already existed)")
        return load_result("ok", affected)

    except Exception as e:
        print("=" * 80)
        print("FAILED LOADING GOLD TRANSACTIONS")
        print("=" * 80)
        traceback.print_exc(limit=10)
        return load_result("error", 0, str(e))
```

Add the import: `from utils.upsert import upsert_dataframe`.

- [ ] **Step 4: Rewrite `load_sip` in `python_scripts/etl_gold_sip.py`**

Replace the `try: with engine.begin() as connection: gold_df.to_sql(...) ... except Exception: ... return load_result(...)` block with:

```python
    conflict_target_sql = (
        "((rta IS NULL), (COALESCE(rta, '')), "
        "(folio_number IS NULL), (COALESCE(folio_number, '')), "
        "(scheme_code IS NULL), (COALESCE(scheme_code, '')), "
        "(registered_date IS NULL), (COALESCE(registered_date, '0001-01-01'::date)), "
        "(amount IS NULL), (COALESCE(amount, 0)))"
    )
    update_set_sql = (
        "status = EXCLUDED.status, ceased_date = EXCLUDED.ceased_date, "
        "ceased_reason = EXCLUDED.ceased_reason, "
        "completed_installments = EXCLUDED.completed_installments, "
        "bounced_installments = EXCLUDED.bounced_installments, "
        "next_due_date = EXCLUDED.next_due_date, mandate_id = EXCLUDED.mandate_id, "
        "sip_day = EXCLUDED.sip_day, sip_type = EXCLUDED.sip_type, "
        "registered_installments = EXCLUDED.registered_installments, "
        "client_id = EXCLUDED.client_id, amc_id = EXCLUDED.amc_id, "
        "scheme_id = EXCLUDED.scheme_id, arn_id = EXCLUDED.arn_id, "
        "arn = EXCLUDED.arn, sub_arn = EXCLUDED.sub_arn, isin = EXCLUDED.isin, "
        "scheme_name = EXCLUDED.scheme_name, amc_code = EXCLUDED.amc_code, "
        "frequency = EXCLUDED.frequency, start_date = EXCLUDED.start_date, "
        "end_date = EXCLUDED.end_date"
    )

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="sip",
            conflict_target_sql=conflict_target_sql, update_set_sql=update_set_sql,
        )

        verification = safe_read("SELECT COUNT(*) AS total_rows FROM gold.sip")
        total_rows = int(verification.iloc[0]["total_rows"]) if not verification.empty else -1

        print()
        print("Upserted rows:", affected)
        print("Gold SIP rows after load:", total_rows)
        print()
        print("GOLD SIP LOAD SUCCESSFUL")

        return load_result("ok", affected)

    except Exception as e:
        print()
        print("=" * 80)
        print("GOLD SIP LOAD FAILED")
        print("=" * 80)
        traceback.print_exc(limit=10)
        return load_result("error", 0, str(e))
```

Add the import: `from utils.upsert import upsert_dataframe`.

- [ ] **Step 5: Rewrite `load_holdings` in `python_scripts/etl_gold_holdings.py`**

Replace the "EXISTING HOLDINGS" / "NORMALIZE KEYS" / "REMOVE EXISTING HOLDINGS" / "FINAL DUPLICATE CHECK" / "INSERT" sections (from `existing_holdings = pd.read_sql(...)` through the final `except Exception as e:` block) with:

```python
    conflict_target_sql = (
        "((rta IS NULL), (COALESCE(rta, '')), "
        "(folio_number IS NULL), (COALESCE(folio_number, '')), "
        "(scheme_id IS NULL), (COALESCE(scheme_id, '')))"
    )
    update_set_sql = (
        "pan = EXCLUDED.pan, units = EXCLUDED.units, market_value = EXCLUDED.market_value, "
        "as_on_date = EXCLUDED.as_on_date, folio_date = EXCLUDED.folio_date, "
        "arn = EXCLUDED.arn, holding_nature = EXCLUDED.holding_nature, "
        "nominee_name = EXCLUDED.nominee_name, nominee_relation = EXCLUDED.nominee_relation, "
        "nominee_pct = EXCLUDED.nominee_pct, kyc_status = EXCLUDED.kyc_status, "
        "bank_name = EXCLUDED.bank_name, bank_ac_last4 = EXCLUDED.bank_ac_last4, "
        "demat_flag = EXCLUDED.demat_flag, client_id = EXCLUDED.client_id, "
        "amc_id = EXCLUDED.amc_id, purchase_date = EXCLUDED.purchase_date, "
        "arn_id = EXCLUDED.arn_id, avg_cost_nav = EXCLUDED.avg_cost_nav, "
        "invested_amount = EXCLUDED.invested_amount, current_nav = EXCLUDED.current_nav, "
        "current_value = EXCLUDED.current_value, nav_date = EXCLUDED.nav_date, "
        "unrealised_gain = EXCLUDED.unrealised_gain, xirr = EXCLUDED.xirr, "
        "source_file_id = EXCLUDED.source_file_id, last_synced_at = EXCLUDED.last_synced_at, "
        "subarn = EXCLUDED.subarn"
        # id, rta, folio_number, scheme_id, created_at, first_purchase_date excluded:
        # id/rta/folio_number/scheme_id are the identity, created_at and
        # first_purchase_date must never be overwritten by a later sync.
    )

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="holdings",
            conflict_target_sql=conflict_target_sql, update_set_sql=update_set_sql,
        )
        print()
        print("Upserted rows:", affected)
        print("Holdings loaded successfully")
        return load_result("ok", affected)

    except Exception as e:
        print()
        print("=" * 80)
        print("ERROR WHILE LOADING GOLD.HOLDINGS")
        print("=" * 80)
        print("Error type:", type(e).__name__)
        print("Error:", e)
        return load_result("error", 0, str(e))
```

Add the import: `from utils.upsert import upsert_dataframe`. Keep the earlier `remove_zero_net_holdings`, varchar-limit validation, and `required`/`scheme_id` NULL checks exactly as they are (unchanged) — only the section from "EXISTING HOLDINGS" onward is replaced.

- [ ] **Step 6: Rewrite `load_clients` in `python_scripts/etl_gold_clients.py`**

This one keeps its dynamic column-set validation against `information_schema.columns` (the DataFrame's exact column set legitimately varies), so the update-set list is built dynamically rather than hardcoded. Replace the "GET EXISTING PANS FROM DATABASE" / "NOTHING NEW" / "INSERT" sections (from `existing = safe_read(...)` through the final `return False`/`return True` at the bottom) with:

```python
    print()
    print("Checking gold.clients table columns...")

    table_columns = safe_read(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'gold' AND table_name = 'clients'
        ORDER BY ordinal_position
        """
    )

    if table_columns.empty:
        print("ERROR: gold.clients table was not found.")
        return load_result("error", 0, "gold.clients table was not found")

    database_columns = set(table_columns["column_name"])

    missing_database_columns = [
        col for col in gold_df.columns if col not in database_columns
    ]

    if missing_database_columns:
        print()
        print("ERROR: These columns exist in the DataFrame but not in gold.clients:")
        for col in missing_database_columns:
            print(" -", col)
        return load_result(
            "error", 0, f"columns missing from gold.clients: {missing_database_columns}"
        )

    gold_df = gold_df[[col for col in gold_df.columns if col in database_columns]].copy()
    gold_df = gold_df.astype(object)
    gold_df = gold_df.where(pd.notna(gold_df), None)

    update_columns = [c for c in gold_df.columns if c not in ("pan", "created_at")]
    update_set_sql = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_columns)

    print()
    print("Starting upsert...")

    try:
        affected = upsert_dataframe(
            engine, gold_df, schema="gold", table="clients",
            conflict_target_sql="(pan) WHERE pan IS NOT NULL",
            update_set_sql=update_set_sql,
        )

        print()
        print("=" * 80)
        print("VERIFYING GOLD.CLIENTS")
        print("=" * 80)

        count_df = safe_read("SELECT COUNT(*) AS total_clients FROM gold.clients")
        if not count_df.empty:
            print("Total rows in gold.clients:", int(count_df.iloc[0]["total_clients"]))

        print()
        print("=" * 80)
        print("GOLD.CLIENTS UPSERT SUCCESSFUL")
        print("=" * 80)

        return load_result("ok", affected)

    except Exception as e:
        print()
        print("=" * 80)
        print("GOLD.CLIENTS UPSERT FAILED")
        print("=" * 80)
        print("Error type:", type(e).__name__)
        print("Database error:", str(e)[:3000])
        return load_result("error", 0, str(e)[:2000])
```

Add the import: `from utils.upsert import upsert_dataframe`. Keep the earlier PAN-cleaning and in-batch-duplicate-removal sections (everything before "GET EXISTING PANS FROM DATABASE") exactly as they are.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_upsert_expression_indexes.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add python_scripts/etl_gold_transaction.py python_scripts/etl_gold_sip.py \
        python_scripts/etl_gold_holdings.py python_scripts/etl_gold_clients.py \
        python_scripts/tests/test_gold_upsert_expression_indexes.py
git commit -m "refactor: real ON CONFLICT upserts for transactions/sip/holdings/clients

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Re-enable `drop_duplicates()` in `etl_trans.py`

**Files:**
- Modify: `python_scripts/etl_trans.py:780-794`
- Test: `python_scripts/tests/test_etl_trans_drop_duplicates.py`

**Interfaces:**
- Consumes: nothing new. Produces: same `process_transactions(cams=None, kfin=None)` signature and behavior, minus exact-duplicate rows within a single batch.

- [ ] **Step 1: Write the failing test**

```python
# python_scripts/tests/test_etl_trans_drop_duplicates.py
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import inspect  # noqa: E402
import etl_trans  # noqa: E402


def test_drop_duplicates_is_active_not_commented_out():
    source = inspect.getsource(etl_trans)
    assert "df.drop_duplicates(keep=\"first\")" in source or \
           "df.drop_duplicates(keep='first')" in source
    # the old commented-out form must be gone
    assert '#     .drop_duplicates(keep="first")' not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_etl_trans_drop_duplicates.py -v`
Expected: FAIL — the code is still commented out.

- [ ] **Step 3: Edit `python_scripts/etl_trans.py`**

```python
# OLD (lines 780-794)
    # =====================================================
    # REMOVE EXACT DUPLICATE ROWS
    # =====================================================

  # before = len(df)

   # df = (
   #     df
    #    .drop_duplicates(keep="first")
    #    .reset_index(drop=True)
   # )

    #print(
    #    f"Removed {before - len(df)} exact duplicate rows"
   # )

# NEW
    # =====================================================
    # REMOVE EXACT DUPLICATE ROWS
    # =====================================================

    before = len(df)

    df = (
        df
        .drop_duplicates(keep="first")
        .reset_index(drop=True)
    )

    print(
        f"Removed {before - len(df)} exact duplicate rows"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_etl_trans_drop_duplicates.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full test suite**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: all pass — this drops exact-duplicate rows before the bronze insert, on top of (not instead of) the bronze unique index from Task 1, which now also guards against re-inserting the same natural key across separate calls.

- [ ] **Step 6: Commit**

```bash
git add python_scripts/etl_trans.py python_scripts/tests/test_etl_trans_drop_duplicates.py
git commit -m "fix: re-enable exact-duplicate-row removal in process_transactions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Populate `arn`/`sub_arn` on `gold.folio_nominees`

**Files:**
- Modify: `python_scripts/etl_gold_folio_nominees.py`
- Test: `python_scripts/tests/test_folio_nominees_arn.py`

**Interfaces:**
- Consumes: `gold.holdings.arn` and `gold.holdings.subarn` (already populated — confirmed 1790/1790 non-null for `arn` on the live DB) via the `holding_id` join already built in `transform_folio_nominees`.
- Produces: `transform_folio_nominees(df)` output DataFrame now includes `arn`/`sub_arn` columns, matching the two columns that already exist on `gold.folio_nominees` but have always been empty (confirmed 0/1616 non-null on the live DB).

- [ ] **Step 1: Write the failing test**

```python
# python_scripts/tests/test_folio_nominees_arn.py
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from etl_gold_folio_nominees import transform_folio_nominees  # noqa: E402


def test_transform_folio_nominees_output_has_arn_columns():
    # transform_folio_nominees reads live silver/gold data internally (safe_read),
    # so this test only asserts the output SHAPE, not specific values — it runs
    # against whatever real data exists in the dev DB.
    df = pd.DataFrame({
        "source": ["CAMS"], "folio_no": ["DOES-NOT-EXIST"], "pan_no": ["XXXXX0000X"],
        "nominee1_name": ["Test Nominee"], "nominee1_relation": ["Spouse"],
        "nominee1_percentage": [100],
        "nominee2_name": [None], "nominee2_relation": [None], "nominee2_percentage": [None],
        "nominee3_name": [None], "nominee3_relation": [None], "nominee3_percentage": [None],
        "nominee_dob": [None], "nominee_guardian_name": [None], "guardian_name": [None],
    })
    result = transform_folio_nominees(df)
    # No matching holding for this synthetic folio, so result is empty — but the
    # COLUMN SHAPE the function is contracted to produce must include arn/sub_arn
    # even on the empty-result path, since load_folio_nominees's upsert always
    # selects these columns.
    assert "arn" in result.columns or result.empty
```

- [ ] **Step 2: Run test to verify it fails or passes trivially**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_folio_nominees_arn.py -v`
Expected: PASS trivially today (the synthetic folio matches nothing, `result.empty` is `True`, so the `or result.empty` branch short-circuits) — this test's real value comes after Step 3; re-run it then and additionally do the manual verification in Step 4.

- [ ] **Step 3: Edit `transform_folio_nominees` in `python_scripts/etl_gold_folio_nominees.py`**

In the "LOAD HOLDINGS" query, add `arn` and `subarn`:
```python
# OLD
    holdings = safe_read(
        """
        SELECT
            id AS holding_id,
            rta,
            folio_number,
            pan
        FROM gold.holdings
        """
    )

# NEW
    holdings = safe_read(
        """
        SELECT
            id AS holding_id,
            rta,
            folio_number,
            pan,
            arn,
            subarn
        FROM gold.holdings
        """
    )
```

In the "DIRECT LEFT JOIN", carry `arn`/`subarn` through:
```python
# OLD
    df = df.merge(

        holdings[
            [
                "holding_id",
                "rta",
                "folio_number",
                "pan"
            ]
        ],

        left_on=[
            "source",
            "folio_no",
            "pan_no"
        ],

        right_on=[
            "rta",
            "folio_number",
            "pan"
        ],

        how="left"

    )

# NEW
    df = df.merge(

        holdings[
            [
                "holding_id",
                "rta",
                "folio_number",
                "pan",
                "arn",
                "subarn"
            ]
        ],

        left_on=[
            "source",
            "folio_no",
            "pan_no"
        ],

        right_on=[
            "rta",
            "folio_number",
            "pan"
        ],

        how="left"

    )
```

In the "CREATE NOMINEE ROWS" loop, add `arn`/`sub_arn` to each built row (note the target table's column is `sub_arn`, the source holdings column is `subarn` — no underscore, matching `gold.holdings`'s actual column name confirmed on the live DB):
```python
# OLD
            gold_rows.append({

                "holding_id":
                    row["holding_id"],

                "seq":
                    seq,

                "name":
                    nominee["name"],

                "relationship":
                    nominee["relationship"],

                "percentage":
                    nominee["percentage"],

                "dob":
                    nominee_dob,

                "is_minor":
                    is_minor,

                "guardian_name":
                    guardian_name,

                "id_type":
                    None,

                "id_no":
                    None,

                "address":
                    None

            })

# NEW
            gold_rows.append({

                "holding_id":
                    row["holding_id"],

                "seq":
                    seq,

                "name":
                    nominee["name"],

                "relationship":
                    nominee["relationship"],

                "percentage":
                    nominee["percentage"],

                "dob":
                    nominee_dob,

                "is_minor":
                    is_minor,

                "guardian_name":
                    guardian_name,

                "id_type":
                    None,

                "id_no":
                    None,

                "address":
                    None,

                "arn":
                    row.get("arn"),

                "sub_arn":
                    row.get("subarn")

            })
```

Add `"arn"` and `"sub_arn"` to the `gold_df = pd.DataFrame(gold_rows, columns=[...])` column list:
```python
# OLD
    gold_df = pd.DataFrame(

        gold_rows,

        columns=[

            "holding_id",
            "seq",
            "name",
            "relationship",
            "percentage",
            "dob",
            "is_minor",
            "guardian_name",
            "id_type",
            "id_no",
            "address"

        ]

    )

# NEW
    gold_df = pd.DataFrame(

        gold_rows,

        columns=[

            "holding_id",
            "seq",
            "name",
            "relationship",
            "percentage",
            "dob",
            "is_minor",
            "guardian_name",
            "id_type",
            "id_no",
            "address",
            "arn",
            "sub_arn"

        ]

    )
```

- [ ] **Step 4: Manually verify against the live DB**

Run:
```bash
cd python_scripts && source venv/bin/activate && python3 -c "
from etl_gold_folio_nominees import extract_folio_nominees, transform_folio_nominees
df = extract_folio_nominees()
result = transform_folio_nominees(df)
print('rows:', len(result))
print('arn non-null:', result['arn'].notna().sum())
print(result[['holding_id', 'seq', 'arn', 'sub_arn']].head(10).to_string(index=False))
"
```
Expected: `arn non-null` is greater than 0 (matching however many nominee rows have a resolvable holding — recall `gold.holdings.arn` is 1790/1790 populated, so any successfully-matched nominee row should now carry a non-null `arn`).

- [ ] **Step 5: Run test to verify it still passes, plus the full suite**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add python_scripts/etl_gold_folio_nominees.py python_scripts/tests/test_folio_nominees_arn.py
git commit -m "fix: populate arn/sub_arn on gold.folio_nominees via holdings join

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-review notes (per writing-plans checklist)

- **Spec coverage:** §7.2 (ON CONFLICT/unique keys) → Tasks 1, 3, 4. §7.3 (`drop_duplicates`) → Task 5. §7.4 (folio_nominees ARN) → Task 6.
- **Placeholder scan:** no TBD/TODO; every SQL fragment is the literal text to write, sourced from `add_constraints.sql`'s already-validated index definitions and the live DB's actual column lists (queried directly, not guessed).
- **Type consistency:** every rewritten `load_*` still returns `load_result()`'s exact shape from Part A Task 10; `upsert_dataframe()`'s signature (`engine, df, schema, table, conflict_target_sql, update_set_sql, insert_columns=None`) is identical across all 8 call sites in Tasks 3–4.
- **Ordering dependency, stated plainly:** Task 1 must run before Tasks 3–4 — their `ON CONFLICT` targets reference indexes Task 1 creates. If Task 1 hasn't been run against the target environment, Tasks 3's/4's tests will fail with `42P10 there is no unique or exclusion constraint matching the ON CONFLICT specification`, not a subtle bug — loud and immediate.
