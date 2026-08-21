# File Decoupling and SIP Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `WBR2`/`WBR9`/`WBR49` (and the KFIN equivalents) from being held for each other before processing, and stop `gold.sip` rows from staying permanently stale when their enrichment data (transactions/client) arrives later than the SIP file itself.

**Architecture:** (1) Re-scope the pipeline's "hold group" concept in `etl_pipeline/hold_groups.py` from *per-RTA-trio* to *per-report-code* — each file becomes its own trivially-complete group, so it processes the moment it's reserved instead of waiting on siblings. `runner.py` needs no changes: its group-consumption logic is already generic over group size. (2) Give `gold.sip` a `enrichment_pending_since` marker set whenever `transform_sip()` can't find a transaction or client match for a row, and a bounded, self-terminating retry pass (`extract_pending_sip_retry_candidates()`) that re-runs the existing `transform_sip()`/`load_sip()` path against just those rows on every `gold_loader.load_gold()` run, until either it resolves or it ages out.

**Tech Stack:** Python 3.13, pandas, SQLAlchemy, PostgreSQL 14, pytest.

**Spec:** This plan's spec is the design discussion in this conversation (no separate spec file) — key decisions: (a) decouple all three files rather than hold-until-complete or gate-in-silver (gating was rejected: it can't distinguish "sibling not arrived yet" from "this SIP legitimately predates its first transaction," and would indefinitely hide valid new SIP registrations); (b) fix `gold.sip` via promote-then-reconcile, not a blocking wait — see `automated_pipeline_documentation.md` for the pipeline's existing architecture this plan modifies, and `python_scripts/etl_pipeline/README.md` for current operational docs.

## Global Constraints

- Every SQL migration file must be idempotent (`ADD COLUMN IF NOT EXISTS`, safe to re-run) — matches every existing file in `sql_scripts/`.
- No behavior change to `runner.py` is in scope — the decoupling must be achievable entirely inside `hold_groups.py` because `runner.py`'s group-consumption code (`_discover_and_reserve`, `_run_bronze_for_ready_groups`, `_report_gold_outcomes`, `_check_hold_timeouts`) is already generic over `{report_code: item}`-shaped `members` dicts of any size. If a task finds this assumption wrong, stop and flag it rather than silently expanding scope.
- `gold.sip` has no primary key and no stored `pan` column (confirmed live: `information_schema` query returned zero PK columns; `pan` is not in its column list). The retry mechanism must re-derive `pan` by joining back to `silver.sip_master_new` on the same natural key `load_sip()` already uses for `ON CONFLICT` — do not invent a new identity column as a shortcut.
- Reuse `transform_sip()`/`load_sip()` unchanged for the retry pass — do not write a bespoke partial-column `UPDATE`. The retry pass's only job is to select the right *input* rows; the existing transform/load logic already upserts correctly on the natural key.
- Every new/changed function needs a test that runs against the real dev DB (`utils.db.engine`), matching this repo's existing test convention (see `python_scripts/tests/etl_pipeline/*` and `python_scripts/tests/test_upsert.py` — no mocked DB layer anywhere in this codebase).

---

## File Structure

| File | Responsibility |
|---|---|
| `python_scripts/etl_pipeline/hold_groups.py` | *Modify.* Regroup by `(rta, report_code, arn_code, date)` instead of `(rta, arn_code, date)`; `required_report_codes(rta, report_code)` now returns `{report_code}` (self-requirement) instead of the RTA's full trio. |
| `python_scripts/tests/etl_pipeline/test_hold_groups.py` | *Modify.* Update every test to the new per-report-code grouping semantics. |
| `sql_scripts/gold_sip_enrichment_tracking.sql` | *Create.* Idempotent `ALTER TABLE gold.sip ADD COLUMN IF NOT EXISTS enrichment_pending_since TIMESTAMPTZ`. |
| `python_scripts/etl_gold_sip.py` | *Modify.* `transform_sip()` computes and stamps `enrichment_pending_since`; `load_sip()`'s column list/upsert `SET` clause carry it; new `extract_pending_sip_retry_candidates()` and `reconcile_pending_sip()` functions. |
| `python_scripts/gold_loader.py` | *Modify.* SIP section calls `reconcile_pending_sip()` after the normal `extract_sip()`/`transform_sip()`/`load_sip()` pass, folding its row count into `results["sip"]`. |
| `python_scripts/tests/test_gold_sip_enrichment_reconciliation.py` | *Create.* Tests for the pending-flag computation and the retry/reconcile path. |
| `automated_pipeline_documentation.md` | *Modify.* Update the hold/group section and add a section on SIP reconciliation. |
| `python_scripts/etl_pipeline/README.md` | *Modify.* One-paragraph note that files no longer wait for siblings. |

---

### Task 1: Decouple WBR2/WBR9/WBR49 (and KFIN) grouping

**Files:**
- Modify: `python_scripts/etl_pipeline/hold_groups.py`
- Test: `python_scripts/tests/etl_pipeline/test_hold_groups.py`

**Interfaces:**
- Produces: `hold_groups.required_report_codes(rta, report_code) -> set[str]` (signature changed — was `required_report_codes(rta)`), `hold_groups.coarse_group_key(rta, report_code, arn_code, created_at) -> str` (signature changed — was `coarse_group_key(rta, arn_code, created_at)`), `hold_groups.group_key(rta, report_code, arn_code, s3_date) -> str` (signature changed — was `group_key(rta, arn_code, s3_date)`). `group_pending_items()`, `ready_handoff_ids()`, `regroup_by_authoritative_key()`, `is_group_complete()` keep their existing signatures and return shapes — only what counts as "one group" changes.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `python_scripts/tests/etl_pipeline/test_hold_groups.py`:

```python
import sys
from datetime import date, datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PKG_ROOT))

from etl_pipeline import hold_groups  # noqa: E402


def test_s3_date_from_uri_parses_partition_date():
    uri = "s3://bucket/mailback/org_abc/arn_ARN-266051/2026-08-19/msg_123/processed/WBR2.csv"
    assert hold_groups.s3_date_from_uri(uri) == date(2026, 8, 19)


def test_s3_date_from_uri_raises_on_missing_date():
    try:
        hold_groups.s3_date_from_uri("s3://bucket/no/date/here.csv")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_required_report_codes_is_self_requirement_for_known_code():
    # Each report code requires only itself now — no more "hold for siblings".
    assert hold_groups.required_report_codes("CAMS", "WBR2") == {"WBR2"}
    assert hold_groups.required_report_codes("CAMS", "WBR9") == {"WBR9"}
    assert hold_groups.required_report_codes("CAMS", "WBR49") == {"WBR49"}
    assert hold_groups.required_report_codes("KFIN", "MFSD201") == {"MFSD201"}
    assert hold_groups.required_report_codes("KFIN", "MFSD211") == {"MFSD211"}
    assert hold_groups.required_report_codes("KFIN", "MFSD243") == {"MFSD243"}


def test_required_report_codes_unrecognized_pair_returns_empty():
    assert hold_groups.required_report_codes("CAMS", "NOT_A_REAL_CODE") == set()
    assert hold_groups.required_report_codes("UNKNOWN", "WBR2") == set()


def _pending_item(id_, rta, report_code, arn_code, created_at):
    return {"id": id_, "rta": rta, "report_code": report_code,
            "arn_code": arn_code, "created_at": created_at}


def test_group_pending_items_each_report_code_is_its_own_ready_group():
    # Previously WBR2+WBR9+WBR49 for the same arn+date formed ONE group that
    # needed all three present. Now each forms its OWN group, ready alone.
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [
        _pending_item("h1", "CAMS", "WBR2", "ARN-1", now),
        _pending_item("h2", "CAMS", "WBR9", "ARN-1", now),
        # WBR49 deliberately absent — must NOT block h1/h2
    ]
    groups = hold_groups.group_pending_items(items)
    assert len(groups) == 2  # one group per report_code, not one combined group
    for group in groups.values():
        assert group["missing"] == set()
    ready_ids = hold_groups.ready_handoff_ids(groups)
    assert set(ready_ids) == {"h1", "h2"}


def test_group_pending_items_unrecognized_report_code_never_ready():
    now = datetime(2026, 8, 19, 10, 0, 0)
    items = [_pending_item("h1", "CAMS", "NOT_A_REAL_CODE", "ARN-1", now)]
    groups = hold_groups.group_pending_items(items)
    group = next(iter(groups.values()))
    assert group["missing"] == set()  # required is empty, so "missing" is empty too...
    assert hold_groups.ready_handoff_ids(groups) == []  # ...but never ready, since required itself is empty


def _reserved_item(handoff_id, rta, report_code, arn_code, s3_date_str, filename):
    return {
        "handoff_id": handoff_id, "rta": rta, "report_code": report_code,
        "arn_code": arn_code, "filename": filename, "payload_format": "csv",
        "content_hash": f"hash-{handoff_id}", "file_size": 100,
        "source_s3_uri": f"s3://bucket/mailback/org_x/arn_{arn_code}/{s3_date_str}/msg_1/processed/{filename}",
    }


def test_regroup_by_authoritative_key_single_file_is_immediately_complete():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 1
    group = next(iter(groups.values()))
    assert hold_groups.is_group_complete(group)
    assert group["s3_date"] == date(2026, 8, 19)


def test_regroup_by_authoritative_key_splits_different_report_codes_even_on_same_date():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR9", "ARN-1", "2026-08-19", "WBR9.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 2  # WBR2 and WBR9 no longer merge into one group
    assert all(hold_groups.is_group_complete(g) for g in groups.values())  # each complete on its own


def test_regroup_by_authoritative_key_splits_on_real_date_mismatch():
    items = [
        _reserved_item("h1", "CAMS", "WBR2", "ARN-1", "2026-08-19", "WBR2.csv"),
        _reserved_item("h2", "CAMS", "WBR2", "ARN-1", "2026-08-20", "WBR2.csv"),
    ]
    groups = hold_groups.regroup_by_authoritative_key(items)
    assert len(groups) == 2
    assert all(hold_groups.is_group_complete(g) for g in groups.values())  # each is still self-complete
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_hold_groups.py -v`
Expected: several `FAIL`s — `required_report_codes()` still takes one positional arg (`TypeError`), grouping still merges by arn+date only (`len(groups) == 1` assertions fail where the new tests expect 2).

- [ ] **Step 3: Rewrite `hold_groups.py`**

Replace the full contents of `python_scripts/etl_pipeline/hold_groups.py`:

```python
import re
from datetime import date, datetime

# Every report code CAMS/KFIN are known to send. This is NOT a "must arrive
# together" set any more — each report code processes independently the
# moment it arrives (see the 2026-08-20 file-decoupling plan). Kept only to
# recognize which (rta, report_code) pairs are real, so a typo'd/unknown
# report_code never becomes falsely "ready".
KNOWN_REPORT_CODES = {
    "CAMS": {"WBR2", "WBR9", "WBR49"},
    "KFIN": {"MFSD201", "MFSD211", "MFSD243"},
}

_S3_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def s3_date_from_uri(source_s3_uri):
    match = _S3_DATE_RE.search(source_s3_uri)
    if not match:
        raise ValueError(f"No YYYY-MM-DD partition date found in {source_s3_uri!r}")
    return datetime.strptime(match.group(1), "%Y-%m-%d").date()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def coarse_group_key(rta, report_code, arn_code, created_at):
    day = _as_date(created_at)
    return f"{rta}|{report_code}|{arn_code or ''}|{day.isoformat()}"


def group_key(rta, report_code, arn_code, s3_date):
    day = s3_date if isinstance(s3_date, date) else s3_date_from_uri(s3_date)
    return f"{rta}|{report_code}|{arn_code or ''}|{day.isoformat()}"


def required_report_codes(rta, report_code):
    """A group's requirement is just its own report_code — each file type
    processes independently, no longer held for its siblings. Returns
    {report_code} if it's a recognized code for that RTA, else an empty set
    (an unrecognized code never becomes "ready")."""
    known = KNOWN_REPORT_CODES.get((rta or "").upper(), set())
    return {report_code} if report_code in known else set()


def group_pending_items(pending_items):
    """
    pending_items: list of dicts shaped like GET /pending's EtlHandoffRead —
    must have 'id', 'rta', 'report_code', 'arn_code', 'created_at'.

    Returns {coarse_group_key: {rta, arn_code, required, present, missing, items}}.

    Each group is scoped to a single (rta, report_code, arn_code, date) —
    WBR2/WBR9/WBR49 for the same distributor+date land in three separate
    groups, each complete the moment its own single file is present.
    """
    groups = {}
    for item in pending_items:
        rta = item["rta"]
        report_code = item["report_code"]
        arn_code = item.get("arn_code")
        key = coarse_group_key(rta, report_code, arn_code, item["created_at"])
        group = groups.setdefault(key, {
            "rta": rta,
            "arn_code": arn_code,
            "required": required_report_codes(rta, report_code),
            "present": set(),
            "items": [],
        })
        group["present"].add(item["report_code"])
        group["items"].append(item)

    for group in groups.values():
        group["missing"] = group["required"] - group["present"]

    return groups


def ready_handoff_ids(groups):
    """handoff ids (peek item['id']) belonging to coarse groups with nothing missing."""
    ids = []
    for group in groups.values():
        if group["required"] and not group["missing"]:
            ids.extend(item["id"] for item in group["items"])
    return ids


def regroup_by_authoritative_key(reserved_items):
    """
    reserved_items: list of dicts shaped like POST /reservations' EtlHandoffItem —
    must have 'handoff_id', 'rta', 'report_code', 'arn_code', 'source_s3_uri',
    plus whatever else the caller wants carried through (filename, content_hash, ...).

    Returns {group_key: {rta, arn_code, s3_date, required, members: {report_code: item}}}.
    Each group is scoped to a single report_code (see group_pending_items).
    """
    groups = {}
    for item in reserved_items:
        s3_date = s3_date_from_uri(item["source_s3_uri"])
        key = group_key(item["rta"], item["report_code"], item.get("arn_code"), s3_date)
        group = groups.setdefault(key, {
            "rta": item["rta"],
            "arn_code": item.get("arn_code"),
            "s3_date": s3_date,
            "required": required_report_codes(item["rta"], item["report_code"]),
            "members": {},
        })
        group["members"][item["report_code"]] = item
    return groups


def is_group_complete(group):
    return bool(group["required"]) and group["required"] <= set(group["members"].keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/test_hold_groups.py -v`
Expected: all `PASS`.

- [ ] **Step 5: Run the full `etl_pipeline` suite to confirm nothing downstream broke**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/etl_pipeline/ -v --ignore=tests/etl_pipeline/test_s3_client.py`
Expected: all `PASS`, including `test_runner.py` — its mocked `peek_pending` scenario (2 of 3 CAMS report codes present) now produces **two independently-ready groups** instead of one held group; re-read that test's assertions before trusting a green run blindly, since its *meaning* changed even though its literal assertions (`report_outcome.assert_not_called()` when nothing was mocked as reserved) may still happen to pass. If it needs updating to actually exercise the new "each file ready alone" behavior, update it — a stale test that passes for the wrong reason is worse than a failing one.

- [ ] **Step 6: Manually sanity-check against the real dev DB**

Run: `cd python_scripts && source venv/bin/activate && python3 -m etl_pipeline.runner`, then:

```sql
SELECT group_key, status, required_report_codes, members
FROM pipeline.etl_report_group_hold
WHERE arn_code = 'ARN-266051'
ORDER BY last_updated_at DESC;
```

Expected: the existing `CAMS|ARN-266051|2026-08-19` HOLDING group (waiting on `WBR49`, per the earlier session) either resolves into per-report-code groups on this run, or — since its `WBR2`/`WBR9` were already reserved under the OLD combined key — may need a fresh peek cycle to re-key. If it's still sitting under the old 3-part key format, that's expected (old data, old key shape) — not a regression; new arrivals will use the new key shape going forward.

- [ ] **Step 7: Commit**

```bash
git add python_scripts/etl_pipeline/hold_groups.py python_scripts/tests/etl_pipeline/test_hold_groups.py
git commit -m "feat: decouple WBR2/WBR9/WBR49 (and KFIN) from hold-until-complete grouping"
```

---

### Task 2: Add `gold.sip.enrichment_pending_since` column

**Files:**
- Create: `sql_scripts/gold_sip_enrichment_tracking.sql`

**Interfaces:**
- Produces: `gold.sip.enrichment_pending_since TIMESTAMPTZ` (nullable) column, applied to the live dev DB by this task.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the migration file**

```sql
-- Tracks gold.sip rows whose enrichment (arn/sub_arn/arn_id from transaction
-- data, client_id from gold.clients) could not be resolved at load time
-- because the sibling WBR2/WBR9 (or KFIN equivalent) file hadn't arrived
-- yet. NULL means either "fully resolved" or "structurally blank" (e.g. a
-- genuine direct-plan SIP with no distributor) — set only when NO match was
-- found at all, not when a match was found with an empty field.
--
-- Idempotent: safe to re-run.

ALTER TABLE gold.sip ADD COLUMN IF NOT EXISTS enrichment_pending_since TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_gold_sip_enrichment_pending_since
    ON gold.sip (enrichment_pending_since)
    WHERE enrichment_pending_since IS NOT NULL;
```

- [ ] **Step 2: Apply it to the dev DB**

```bash
cd python_scripts && source venv/bin/activate && python3 - <<'EOF'
import sys; sys.path.insert(0, ".")
from sqlalchemy import text
from utils.db import engine

with open("../sql_scripts/gold_sip_enrichment_tracking.sql") as f:
    sql = f.read()

with engine.begin() as conn:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt and not stmt.startswith("--"):
            conn.execute(text(stmt))

print("Applied OK")
EOF
```

- [ ] **Step 3: Verify the column exists**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from sqlalchemy import text
from utils.db import engine
with engine.connect() as conn:
    print(conn.execute(text(
        \"SELECT column_name FROM information_schema.columns WHERE table_schema='gold' AND table_name='sip' AND column_name='enrichment_pending_since'\"
    )).fetchall())
"
```
Expected: `[('enrichment_pending_since',)]`

- [ ] **Step 4: Commit**

```bash
git add sql_scripts/gold_sip_enrichment_tracking.sql
git commit -m "feat: add gold.sip.enrichment_pending_since tracking column"
```

---

### Task 3: Stamp `enrichment_pending_since` in `transform_sip()` / `load_sip()`

**Files:**
- Modify: `python_scripts/etl_gold_sip.py`
- Test: `python_scripts/tests/test_gold_sip_enrichment_reconciliation.py` (created here, extended in Task 4)

**Interfaces:**
- Consumes: `gold.sip.enrichment_pending_since` column from Task 2.
- Produces: `transform_sip(df)` output now includes an `"enrichment_pending_since"` column; `load_sip(gold_df)` persists and upserts it.

- [ ] **Step 1: Write the failing test**

Create `python_scripts/tests/test_gold_sip_enrichment_reconciliation.py`:

```python
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402
from utils.db import engine  # noqa: E402
from etl_gold_sip import transform_sip  # noqa: E402


def _sip_silver_row(**overrides):
    row = {
        "source": "CAMS", "zone": None, "branch": None, "ter_location": None,
        "inv_name": None, "pan": "ABCDE1234F", "folio_no": "FOLIO-1", "folio_old": None,
        "inv_iin": None, "inv_dp_id": None, "inv_client_id": None, "dp_inv_name": None,
        "scheme_code": "SCH1", "product_code": "SCH1", "scheme_name": "Test Scheme",
        "plan": None, "sub_arn_code": None, "agent_name": None, "subbroker": None,
        "euin": None, "aut_trntyp": "SIP", "payment_mode": None, "periodicity": "MONTHLY",
        "auto_amount": 1000, "no_of_installments": 12, "period_day": 5,
        "reg_date": datetime(2026, 8, 1), "from_date": None, "to_date": None,
        "cease_date": None, "pause_from_date": None, "pause_to_date": None,
        "target_scheme": None, "target_scheme_code": None, "target_scheme_name": None,
        "target_plan": None, "bank": None, "ac_holder_name": None, "ecs_account_no": None,
        "ecsno": None, "instrm_no": None, "cheq_micr_no": None, "umrn_code": None,
        "ac_type": None, "amc_code": "AMC1", "user_code": None, "package_name": None,
        "special_product": None, "subtrxndesc": None, "remarks": None, "top_up_frq": None,
        "top_up_amt": None, "top_up_perc": None, "status": "ACTIVE", "modify_flag": None,
        "scheme_folio_number": None, "request_ref_no": None, "ft_sip_regno": "SIPREG-1",
        "scheme_id": None, "created_at": datetime.now(timezone.utc), "updated_at": None,
        "flag": 0,
    }
    row.update(overrides)
    return row


def test_transform_sip_marks_enrichment_pending_when_no_transaction_or_client_match():
    df = pd.DataFrame([_sip_silver_row(pan="NOMATCH1234")])  # PAN not in gold.clients, folio not in transactions
    gold_df = transform_sip(df)
    assert "enrichment_pending_since" in gold_df.columns
    assert pd.notna(gold_df.loc[0, "enrichment_pending_since"])
    assert pd.isna(gold_df.loc[0, "arn"])
    assert pd.isna(gold_df.loc[0, "client_id"])


def test_transform_sip_no_pending_marker_when_fully_resolved():
    pan = f"MATCH{uuid.uuid4().hex[:6].upper()}"
    folio = f"FOLIO-{uuid.uuid4().hex[:8]}"
    rta = "CAMS"
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO gold.clients (user_id, pan, full_name, status, created_at) "
                "VALUES (:id, :pan, 'Test Client', 'ACTIVE', now())"
            ), {"id": str(uuid.uuid4()), "pan": pan})
            conn.execute(text(
                "INSERT INTO silver.transaction_master_new (source, pan, folio_no, brokcode, created_at) "
                "VALUES (:rta, :pan, :folio, 'ARN-9999', now())"
            ), {"rta": rta, "pan": pan, "folio": folio})

        df = pd.DataFrame([_sip_silver_row(pan=pan, folio_no=folio, source=rta)])
        gold_df = transform_sip(df)

        assert pd.isna(gold_df.loc[0, "enrichment_pending_since"])
        assert gold_df.loc[0, "arn"] == "ARN-9999"
        assert gold_df.loc[0, "client_id"] is not None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :pan"), {"pan": pan})
            conn.execute(text("DELETE FROM silver.transaction_master_new WHERE pan = :pan"), {"pan": pan})
```

Note: adjust the `gold.clients`/`silver.transaction_master_new` INSERT column lists in Step 1 if this repo's live schema requires additional NOT NULL columns not listed here — check with:
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from sqlalchemy import text
from utils.db import engine
with engine.connect() as conn:
    for t in [('gold','clients'), ('silver','transaction_master_new')]:
        print(t, conn.execute(text(
            \"SELECT column_name, is_nullable FROM information_schema.columns WHERE table_schema=:s AND table_name=:t AND is_nullable='NO'\"
        ), {'s': t[0], 't': t[1]}).fetchall())
"
```
before running the test, and fill in any other NOT NULL columns the live schema requires.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_sip_enrichment_reconciliation.py -v`
Expected: `FAIL` — `KeyError: 'enrichment_pending_since'` (column doesn't exist in `transform_sip()`'s output yet).

- [ ] **Step 3: Modify `transform_sip()` to compute the pending marker**

In `python_scripts/etl_gold_sip.py`, the ARN/sub_arn block currently ends around line 1041 (`gold_df["sub_arn"] = (...)`) inside `if not transactions.empty:`, and `gold_df["client_id"]` is set around line 821-824. Add a `txn_match_found` mask inside the `if not transactions.empty:` block (right after the existing `sub_arn` list-comprehension, before the `print("Mapped ARN rows:", ...)` line at ~1043), and a `client_match_found` mask right after `gold_df["client_id"]` is set (~line 824):

```python
    # ========================================================
    # CLIENT ID
    # ...
    # ========================================================

    print("Loading client mapping...")

    clients = safe_read(
        """
        SELECT
            user_id,
            pan
        FROM gold.clients
        WHERE pan IS NOT NULL
        """
    )

    if not clients.empty:

        clients["pan_clean"] = clean_pan(
            clients["pan"]
        )

        client_lookup = dict(
            zip(
                clients["pan_clean"],
                clients["user_id"]
            )
        )

    else:
        client_lookup = {}

    gold_df["client_id"] = (
        df["pan_clean"]
        .map(client_lookup)
    )

    # ========================================================
    # ENRICHMENT PENDING TRACKING — client match
    #
    # A PAN not present in gold.clients at all means WBR9 (or its
    # downstream gold.clients row) hasn't arrived yet, NOT that this client
    # will never exist. Track separately from client_id itself so a later
    # reconciliation pass can tell "not yet resolved" apart from "resolved
    # to nothing".
    # ========================================================

    client_match_found = df["pan_clean"].isin(client_lookup.keys())
```

Then, still inside `if not transactions.empty:`, right after the existing `sub_arn` block (after the two `print("Mapped ARN rows:"...)`/`print("Mapped Sub ARN rows:"...)` lines, before the `# TRANSACTION TEXT` section), add:

```python
        # ====================================================
        # ENRICHMENT PENDING TRACKING — transaction match
        #
        # A (rta, folio) not present in arn_lookup.index at all means no
        # transaction data exists yet for this folio (WBR2 hasn't arrived),
        # NOT that this SIP is structurally distributor-less. A folio THAT
        # IS present but with a blank brokcode (a genuine direct-plan
        # investment) is correctly resolved, not pending.
        # ====================================================

        txn_match_found = pd.Series(
            [
                (df.loc[idx, "rta_clean"], df.loc[idx, "folio_clean"]) in arn_lookup.index
                for idx in df.index
            ],
            index=df.index,
        )
```

And, right after the `else:` branch that runs when `transactions.empty` (the `if not transactions.empty:` block's implicit else — there's no explicit `else:` today; `gold_df["arn"]`/`gold_df["completed_installments"]`/etc. were already defaulted before the `if` at lines ~901-914), add the corresponding default for the empty case. Immediately before the `if not transactions.empty:` line (~line 920), the defaults block already sets `gold_df["completed_installments"] = 0`, `gold_df["bounced_installments"] = 0`, `gold_df["arn"] = pd.NA series`, `gold_df["sub_arn"] = pd.NA series` — add right after those:

```python
    gold_df["arn"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    gold_df["sub_arn"] = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    txn_match_found = pd.Series(False, index=df.index)  # no transaction table at all yet
```

(This default assignment is overwritten by the real computation inside `if not transactions.empty:` above when transactions do exist — same pattern the existing `arn`/`sub_arn` defaults already use.)

Finally, right before the `# GOLD CREATED AT` section (~line 1393, just before `gold_load_timestamp = datetime.now(timezone.utc)`), add:

```python
    # ========================================================
    # ENRICHMENT PENDING SINCE
    #
    # Set only when NEITHER the transaction match NOR the client match was
    # found — i.e. genuinely nothing to enrich from yet, not "matched but
    # the field happens to be blank" (e.g. a real direct-plan investment
    # with no ARN). NULL = fully resolved or structurally not applicable.
    # ========================================================

    gold_load_timestamp_for_pending = datetime.now(timezone.utc)

    enrichment_missing = (~txn_match_found) | (~client_match_found)

    gold_df["enrichment_pending_since"] = pd.NaT
    gold_df.loc[enrichment_missing, "enrichment_pending_since"] = gold_load_timestamp_for_pending
```

Add `"enrichment_pending_since"` to the `columns` list (~line 1408-1438), as the last entry after `"created_at"`:

```python
    columns = [
        "rta",
        "sip_reg_no",
        "folio_number",
        "scheme_code",
        "scheme_name",
        "amc_code",
        "isin",
        "amount",
        "frequency",
        "start_date",
        "end_date",
        "next_due_date",
        "sip_day",
        "mandate_id",
        "status",
        "registered_date",
        "ceased_date",
        "scheme_id",
        "amc_id",
        "client_id",
        "sip_type",
        "registered_installments",
        "completed_installments",
        "bounced_installments",
        "ceased_reason",
        "arn_id",
        "arn",
        "sub_arn",
        "created_at",
        "enrichment_pending_since"
    ]
```

- [ ] **Step 4: Modify `load_sip()` to persist and upsert the new column**

In `python_scripts/etl_gold_sip.py`'s `load_sip()`, add `"enrichment_pending_since"` to `gold_columns` (~line 1693-1723), same position as above:

```python
    gold_columns = [
        "rta",
        "sip_reg_no",
        "folio_number",
        "scheme_code",
        "scheme_name",
        "amc_code",
        "isin",
        "amount",
        "frequency",
        "start_date",
        "end_date",
        "next_due_date",
        "sip_day",
        "mandate_id",
        "status",
        "registered_date",
        "ceased_date",
        "scheme_id",
        "amc_id",
        "client_id",
        "sip_type",
        "registered_installments",
        "completed_installments",
        "bounced_installments",
        "ceased_reason",
        "arn_id",
        "arn",
        "sub_arn",
        "created_at",
        "enrichment_pending_since"
    ]
```

And append `enrichment_pending_since = EXCLUDED.enrichment_pending_since` to `update_set_sql` (~line 1798-1811):

```python
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
        "end_date = EXCLUDED.end_date, "
        "enrichment_pending_since = EXCLUDED.enrichment_pending_since"
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_sip_enrichment_reconciliation.py -v`
Expected: both tests `PASS`.

- [ ] **Step 6: Run the full gold-related test suite to check for regressions**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_upsert_scheme_and_folio.py tests/test_gold_upsert_expression_indexes.py tests/test_gold_upsert_formal_constraints.py tests/test_upsert.py tests/test_gold_result.py -v`
Expected: all `PASS` — this change only adds a column, doesn't touch any other gold table's logic.

- [ ] **Step 7: Commit**

```bash
git add python_scripts/etl_gold_sip.py python_scripts/tests/test_gold_sip_enrichment_reconciliation.py
git commit -m "feat: stamp gold.sip.enrichment_pending_since when transaction/client match is missing"
```

---

### Task 4: Bounded, self-terminating reconciliation pass

**Files:**
- Modify: `python_scripts/etl_gold_sip.py`
- Modify: `python_scripts/gold_loader.py`
- Test: `python_scripts/tests/test_gold_sip_enrichment_reconciliation.py`

**Interfaces:**
- Consumes: `transform_sip(df) -> gold_df`, `load_sip(gold_df) -> {"status", "rows_loaded", "error"}` from Task 3 — both unchanged, reused as-is.
- Produces: `extract_pending_sip_retry_candidates(limit=200, max_age_days=30) -> pd.DataFrame` (same column shape as `extract_sip()`'s output, suitable to feed straight into `transform_sip()`), `reconcile_pending_sip(limit=200, max_age_days=30) -> {"status", "rows_loaded", "error"}` (same shape as `load_result()`, orchestrates extract→transform→load for the retry batch).

- [ ] **Step 1: Write the failing test**

Append to `python_scripts/tests/test_gold_sip_enrichment_reconciliation.py`:

```python
def test_extract_pending_sip_retry_candidates_finds_stale_row_and_excludes_fresh_and_expired():
    from etl_gold_sip import extract_pending_sip_retry_candidates

    # .upper() matters: gold.sip.folio_number is always stored uppercase (clean_folio()),
    # and the retry query's SQL uppercases silver.sip_master_new.folio_no before comparing —
    # inserting a mixed-case folio directly into gold.sip here (bypassing that cleaning)
    # would make the join silently miss it.
    rta, folio, scheme_code, amount = "CAMS", f"FOLIO-{uuid.uuid4().hex[:8].upper()}", "SCH-PENDING", 500
    reg_date = datetime(2026, 8, 1).date()
    fresh_row = dict(rta=rta, folio_number=folio, scheme_code=scheme_code,
                      registered_date=reg_date, amount=amount,
                      sip_reg_no="X", enrichment_pending_since=datetime.now(timezone.utc))
    expired_row = dict(rta=rta, folio_number=f"{folio}-EXPIRED", scheme_code=scheme_code,
                        registered_date=reg_date, amount=amount,
                        sip_reg_no="Y", enrichment_pending_since=datetime(2000, 1, 1, tzinfo=timezone.utc))

    try:
        with engine.begin() as conn:
            for row in (fresh_row, expired_row):
                conn.execute(text(
                    "INSERT INTO gold.sip (rta, folio_number, scheme_code, registered_date, amount, "
                    "sip_reg_no, enrichment_pending_since, created_at) "
                    "VALUES (:rta, :folio_number, :scheme_code, :registered_date, :amount, "
                    ":sip_reg_no, :enrichment_pending_since, now())"
                ), row)
            conn.execute(text(
                "INSERT INTO silver.sip_master_new (source, folio_no, product_code, scheme_code, "
                "reg_date, auto_amount, ft_sip_regno, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :scheme_code, :reg_date, :amount, 'X', now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount})

        candidates = extract_pending_sip_retry_candidates(limit=200, max_age_days=30)

        assert (candidates["folio_no"] == folio).any()
        assert not (candidates["folio_no"] == f"{folio}-EXPIRED").any()  # aged out, excluded
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number IN (:f1, :f2)"),
                         {"f1": folio, "f2": f"{folio}-EXPIRED"})
            conn.execute(text("DELETE FROM silver.sip_master_new WHERE folio_no = :f"), {"f": folio})


def test_reconcile_pending_sip_clears_marker_once_data_arrives():
    from etl_gold_sip import reconcile_pending_sip

    pan = f"RECON{uuid.uuid4().hex[:6].upper()}"
    # .upper() for the same reason as the retry-candidates test above.
    rta, folio, scheme_code, amount = "CAMS", f"FOLIO-{uuid.uuid4().hex[:8].upper()}", "SCH-RECON", 750
    reg_date = datetime(2026, 8, 1).date()

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO gold.sip (rta, folio_number, scheme_code, registered_date, amount, "
                "sip_reg_no, enrichment_pending_since, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :reg_date, :amount, 'RECON1', now(), now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount})
            conn.execute(text(
                "INSERT INTO silver.sip_master_new (source, folio_no, product_code, scheme_code, "
                "reg_date, auto_amount, ft_sip_regno, pan, created_at) "
                "VALUES (:rta, :folio, :scheme_code, :scheme_code, :reg_date, :amount, 'RECON1', :pan, now())"
            ), {"rta": rta, "folio": folio, "scheme_code": scheme_code,
                "reg_date": reg_date, "amount": amount, "pan": pan})

            # The dependency arrives NOW, after the SIP row was already gold-loaded pending:
            conn.execute(text(
                "INSERT INTO gold.clients (user_id, pan, full_name, status, created_at) "
                "VALUES (:id, :pan, 'Recon Client', 'ACTIVE', now())"
            ), {"id": str(uuid.uuid4()), "pan": pan})
            conn.execute(text(
                "INSERT INTO silver.transaction_master_new (source, pan, folio_no, brokcode, created_at) "
                "VALUES (:rta, :pan, :folio, 'ARN-RECON', now())"
            ), {"rta": rta, "pan": pan, "folio": folio})

        result = reconcile_pending_sip(limit=200, max_age_days=30)
        assert result["status"] == "ok"
        assert result["rows_loaded"] >= 1

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT arn, client_id, enrichment_pending_since FROM gold.sip "
                "WHERE folio_number = :f"
            ), {"f": folio}).fetchone()
        assert row.arn == "ARN-RECON"
        assert row.client_id is not None
        assert row.enrichment_pending_since is None
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM gold.sip WHERE folio_number = :f"), {"f": folio})
            conn.execute(text("DELETE FROM silver.sip_master_new WHERE folio_no = :f"), {"f": folio})
            conn.execute(text("DELETE FROM silver.transaction_master_new WHERE folio_no = :f"), {"f": folio})
            conn.execute(text("DELETE FROM gold.clients WHERE pan = :p"), {"p": pan})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_sip_enrichment_reconciliation.py -v -k "retry_candidates or reconcile_pending"`
Expected: `FAIL` — `ImportError: cannot import name 'extract_pending_sip_retry_candidates'`.

- [ ] **Step 3: Implement `extract_pending_sip_retry_candidates()` and `reconcile_pending_sip()`**

In `python_scripts/etl_gold_sip.py`, add after `extract_sip()` (after line 460, before `# TRANSFORM GOLD SIP`):

```python
# ============================================================
# EXTRACT PENDING SIP RETRY CANDIDATES
#
# Rows in gold.sip whose enrichment (arn/client_id) is still marked
# pending, re-matched back to their original silver.sip_master_new row via
# the SAME natural key load_sip()'s ON CONFLICT clause already uses as
# this row's identity — (rta, folio_number, scheme_code, registered_date,
# amount). Returns rows in extract_sip()'s exact column shape so they can
# be fed straight into transform_sip()/load_sip() unchanged.
# ============================================================

def extract_pending_sip_retry_candidates(limit=200, max_age_days=30):

    print("=" * 80)
    print("EXTRACTING PENDING SIP RETRY CANDIDATES")
    print("=" * 80)

    query = """
        SELECT

            s.source,
            s.zone,
            s.branch,
            s.ter_location,
            s.inv_name,
            s.pan,
            s.folio_no,
            s.folio_old,
            s.inv_iin,
            s.inv_dp_id,
            s.inv_client_id,
            s.dp_inv_name,

            s.scheme_code,

            s.product_code,
            s.scheme_name,
            s.plan,
            s.sub_arn_code,
            s.agent_name,
            s.subbroker,
            s.euin,
            s.aut_trntyp,
            s.payment_mode,
            s.periodicity,
            s.auto_amount,
            s.no_of_installments,
            s.period_day,
            s.reg_date,
            s.from_date,
            s.to_date,
            s.cease_date,
            s.pause_from_date,
            s.pause_to_date,
            s.target_scheme,
            s.target_scheme_code,
            s.target_scheme_name,
            s.target_plan,
            s.bank,
            s.ac_holder_name,
            s.ecs_account_no,
            s.ecsno,
            s.instrm_no,
            s.cheq_micr_no,
            s.umrn_code,
            s.ac_type,
            s.amc_code,
            s.user_code,
            s.package_name,
            s.special_product,
            s.subtrxndesc,
            s.remarks,
            s.top_up_frq,
            s.top_up_amt,
            s.top_up_perc,
            s.status,
            s.modify_flag,
            s.scheme_folio_number,
            s.request_ref_no,
            s.ft_sip_regno,

            s.scheme_id,

            s.created_at,
            s.updated_at,

            s.flag

        FROM silver.sip_master_new s

        JOIN gold.sip g
            ON UPPER(TRIM(s.source)) = g.rta
           -- Mirrors clean_folio() exactly (etl_gold_sip.py:93): strip,
           -- upper, then strip ALL occurrences of the literal ".0"
           -- substring (str.replace(".0", "", regex=False) in pandas) —
           -- NOT just a trailing ".0". A regex anchored to the end (\.0$)
           -- would silently mismatch any folio with an embedded ".0".
           AND REPLACE(UPPER(TRIM(CAST(s.folio_no AS TEXT))), '.0', '') = g.folio_number
           -- Mirrors clean_scheme_code() exactly (etl_gold_sip.py:112):
           -- strip + upper on scheme_code alone, no product_code fallback
           -- (transform_sip() never falls back to product_code at the gold
           -- stage — that fallback only exists in the SILVER scheme_id
           -- join, a different purpose).
           AND UPPER(TRIM(CAST(s.scheme_code AS TEXT))) = g.scheme_code
           AND s.reg_date = g.registered_date
           AND s.auto_amount = g.amount

        WHERE g.enrichment_pending_since IS NOT NULL
          AND g.enrichment_pending_since > now() - make_interval(days => %(max_age_days)s)

        ORDER BY g.enrichment_pending_since ASC

        LIMIT %(limit)s
    """

    df = safe_read(query, params={"max_age_days": max_age_days, "limit": limit})

    print()
    print("Retry candidates found:", len(df))

    return df


# ============================================================
# RECONCILE PENDING SIP
#
# Runs the EXACT same transform_sip()/load_sip() path as a normal load,
# just fed the bounded retry-candidate batch instead of extract_sip()'s
# cursor-based selection. Rows whose enrichment still can't be resolved
# simply get re-stamped with a fresh (or unchanged) enrichment_pending_since
# by transform_sip() itself and get retried again next run, until either
# they resolve or age past max_age_days and drop out of the candidate set.
# ============================================================

def reconcile_pending_sip(limit=200, max_age_days=30):

    print("=" * 80)
    print("RECONCILING PENDING SIP ENRICHMENT")
    print("=" * 80)

    df = extract_pending_sip_retry_candidates(limit=limit, max_age_days=max_age_days)

    if df.empty:
        print("No pending SIP rows to reconcile.")
        return load_result("skipped", 0)

    gold_df = transform_sip(df)

    if gold_df.empty:
        return load_result("skipped", 0)

    return load_sip(gold_df)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/test_gold_sip_enrichment_reconciliation.py -v`
Expected: all 4 tests `PASS`.

- [ ] **Step 5: Wire `reconcile_pending_sip()` into `gold_loader.py`**

In `python_scripts/gold_loader.py`, modify the `# GOLD SIP` section (the `if SIP_AVAILABLE:` block, currently ending around line 365):

```python
    # =====================================================
    # GOLD SIP
    # =====================================================

    if SIP_AVAILABLE:

        try:

            print("\nLoading Gold SIP")

            sip_df = extract_sip()

            if not sip_df.empty:

                sip_gold_df = transform_sip(
                    sip_df
                )

                if not sip_gold_df.empty:

                    results["sip"] = load_sip(
                        sip_gold_df
                    )

                    print(
                        "SIP loaded successfully"
                    )

                else:

                    results["sip"] = load_result("skipped", 0)

            else:

                print(
                    "No SIP data found"
                )
                results["sip"] = load_result("skipped", 0)

        except Exception as e:

            print("SIP Gold Failed")
            print(e)
            results["sip"] = load_result("error", 0, str(e))

        # =====================================================
        # SIP ENRICHMENT RECONCILIATION
        #
        # Retries any previously-pending rows (their sibling WBR2/WBR9
        # arrived since) alongside every normal gold_loader run. Folded
        # into the same "sip" result entry — a plain load with nothing new
        # AND nothing pending to reconcile still reports "skipped".
        # =====================================================

        try:

            reconcile_result = reconcile_pending_sip()

            if reconcile_result["status"] == "error":
                print("SIP reconciliation failed:", reconcile_result["error"])
                results["sip"] = reconcile_result

            elif reconcile_result["rows_loaded"]:
                print("SIP reconciliation resolved", reconcile_result["rows_loaded"], "row(s)")
                results["sip"] = load_result(
                    "ok",
                    results["sip"]["rows_loaded"] + reconcile_result["rows_loaded"],
                )

        except Exception as e:

            print("SIP reconciliation failed")
            print(e)
            results["sip"] = load_result("error", results["sip"]["rows_loaded"], str(e))

    else:

        print(
            "\nGold SIP module not available"
        )
        results["sip"] = load_result("skipped", 0)
```

Also add `reconcile_pending_sip` to the `from etl_gold_sip import (...)` block at the top of `gold_loader.py` (~line 44-48):

```python
try:

    from etl_gold_sip import (
        extract_sip,
        transform_sip,
        load_sip,
        reconcile_pending_sip
    )

    SIP_AVAILABLE = True

except ImportError:

    SIP_AVAILABLE = False
```

- [ ] **Step 6: Manually verify end-to-end against the dev DB**

Run: `cd python_scripts && source venv/bin/activate && python3 -m gold_loader`
Expected: console output includes both "Loading Gold SIP" and "RECONCILING PENDING SIP ENRICHMENT" sections, exits without exception.

- [ ] **Step 7: Run the full pytest suite**

Run: `cd python_scripts && source venv/bin/activate && pytest tests/ -v --ignore=tests/etl_pipeline/test_s3_client.py 2>&1 | tail -60`
Expected: all `PASS` (pre-existing unrelated failures, if any, should be identical to the baseline before this plan started — compare against the 342-collected/0-unexpected-failures baseline from earlier in this session).

- [ ] **Step 8: Commit**

```bash
git add python_scripts/etl_gold_sip.py python_scripts/gold_loader.py python_scripts/tests/test_gold_sip_enrichment_reconciliation.py
git commit -m "feat: reconcile gold.sip rows whose enrichment was pending on load"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `automated_pipeline_documentation.md`
- Modify: `python_scripts/etl_pipeline/README.md`

**Interfaces:**
- Consumes: nothing (docs-only task).
- Produces: nothing consumed by other tasks — do this last.

- [ ] **Step 1: Update `automated_pipeline_documentation.md`**

In the "Why the two-stage grouping?" section (§2) and the `runner.py` walkthrough (§3), add a note that grouping is now per-report-code, not per-RTA-trio — each file processes independently the moment it's reserved; `pipeline.etl_report_group_hold`'s `group_key` now includes the report code. Add a new subsection under §4 ("Idempotency") titled "SIP enrichment reconciliation" describing `enrichment_pending_since`, `extract_pending_sip_retry_candidates()`, and `reconcile_pending_sip()` — reuse the explanation already given in this conversation (bounded batch, natural-key rejoin to silver, 30-day cutoff, folded into every `gold_loader.load_gold()` run).

- [ ] **Step 2: Update `python_scripts/etl_pipeline/README.md`**

Add one paragraph near the top, after the "One-time setup" section:

```markdown
## File independence

`WBR2`/`WBR9`/`WBR49` (and the KFIN equivalents) no longer wait for each
other — each report code processes to bronze/gold the moment it's
individually reserved. `gold.holdings`/`gold.clients`/`gold.folio_nominees`
self-heal automatically as siblings arrive later (every `gold_loader` run
is a full recompute + upsert). `gold.sip` is the one table whose
enrichment (ARN, client_id, installment counts) can't recompute from a
plain re-run — see `enrichment_pending_since` in
`automated_pipeline_documentation.md` for how that's reconciled instead.
```

- [ ] **Step 3: Commit**

```bash
git add automated_pipeline_documentation.md python_scripts/etl_pipeline/README.md
git commit -m "docs: document file decoupling and SIP enrichment reconciliation"
```

---

### Task 6: Full regression pass and code review

**Files:** none (verification only).

**Interfaces:** Consumes everything from Tasks 1-5.

- [ ] **Step 1: Run the complete test suite**

Run: `cd python_scripts && source venv/bin/activate && pip install -r requirements.txt && pytest tests/ -v 2>&1 | tail -80`
Expected: all tests pass (including `test_s3_client.py`/`test_runner.py`, which needed `boto3` installed — confirmed earlier in this session that a fresh `pip install` fixes their collection).

- [ ] **Step 2: Manually run the pipeline once against the real tunneled backend**

Run: `python3 -m etl_pipeline.runner`, then inspect:
```sql
SELECT group_key, rta, report_code, status FROM pipeline.etl_report_group_hold
ORDER BY last_updated_at DESC LIMIT 20;
```
Wait — `report_code` isn't a column on `etl_report_group_hold`; use `group_key` (now contains the report_code as its second `|`-delimited segment) to confirm each group is single-file-scoped.
Expected: any group with a single member reaches `READY`/`COMPLETED` on its own, without waiting for `WBR2`/`WBR9`/`WBR49` siblings.

- [ ] **Step 3: Request code review**

Invoke `superpowers:requesting-code-review` on the full diff spanning Tasks 1-5 before considering this plan complete.
