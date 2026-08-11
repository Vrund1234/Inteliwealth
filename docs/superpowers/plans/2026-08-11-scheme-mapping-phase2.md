# Scheme Mapping Engine Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise automatic RTA→AMFI scheme mapping coverage from 223/515 (43.3%) to 76–80% without writing a single low-confidence mapping.

**Architecture:** Replace whole-string name equality with an attribute-aware `SchemeKey` tuple parsed identically from both RTA and AMFI names. Matching rules become pure functions in an ordered registry that all execute and are then arbitrated by confidence, so every rule's outcome is auditable. Anything that fails a rule's confidence guard writes nothing and emits its top-3 candidates to a human review table.

**Tech Stack:** Python 3.13, pandas 3.0.5, SQLAlchemy 2.0.51, rapidfuzz 3.14.5, PostgreSQL, pytest (added by Task 1).

## Global Constraints

- **Regression is a hard failure.** All 223 currently-matched `(rta, rta_scheme_code) → amfi_scheme_code` pairs must survive every task byte-identical. Task 1 builds the harness; every task after it re-runs the harness.
- **Never auto-write below threshold.** A rule that produces candidates but fails its guards writes nothing to `bronze.scheme_mapping` and emits top-3 to `public.scheme_mapping_review`.
- **Attributes are extracted before filler is deleted.** Plan/option/frequency/qualifier tokens become `SchemeKey` fields. Deleting them as noise merges a fund's Growth and IDCW variants into one key and is the single most dangerous failure mode in this design.
- **Qualifiers compare exactly, including empty-set equality.** `SEGREGATED`, `RETAIL`, `INSTITUTIONAL`.
- **Fuzzy matching applies to `core_name` only**, never to attributes, and only inside a bucket of identical `(amc_code, plan, option, frequency, qualifiers)`.
- **Python venv:** all commands run via `python_scripts/venv/bin/python` and `python_scripts/venv/bin/pytest`.
- **Working directory:** `/var/www/html/intelliwealth_layer_old_code`.
- **Databases:** project = `inteliwealth_db` (`engine`), master = `intelli_wealth_28_07_2026` (`master_engine`), both via `python_scripts/utils/db.py`.
- **Spec:** `docs/superpowers/specs/2026-08-11-scheme-mapping-phase2-design.md`. Read it before Task 1.

---

## File Structure

**New package** `python_scripts/scheme_matching/` — the engine, split by responsibility so each file stays holdable in context:

| File | Responsibility |
|---|---|
| `__init__.py` | Package marker |
| `scheme_key.py` | `SchemeKey` dataclass + `parse_scheme_key()`. Pure functions, no DB, no pandas. |
| `aliases.py` | Load `scheme_name_alias` and apply TOKEN / FUND_RENAME substitutions |
| `rules.py` | `Candidate` dataclass, the seven rule functions, `RULE_REGISTRY`, `arbitrate()` |
| `nav_verify.py` | NAV fingerprint lookup + verification, shared by `NAV_MATCH` and the Task 12 gate |
| `reference.py` | Loads `rta_amc_code`, `scheme_mapping_override`; writes audit and review tables |

**New tests** `python_scripts/tests/` — `test_scheme_key.py`, `test_aliases.py`, `test_rules.py`, `test_regression.py`.

**New SQL** `sql_scripts/scheme_mapping_phase2.sql` — all DDL for the phase, idempotent.

**Modified** `python_scripts/scheme_mapping.py` — becomes an orchestrator: load, parse, run registry, arbitrate, verify, write. Rule bodies move out to `rules.py`.

**Modified** `python_scripts/requirements.txt` — add `rapidfuzz` (imported but undeclared) and `pytest`.

Tasks 1–3 are foundation and must run in order. Tasks 4–7 build the parser bottom-up. Tasks 8–11 build the rules. Tasks 12–13 are the verification gate and cutover.

---

### Task 1: Regression harness and test infrastructure

Nothing else may start until the 223 existing mappings are locked down. This task also installs pytest, which every later task depends on.

**Files:**
- Create: `python_scripts/tests/__init__.py`
- Create: `python_scripts/tests/conftest.py`
- Create: `python_scripts/tests/test_regression.py`
- Create: `python_scripts/tests/baseline_mappings.csv` (generated, committed)
- Modify: `python_scripts/requirements.txt`

**Interfaces:**
- Consumes: nothing
- Produces: `tests/conftest.py::baseline_df` fixture returning a DataFrame with columns `rta`, `rta_scheme_code`, `amfi_scheme_code`; `tests/conftest.py::current_mappings()` returning the same shape read live from `bronze.scheme_mapping`

- [ ] **Step 1: Add test dependencies**

Edit `python_scripts/requirements.txt`, appending two lines (note the file currently has no trailing newline after `pyparsing`):

```
rapidfuzz
pytest
```

`rapidfuzz` is already imported by `scheme_mapping.py:5` and installed in the venv, but was never declared. Install pytest:

```bash
python_scripts/venv/bin/pip install pytest
```

- [ ] **Step 2: Generate the baseline snapshot**

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
import pandas as pd
from utils.db import engine
df = pd.read_sql('''
    SELECT rta, rta_scheme_code, amfi_scheme_code
    FROM bronze.scheme_mapping
    WHERE amfi_scheme_code IS NOT NULL
    ORDER BY rta, rta_scheme_code
''', engine)
df.to_csv('python_scripts/tests/baseline_mappings.csv', index=False)
print('baseline rows:', len(df))
"
```

Expected: `baseline rows: 223`. If the number differs, stop and report it — the plan's arithmetic is built on 223 and a different number means the database moved.

- [ ] **Step 3: Write conftest.py**

```python
import sys
from pathlib import Path

import pandas as pd
import pytest

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from utils.db import engine  # noqa: E402

BASELINE_CSV = Path(__file__).parent / "baseline_mappings.csv"


@pytest.fixture(scope="session")
def baseline_df():
    """The 223 mappings that existed before Phase 2. These must never change."""
    return pd.read_csv(BASELINE_CSV, dtype=str)


@pytest.fixture(scope="session")
def current_df():
    return current_mappings()


def current_mappings():
    """Live mappings from bronze.scheme_mapping, same shape as the baseline."""
    return pd.read_sql(
        """
        SELECT rta, rta_scheme_code, amfi_scheme_code
        FROM bronze.scheme_mapping
        WHERE amfi_scheme_code IS NOT NULL
        ORDER BY rta, rta_scheme_code
        """,
        engine,
        dtype=str,
    )
```

- [ ] **Step 4: Write the failing test**

`python_scripts/tests/test_regression.py`:

```python
def test_no_baseline_mapping_was_lost(baseline_df, current_df):
    """Every scheme matched before Phase 2 is still matched."""
    base_keys = set(zip(baseline_df.rta, baseline_df.rta_scheme_code))
    curr_keys = set(zip(current_df.rta, current_df.rta_scheme_code))
    lost = base_keys - curr_keys
    assert not lost, f"{len(lost)} mappings lost: {sorted(lost)[:10]}"


def test_no_baseline_mapping_changed_code(baseline_df, current_df):
    """Every scheme matched before Phase 2 kept the same AMFI code."""
    merged = baseline_df.merge(
        current_df,
        on=["rta", "rta_scheme_code"],
        suffixes=("_base", "_curr"),
    )
    changed = merged[merged.amfi_scheme_code_base != merged.amfi_scheme_code_curr]
    assert changed.empty, (
        f"{len(changed)} mappings changed AMFI code:\n"
        f"{changed.head(10).to_string()}"
    )


def test_baseline_is_the_expected_size(baseline_df):
    assert len(baseline_df) == 223
```

- [ ] **Step 5: Run the tests — they must PASS immediately**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_regression.py -v
```

Expected: 3 passed. This is the one task whose tests pass on first write — the harness describes the *current* state. If any fail now, the baseline CSV and the database disagree and something else is writing to the table.

- [ ] **Step 6: Commit**

```bash
git add python_scripts/tests/ python_scripts/requirements.txt
git commit -m "test: add regression harness locking the 223 existing scheme mappings"
```

---

### Task 2: Phase 2 DDL

All schema changes in one idempotent script, applied once. Later tasks assume these tables exist.

**Files:**
- Create: `sql_scripts/scheme_mapping_phase2.sql`

**Interfaces:**
- Consumes: nothing
- Produces: tables `public.scheme_name_alias`, `public.scheme_mapping_override`, `public.scheme_mapping_review`, `bronze.scheme_mapping_audit`; column `public.rta_amc_code.amfi_amc_code`; constraint `uq_scheme_mapping_amfi`

- [ ] **Step 1: Write the DDL**

`sql_scripts/scheme_mapping_phase2.sql`. Note the two databases — the `public.*` tables live in `intelli_wealth_28_07_2026` (master), the `bronze.*` objects in `inteliwealth_db` (project). The script is split into two clearly marked sections because they run against different connections.

```sql
-- ============================================================
-- SECTION A — run against MASTER db (intelli_wealth_28_07_2026)
-- ============================================================

-- Explicit RTA -> AMFI amc_code link. Today the two vocabularies happen to
-- agree for 27 of 29 codes; storing it makes a future divergence a data edit
-- rather than a code change. amc_slug is retained for display only.
ALTER TABLE public.rta_amc_code
    ADD COLUMN IF NOT EXISTS amfi_amc_code VARCHAR;

CREATE TABLE IF NOT EXISTS public.scheme_name_alias (
    alias_id        UUID PRIMARY KEY,
    raw_term        TEXT NOT NULL,
    normalized_term TEXT NOT NULL DEFAULT '',
    alias_type      VARCHAR NOT NULL CHECK (alias_type IN ('TOKEN', 'FUND_RENAME')),
    amc_code        VARCHAR,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_name_alias
    ON public.scheme_name_alias (alias_type, raw_term, COALESCE(amc_code, ''));

CREATE TABLE IF NOT EXISTS public.scheme_mapping_override (
    override_id      UUID PRIMARY KEY,
    rta              VARCHAR NOT NULL,
    rta_scheme_code  VARCHAR NOT NULL,
    -- NULL is meaningful: a curator asserting the fund is absent from AMFI.
    amfi_scheme_code VARCHAR,
    reason           TEXT NOT NULL,
    mapped_by        VARCHAR,
    mapped_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_scheme_mapping_override UNIQUE (rta, rta_scheme_code)
);

CREATE TABLE IF NOT EXISTS public.scheme_mapping_review (
    review_id           UUID PRIMARY KEY,
    rta                 VARCHAR NOT NULL,
    rta_scheme_code     VARCHAR NOT NULL,
    rta_scheme_name     TEXT,
    candidate_rank      INT NOT NULL,
    candidate_amfi_code VARCHAR,
    candidate_amfi_name TEXT,
    candidate_score     NUMERIC,
    rule_name           VARCHAR NOT NULL,
    reviewer_decision   VARCHAR CHECK (reviewer_decision IN ('APPROVED', 'REJECTED')),
    reviewed_by         VARCHAR,
    reviewed_at         TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_scheme_mapping_review UNIQUE (rta, rta_scheme_code, candidate_rank)
);

-- ============================================================
-- SECTION B — run against PROJECT db (inteliwealth_db)
-- ============================================================

CREATE TABLE IF NOT EXISTS bronze.scheme_mapping_audit (
    audit_id            UUID PRIMARY KEY,
    rta                 VARCHAR NOT NULL,
    rta_scheme_code     VARCHAR NOT NULL,
    rule_name           VARCHAR NOT NULL,
    execution_outcome   VARCHAR NOT NULL,
    confidence_score    INT,
    candidate_scheme_id VARCHAR,
    evaluated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_scheme_mapping_audit_scheme
    ON bronze.scheme_mapping_audit (rta, rta_scheme_code);

-- The duplicate-expansion INSERT at scheme_mapping.py:1394 declares
-- ON CONFLICT (rta, rta_scheme_code, amfi_scheme_code) but no such constraint
-- exists, so that branch raises as soon as it receives rows. It is currently
-- masked only because target_names is empty.
CREATE UNIQUE INDEX IF NOT EXISTS uq_scheme_mapping_amfi
    ON bronze.scheme_mapping (rta, rta_scheme_code, amfi_scheme_code);
```

- [ ] **Step 2: Apply Section A to the master database**

```bash
cd /var/www/html/intelliwealth_layer_old_code
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
from sqlalchemy import text
from utils.db import master_engine
sql = open('sql_scripts/scheme_mapping_phase2.sql').read()
section_a = sql.split('-- SECTION B')[0]
with master_engine.begin() as c:
    c.execute(text(section_a))
print('Section A applied')
"
```

Expected: `Section A applied`.

- [ ] **Step 3: Apply Section B to the project database**

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
from sqlalchemy import text
from utils.db import engine
sql = open('sql_scripts/scheme_mapping_phase2.sql').read()
section_b = sql.split('-- SECTION B')[1]
section_b = section_b.split('====\n', 1)[-1]
with engine.begin() as c:
    c.execute(text(section_b))
print('Section B applied')
"
```

Expected: `Section B applied`.

If `uq_scheme_mapping_amfi` fails with a duplicate key error, `bronze.scheme_mapping` already holds duplicate `(rta, rta_scheme_code, amfi_scheme_code)` triples. Report the duplicates rather than deleting rows.

- [ ] **Step 4: Verify all objects exist**

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
import pandas as pd
from utils.db import engine, master_engine
print(pd.read_sql(\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'scheme_%'\", master_engine).to_string())
print(pd.read_sql(\"SELECT column_name FROM information_schema.columns WHERE table_name='rta_amc_code' AND column_name='amfi_amc_code'\", master_engine).to_string())
print(pd.read_sql(\"SELECT indexname FROM pg_indexes WHERE schemaname='bronze'\", engine).to_string())
"
```

Expected: `scheme_name_alias`, `scheme_mapping_override`, `scheme_mapping_review` listed; `amfi_amc_code` present; `uq_scheme_mapping_amfi` and `ix_scheme_mapping_audit_scheme` listed.

- [ ] **Step 5: Commit**

```bash
git add sql_scripts/scheme_mapping_phase2.sql
git commit -m "feat: add Phase 2 scheme mapping tables and fix missing ON CONFLICT constraint"
```

---

### Task 3: Backfill the AMC code link

The one-line root cause. This alone unblocks Rules 3 and 4 for all 515 schemes.

**Files:**
- Create: `python_scripts/scheme_matching/__init__.py`
- Create: `python_scripts/scheme_matching/reference.py`
- Create: `python_scripts/tests/test_reference.py`

**Interfaces:**
- Consumes: `public.rta_amc_code.amfi_amc_code` (Task 2)
- Produces: `reference.load_amc_map(master_engine) -> pd.DataFrame` with columns `rta`, `rta_amc_code`, `amfi_amc_code`, `amc_slug`

- [ ] **Step 1: Backfill amfi_amc_code**

For 27 of 29 codes the RTA code and the AMFI code are the same string, so the backfill is a self-copy guarded by existence in `amfi_scheme_master`:

```bash
cd /var/www/html/intelliwealth_layer_old_code
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
from sqlalchemy import text
from utils.db import master_engine
with master_engine.begin() as c:
    r = c.execute(text('''
        UPDATE public.rta_amc_code t
        SET amfi_amc_code = t.amc_code
        WHERE EXISTS (
            SELECT 1 FROM public.amfi_scheme_master a
            WHERE a.amc_code = t.amc_code
        )
    '''))
    print('rows backfilled:', r.rowcount)
"
```

Expected: a non-zero count covering the codes present in AMFI. Codes with no AMFI counterpart keep `amfi_amc_code = NULL`, which is correct — they cannot match.

- [ ] **Step 2: Add the two missing KFIN AMCs**

KFIN `906` (Altiva) and `908` (Diviniti) are absent from `rta_amc_code` entirely. They are also absent from `amfi_scheme_master`, so `amfi_amc_code` stays NULL and their two schemes will terminate as `NOT_IN_AMFI` rather than `UNMATCHED`.

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
import uuid
from sqlalchemy import text
from utils.db import master_engine
rows = [
    ('KFIN', '906', 'ALTIVA MUTUAL FUND', 'ALTIVA'),
    ('KFIN', '908', 'DIVINITI MUTUAL FUND', 'DIVINITI'),
]
with master_engine.begin() as c:
    for rta, code, name, slug in rows:
        c.execute(text('''
            INSERT INTO public.rta_amc_code (id, rta, amc_code, amc_name, amc_slug, is_deleted)
            VALUES (:id, :rta, :code, :name, :slug, FALSE)
            ON CONFLICT DO NOTHING
        '''), {'id': str(uuid.uuid4()), 'rta': rta, 'code': code, 'name': name, 'slug': slug})
print('inserted')
"
```

- [ ] **Step 3: Write the failing test**

`python_scripts/tests/test_reference.py`:

```python
import pandas as pd

from scheme_matching.reference import load_amc_map
from utils.db import master_engine


def test_amc_map_covers_all_rta_codes_in_use():
    """Every AMC code appearing in the RTA data resolves to a row in the map."""
    amc_map = load_amc_map(master_engine)
    results = pd.read_csv(
        "scheme_mapping_analysis/scheme_mapping_results.csv", dtype=str
    )
    used = set(zip(results.rta, results.rta_amc_code))
    known = set(zip(amc_map.rta, amc_map.rta_amc_code))
    missing = used - known
    assert not missing, f"AMC codes in data but not in rta_amc_code: {missing}"


def test_amfi_amc_code_resolves_for_513_of_515_schemes():
    """The 2 exceptions are KFIN 906 (Altiva) and 908 (Diviniti), absent from AMFI."""
    amc_map = load_amc_map(master_engine)
    resolvable = amc_map[amc_map.amfi_amc_code.notna()]
    results = pd.read_csv(
        "scheme_mapping_analysis/scheme_mapping_results.csv", dtype=str
    )
    merged = results.merge(
        resolvable,
        on=["rta", "rta_amc_code"],
        how="left",
    )
    covered = merged.amfi_amc_code.notna().sum()
    assert covered == 513, f"expected 513 covered, got {covered}"


def test_amc_slug_is_not_a_valid_amfi_code():
    """Regression guard for the original bug: slugs and AMFI codes are disjoint."""
    amc_map = load_amc_map(master_engine)
    amfi_codes = set(
        pd.read_sql(
            "SELECT DISTINCT amc_code FROM public.amfi_scheme_master "
            "WHERE amc_code IS NOT NULL",
            master_engine,
        ).amc_code
    )
    slugs = set(amc_map.amc_slug.dropna())
    overlap = slugs & amfi_codes
    assert len(overlap) <= 1, (
        f"slugs unexpectedly overlap AMFI codes: {overlap}. "
        "Joining on amc_slug is still wrong regardless."
    )
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_reference.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scheme_matching'`.

- [ ] **Step 5: Write the implementation**

`python_scripts/scheme_matching/__init__.py` — empty file.

`python_scripts/scheme_matching/reference.py`:

```python
"""Reference-data loaders for the scheme matching engine."""

import pandas as pd


def load_amc_map(master_engine):
    """RTA AMC code -> AMFI AMC code.

    amfi_amc_code is NULL where the AMC has no schemes in amfi_scheme_master;
    those rows can never produce a match and are kept only for reporting.
    """
    return pd.read_sql(
        """
        SELECT
            rta,
            amc_code AS rta_amc_code,
            amfi_amc_code,
            amc_slug
        FROM public.rta_amc_code
        WHERE is_deleted IS NOT TRUE
        """,
        master_engine,
        dtype=str,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: 6 passed (3 regression + 3 reference).

- [ ] **Step 7: Commit**

```bash
git add python_scripts/scheme_matching/ python_scripts/tests/test_reference.py
git commit -m "fix: link rta_amc_code to amfi amc_code, replacing the broken amc_slug join"
```

---

### Task 4: SchemeKey attribute extraction

The parser's core. Pure functions, no database, no pandas — fast to test and the place where wrong mappings are prevented.

**Files:**
- Create: `python_scripts/scheme_matching/scheme_key.py`
- Create: `python_scripts/tests/test_scheme_key.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `scheme_key.SchemeKey` — frozen dataclass with fields `amc_code: str | None`, `core_name: str`, `plan: str`, `option: str`, `frequency: str | None`, `qualifiers: frozenset[str]`
  - `scheme_key.parse_scheme_key(name: str, amc_code: str | None = None, alias_fn=None) -> SchemeKey | None`
  - `scheme_key.strip_parentheticals(text: str) -> str`
  - `scheme_key.extract_attributes(text: str) -> tuple[str, dict]` returning residual text and `{"plan", "option", "frequency", "qualifiers"}`

- [ ] **Step 1: Write the failing tests**

`python_scripts/tests/test_scheme_key.py`:

```python
import pytest

from scheme_matching.scheme_key import (
    SchemeKey,
    extract_attributes,
    parse_scheme_key,
    strip_parentheticals,
)


class TestStripParentheticals:
    def test_removes_formerly_known_as(self):
        out = strip_parentheticals(
            "MIRAE ASSET LIQUID FUND ( FORMERLY MIRAE ASSET CASH MANAGEMENT FUND ) "
            "- REGULAR PLAN"
        )
        assert "CASH MANAGEMENT" not in out
        assert "MIRAE ASSET LIQUID FUND" in out

    def test_removes_erstwhile(self):
        out = strip_parentheticals(
            "FRANKLIN INDIA FLEXI CAP FUND - GROWTH (ERSTWHILE FRANKLIN INDIA EQUITY FUND)"
        )
        assert "EQUITY FUND" not in out
        assert "FLEXI CAP" in out

    def test_removes_elss_boilerplate(self):
        out = strip_parentheticals(
            "ADITYA BIRLA SUN LIFE TAX PLAN - (ELSS U/S 80C OF IT ACT) - GROWTH"
        )
        assert "80C" not in out
        assert "TAX PLAN" in out

    def test_removes_maturity_date(self):
        out = strip_parentheticals(
            "ADITYA BIRLA SUN LIFE FIXED TERM PLAN SERIES ED "
            "- (MATURITY DATE - 10-JUL-2014) - GROWTH"
        )
        assert "2014" not in out
        assert "SERIES ED" in out

    def test_keeps_unrelated_parentheticals_content(self):
        """A parenthetical that is not a known annotation keeps its words."""
        out = strip_parentheticals("KOTAK MULTI ASSET OMNI FOF GROWTH (REGULAR PLAN)")
        assert "REGULAR" in out


class TestExtractAttributes:
    def test_regular_plan_becomes_plan_attribute_not_deleted(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH")
        assert attrs["plan"] == "REGULAR"

    def test_direct_plan_detected(self):
        _, attrs = extract_attributes("CANARA ROBECO MID CAP FUND DIRECT PLAN GROWTH")
        assert attrs["plan"] == "DIRECT"

    def test_plan_defaults_to_regular(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND GROWTH")
        assert attrs["plan"] == "REGULAR"

    def test_idcw_detected(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN IDCW")
        assert attrs["option"] == "IDCW"

    def test_dividend_is_idcw(self):
        _, attrs = extract_attributes(
            "HDFC ARBITRAGE FUND QUARTERLY DIVIDEND REINVESTMENT OPTION"
        )
        assert attrs["option"] == "IDCW"

    def test_option_defaults_to_growth(self):
        """UTI Flexi Cap Fund - Regular Plan carries no option token."""
        _, attrs = extract_attributes("UTI FLEXI CAP FUND REGULAR PLAN")
        assert attrs["option"] == "GROWTH"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("HDFC LOW DURATION FUND WEEKLY IDCW", "WEEKLY"),
            ("DSP ULTRA SHORT FUND REGULAR PLAN IDCW DAILY", "DAILY"),
            ("ABSL REGULAR SAVINGS FUND REGULAR MONTHLY IDCW", "MONTHLY"),
            ("HDFC ARBITRAGE FUND RETAIL PLAN QUARTERLY DIVIDEND", "QUARTERLY"),
            ("MOTILAL OSWAL BALANCED ADVANTAGE FUND REGULAR ANNUAL IDCW", "ANNUAL"),
            ("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH", None),
        ],
    )
    def test_frequency(self, text, expected):
        _, attrs = extract_attributes(text)
        assert attrs["frequency"] == expected

    def test_half_yearly_normalizes_with_underscore(self):
        _, attrs = extract_attributes("SOME FUND HALF YEARLY IDCW")
        assert attrs["frequency"] == "HALF_YEARLY"

    def test_qualifiers_are_captured_not_discarded(self):
        _, attrs = extract_attributes(
            "HDFC ARBITRAGE FUND RETAIL PLAN QUARTERLY DIVIDEND"
        )
        assert "RETAIL" in attrs["qualifiers"]

    def test_segregated_captured(self):
        _, attrs = extract_attributes(
            "NIPPON INDIA CREDIT RISK FUND SEGREGATED PORTFOLIO 1 GROWTH"
        )
        assert "SEGREGATED" in attrs["qualifiers"]

    def test_no_qualifiers_is_empty_frozenset(self):
        _, attrs = extract_attributes("HDFC FLEXI CAP FUND REGULAR PLAN GROWTH")
        assert attrs["qualifiers"] == frozenset()


class TestParseSchemeKey:
    def test_rta_and_amfi_names_produce_the_same_key(self):
        """The whole point of the design: divergent names, identical keys."""
        rta = parse_scheme_key(
            "Canara Robeco Mid Cap Fund - Regular Growth", amc_code="101"
        )
        amfi = parse_scheme_key(
            "CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", amc_code="101"
        )
        assert rta == amfi

    def test_hdfc_hybrid_equity_growth_matches(self):
        rta = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
        )
        amfi = parse_scheme_key("HDFC HYBRID EQUITY FUND GROWTH PLAN", amc_code="H")
        assert rta == amfi

    def test_growth_and_idcw_variants_never_collide(self):
        """The most dangerous failure mode this design must prevent."""
        growth = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
        )
        idcw = parse_scheme_key(
            "HDFC Hybrid Equity Fund - Regular Plan - IDCW", amc_code="H"
        )
        assert growth != idcw

    def test_retail_and_non_retail_never_collide(self):
        plain = parse_scheme_key("HDFC Arbitrage Fund - Quarterly IDCW", amc_code="H")
        retail = parse_scheme_key(
            "HDFC Arbitrage Fund - Retail Plan - Quarterly IDCW", amc_code="H"
        )
        assert plain != retail

    def test_daily_and_weekly_idcw_never_collide(self):
        daily = parse_scheme_key(
            "Aditya Birla Sun Life Low Duration Fund - Daily IDCW", amc_code="B"
        )
        weekly = parse_scheme_key(
            "Aditya Birla Sun Life Low Duration Fund - Weekly IDCW", amc_code="B"
        )
        assert daily != weekly

    def test_ampersand_and_and_are_equivalent(self):
        a = parse_scheme_key(
            "Aditya Birla Sun Life Pharma & Healthcare Fund Regular Growth", amc_code="B"
        )
        b = parse_scheme_key(
            "ADITYA BIRLA SUN LIFE PHARMA AND HEALTHCARE FUND REGULAR GROWTH",
            amc_code="B",
        )
        assert a == b

    def test_returns_none_for_blank_name(self):
        assert parse_scheme_key("", amc_code="H") is None
        assert parse_scheme_key(None, amc_code="H") is None

    def test_key_is_hashable(self):
        """Keys are used as dict keys for candidate lookup."""
        k = parse_scheme_key("HDFC Flexi Cap Fund - Growth", amc_code="H")
        assert isinstance(hash(k), int)
        assert len({k, k}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_scheme_key.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'scheme_matching.scheme_key'`.

- [ ] **Step 3: Write the implementation**

`python_scripts/scheme_matching/scheme_key.py`:

```python
"""Parse RTA and AMFI scheme names into a comparable structured key.

The critical ordering rule: attributes are EXTRACTED into fields before
structural filler is DELETED. Deleting plan/option/frequency tokens as noise
would collapse a fund's Growth and IDCW variants onto one key and produce
confidently wrong mappings.
"""

import re
from dataclasses import dataclass

PLAN_DIRECT = "DIRECT"
PLAN_REGULAR = "REGULAR"
OPTION_GROWTH = "GROWTH"
OPTION_IDCW = "IDCW"

# Ordered longest-first so "HALF YEARLY" wins over "YEARLY".
_FREQUENCIES = [
    ("HALF\\s+YEARLY", "HALF_YEARLY"),
    ("HALFYEARLY", "HALF_YEARLY"),
    ("FORTNIGHTLY", "FORTNIGHTLY"),
    ("QUARTERLY", "QUARTERLY"),
    ("MONTHLY", "MONTHLY"),
    ("WEEKLY", "WEEKLY"),
    ("ANNUALLY", "ANNUAL"),
    ("ANNUAL", "ANNUAL"),
    ("YEARLY", "ANNUAL"),
    ("DAILY", "DAILY"),
]

_QUALIFIERS = ["SEGREGATED", "RETAIL", "INSTITUTIONAL"]

_IDCW_TOKENS = r"\b(IDCW|DIVIDEND|DIV)\b"

# Deleted only after attribute extraction.
_FILLER = {
    "FUND", "FUNDS", "SCHEME", "PLAN", "PLANS", "OPTION", "OPTIONS",
    "THE", "OF", "AN", "A", "MUTUAL",
    "REGULAR", "DIRECT", "GROWTH", "IDCW", "DIVIDEND", "DIV",
    "PAYOUT", "REINVESTMENT", "REINVEST", "REINVESTED",
    "SEGREGATED", "RETAIL", "INSTITUTIONAL",
    "DAILY", "WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY",
    "HALF", "YEARLY", "ANNUAL", "ANNUALLY",
    "DAYS", "DAY",
}

_PARENTHETICAL_NOISE = [
    r"\([^)]*FORMERLY[^)]*\)",
    r"\([^)]*ERSTWHILE[^)]*\)",
    r"\([^)]*ELSS[^)]*\)",
    r"\([^)]*MATURITY\s*DATE[^)]*\)",
    r"\([^)]*DISCONTINUED[^)]*\)",
]

_BARE_NOISE = [
    r"FORMERLY\s+KNOWN\s+AS.*$",
    r"FORMERLY\s+.*$",
    r"ERSTWHILE\s+.*$",
    r"MATURITY\s*DATE\s*[-–]?\s*\d{1,2}[-/][A-Z]{3}[-/]\d{2,4}",
    r"U\s*/?\s*S\s*80\s*C(\s+OF\s+IT\s+ACT)?",
    r"CLOSED\s+FOR\s+FV\s+CHANGE",
]


@dataclass(frozen=True)
class SchemeKey:
    amc_code: str | None
    core_name: str
    plan: str
    option: str
    frequency: str | None
    qualifiers: frozenset

    def bucket(self):
        """Everything except core_name. Fuzzy matching happens within a bucket."""
        return (self.amc_code, self.plan, self.option, self.frequency, self.qualifiers)


def strip_parentheticals(text):
    """Remove rename annotations, regulatory boilerplate and maturity dates."""
    out = text.upper()
    for pattern in _PARENTHETICAL_NOISE:
        out = re.sub(pattern, " ", out)
    for pattern in _BARE_NOISE:
        out = re.sub(pattern, " ", out)
    return re.sub(r"\s+", " ", out).strip()


def extract_attributes(text):
    """Pull plan/option/frequency/qualifiers out of `text`.

    Returns (text_unchanged, attrs). The text is returned as-is; filler removal
    happens later in parse_scheme_key so callers can inspect attributes without
    losing words.
    """
    upper = text.upper()

    plan = PLAN_DIRECT if re.search(r"\bDIRECT\b", upper) else PLAN_REGULAR
    option = OPTION_IDCW if re.search(_IDCW_TOKENS, upper) else OPTION_GROWTH

    frequency = None
    for pattern, value in _FREQUENCIES:
        if re.search(r"\b" + pattern + r"\b", upper):
            frequency = value
            break

    qualifiers = frozenset(
        q for q in _QUALIFIERS if re.search(r"\b" + q + r"\b", upper)
    )

    return text, {
        "plan": plan,
        "option": option,
        "frequency": frequency,
        "qualifiers": qualifiers,
    }


def _to_core_name(text):
    """Delete structural filler and punctuation, leaving the distinguishing words."""
    out = text.upper()
    out = out.replace("&", " AND ")
    out = re.sub(r"[^A-Z0-9\s]", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    words = [w for w in out.split() if w not in _FILLER]
    return " ".join(words)


def parse_scheme_key(name, amc_code=None, alias_fn=None):
    """Parse a raw RTA or AMFI scheme name into a SchemeKey.

    `alias_fn`, when supplied, is called as alias_fn(text, amc_code) between
    parenthetical stripping and attribute extraction. Task 5 supplies it.
    Returns None when the name is empty.
    """
    if name is None:
        return None

    text = str(name).strip()
    if not text:
        return None

    text = strip_parentheticals(text)

    if alias_fn is not None:
        text = alias_fn(text, amc_code)

    _, attrs = extract_attributes(text)
    core = _to_core_name(text)

    return SchemeKey(
        amc_code=amc_code,
        core_name=core,
        plan=attrs["plan"],
        option=attrs["option"],
        frequency=attrs["frequency"],
        qualifiers=attrs["qualifiers"],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_scheme_key.py -v
```

Expected: all pass. If `test_keeps_unrelated_parentheticals_content` fails, the parenthetical patterns are too greedy — they must match only the listed annotation keywords, not every `(...)`.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/scheme_key.py python_scripts/tests/test_scheme_key.py
git commit -m "feat: add SchemeKey structured name parser"
```

---

### Task 5: Alias table and substitution

Moves hardcoded token rules into configurable data, and adds AMC-scoped fund renames.

**Files:**
- Create: `python_scripts/scheme_matching/aliases.py`
- Create: `python_scripts/tests/test_aliases.py`
- Modify: `sql_scripts/scheme_mapping_phase2.sql` (append seed INSERTs)

**Interfaces:**
- Consumes: `scheme_key.parse_scheme_key`'s `alias_fn` hook (Task 4); `public.scheme_name_alias` (Task 2)
- Produces:
  - `aliases.load_aliases(master_engine) -> list[dict]` with keys `raw_term`, `normalized_term`, `alias_type`, `amc_code`
  - `aliases.build_alias_fn(alias_rows) -> callable(text, amc_code) -> str` — the `alias_fn` passed to `parse_scheme_key`

- [ ] **Step 1: Write the failing tests**

`python_scripts/tests/test_aliases.py`:

```python
from scheme_matching.aliases import build_alias_fn
from scheme_matching.scheme_key import parse_scheme_key

TOKEN_ROWS = [
    {"raw_term": "GR", "normalized_term": "GROWTH", "alias_type": "TOKEN", "amc_code": None},
    {"raw_term": "FTP", "normalized_term": "FIXED TERM PLAN", "alias_type": "TOKEN", "amc_code": None},
    {"raw_term": "MIP", "normalized_term": "MONTHLY INCOME PLAN", "alias_type": "TOKEN", "amc_code": None},
]

RENAME_ROWS = [
    {
        "raw_term": "LONG TERM EQUITY",
        "normalized_term": "ELSS TAX SAVER",
        "alias_type": "FUND_RENAME",
        "amc_code": "FTI",
    },
]


class TestTokenAliases:
    def test_gr_expands_to_growth(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "GROWTH" in fn("ABSL CREDIT RISK FUND GR REGULAR", "B")

    def test_ftp_expands(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "FIXED TERM PLAN" in fn("ABSL FTP RETAIL SERIES AF GROWTH", "B")

    def test_token_alias_only_matches_whole_words(self):
        """GR must not rewrite the GR inside GROWTH or GREEN."""
        fn = build_alias_fn(TOKEN_ROWS)
        assert fn("SOME GREEN ENERGY FUND", "B") == "SOME GREEN ENERGY FUND"

    def test_token_aliases_apply_to_every_amc(self):
        fn = build_alias_fn(TOKEN_ROWS)
        assert "FIXED TERM PLAN" in fn("DSP FTP SERIES 41", "D")


class TestFundRenameAliases:
    def test_rename_applies_within_its_amc(self):
        fn = build_alias_fn(RENAME_ROWS)
        assert "ELSS TAX SAVER" in fn("FRANKLIN INDIA LONG TERM EQUITY FUND", "FTI")

    def test_rename_does_not_leak_to_other_amcs(self):
        """An AMC-scoped rename must never rewrite another AMC's fund."""
        fn = build_alias_fn(RENAME_ROWS)
        out = fn("HDFC LONG TERM EQUITY FUND", "H")
        assert "LONG TERM EQUITY" in out
        assert "ELSS TAX SAVER" not in out


class TestAliasIntegrationWithParser:
    def test_gr_abbreviation_matches_spelled_out_growth(self):
        fn = build_alias_fn(TOKEN_ROWS)
        rta = parse_scheme_key(
            "Aditya Birla Sun Life Credit Risk Fund - Gr. REGULAR",
            amc_code="B",
            alias_fn=fn,
        )
        amfi = parse_scheme_key(
            "ADITYA BIRLA SUN LIFE CREDIT RISK FUND REGULAR PLAN GROWTH",
            amc_code="B",
            alias_fn=fn,
        )
        assert rta == amfi

    def test_no_aliases_is_a_no_op(self):
        fn = build_alias_fn([])
        assert fn("HDFC FLEXI CAP FUND", "H") == "HDFC FLEXI CAP FUND"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_aliases.py -v
```

Expected: `ModuleNotFoundError: No module named 'scheme_matching.aliases'`.

- [ ] **Step 3: Write the implementation**

`python_scripts/scheme_matching/aliases.py`:

```python
"""Configurable name substitutions, loaded from public.scheme_name_alias.

Two alias types, deliberately kept apart:

TOKEN       word-level, global. "GR" -> "GROWTH".
FUND_RENAME phrase-level, AMC-scoped. "LONG TERM EQUITY" -> "ELSS TAX SAVER"
            within FTI only. Applying a rename globally would corrupt other AMCs.

Plan, option and frequency terms are NOT aliases. They are parsed attributes
(see scheme_key.extract_attributes) and must never be added to this table.
"""

import re

import pandas as pd


def load_aliases(master_engine):
    df = pd.read_sql(
        """
        SELECT raw_term, normalized_term, alias_type, amc_code
        FROM public.scheme_name_alias
        WHERE is_active IS TRUE
        """,
        master_engine,
    )
    return df.to_dict(orient="records")


def build_alias_fn(alias_rows):
    """Compile alias rows into a callable(text, amc_code) -> text.

    Renames are applied before tokens so a rename's replacement text is itself
    token-normalized. Longer raw_terms are applied first within each group so a
    short alias cannot pre-empt a longer overlapping one.
    """
    tokens = []
    renames = []

    for row in alias_rows:
        raw = str(row["raw_term"]).upper().strip()
        norm = str(row.get("normalized_term") or "").upper().strip()
        amc = row.get("amc_code")
        amc = str(amc).strip() if amc is not None and not pd.isna(amc) else None
        if not raw:
            continue
        pattern = re.compile(r"\b" + re.escape(raw) + r"\b")
        entry = (pattern, norm, amc, len(raw))
        if row["alias_type"] == "FUND_RENAME":
            renames.append(entry)
        else:
            tokens.append(entry)

    renames.sort(key=lambda e: e[3], reverse=True)
    tokens.sort(key=lambda e: e[3], reverse=True)

    def apply(text, amc_code=None):
        out = str(text).upper()
        for pattern, norm, amc, _ in renames:
            if amc is not None and amc != amc_code:
                continue
            out = pattern.sub(norm, out)
        for pattern, norm, amc, _ in tokens:
            if amc is not None and amc != amc_code:
                continue
            out = pattern.sub(norm, out)
        return re.sub(r"\s+", " ", out).strip()

    return apply
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_aliases.py -v
```

Expected: all pass.

- [ ] **Step 5: Seed the alias table**

Append to `sql_scripts/scheme_mapping_phase2.sql` under Section A, then run it against `master_engine` the same way as Task 2 Step 2:

```sql
-- Seed TOKEN aliases. Abbreviations observed in the current 515 RTA names.
INSERT INTO public.scheme_name_alias (alias_id, raw_term, normalized_term, alias_type, amc_code)
VALUES
    (gen_random_uuid(), 'GR',   'GROWTH',              'TOKEN', NULL),
    (gen_random_uuid(), 'FTP',  'FIXED TERM PLAN',     'TOKEN', NULL),
    (gen_random_uuid(), 'FMP',  'FIXED MATURITY PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'MIP',  'MONTHLY INCOME PLAN', 'TOKEN', NULL),
    (gen_random_uuid(), 'REG',  'REGULAR',             'TOKEN', NULL),
    (gen_random_uuid(), 'DIV',  'IDCW',                'TOKEN', NULL),
    (gen_random_uuid(), 'FOF',  'FUND OF FUNDS',       'TOKEN', NULL)
ON CONFLICT DO NOTHING;
```

Verify:

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
import pandas as pd
from utils.db import master_engine
print(pd.read_sql('SELECT alias_type, count(*) FROM public.scheme_name_alias GROUP BY 1', master_engine).to_string())
"
```

Expected: `TOKEN 7`.

- [ ] **Step 6: Commit**

```bash
git add python_scripts/scheme_matching/aliases.py python_scripts/tests/test_aliases.py sql_scripts/scheme_mapping_phase2.sql
git commit -m "feat: add configurable scheme name aliases with AMC-scoped fund renames"
```

---

### Task 6: Candidate model and rule registry

The scaffolding all rules plug into. No matching logic yet — just the contract and arbitration.

**Files:**
- Create: `python_scripts/scheme_matching/rules.py`
- Create: `python_scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `scheme_key.SchemeKey` (Task 4)
- Produces:
  - `rules.Candidate` — frozen dataclass `amfi_scheme_code: str`, `score: float`, `rule_name: str`, `confidence: int`
  - `rules.MatchContext` — dataclass holding `amfi_by_key: dict[SchemeKey, list]`, `amfi_by_bucket: dict[tuple, list]`, `amfi_names: dict[str, str]`, `overrides: dict[tuple, str | None]`, `nav_lookup` (Task 10)
  - `rules.RULE_REGISTRY: list[callable]` — ordered
  - `rules.arbitrate(candidates: list[Candidate]) -> Candidate | None`
  - `rules.run_all(row: dict, context: MatchContext) -> list[Candidate]`

- [ ] **Step 1: Write the failing tests**

`python_scripts/tests/test_rules.py`:

```python
from scheme_matching.rules import Candidate, arbitrate


def c(code, conf, rule, score=100.0):
    return Candidate(amfi_scheme_code=code, score=score, rule_name=rule, confidence=conf)


class TestArbitrate:
    def test_returns_none_for_no_candidates(self):
        assert arbitrate([]) is None

    def test_returns_the_only_candidate(self):
        only = c("100669", 98, "STRUCT_EXACT")
        assert arbitrate([only]) is only

    def test_highest_confidence_wins(self):
        low = c("111111", 90, "CORE_FUZZY")
        high = c("100669", 98, "STRUCT_EXACT")
        assert arbitrate([low, high]).amfi_scheme_code == "100669"

    def test_override_beats_everything_at_equal_confidence(self):
        """OVERRIDE and PRODUCT_MATCH are both 100; registry order breaks the tie."""
        product = c("222222", 100, "PRODUCT_MATCH")
        override = c("333333", 100, "OVERRIDE")
        assert arbitrate([product, override]).rule_name == "OVERRIDE"

    def test_tie_at_same_confidence_and_rule_prefers_higher_score(self):
        a = c("444444", 90, "CORE_FUZZY", score=91.0)
        b = c("555555", 90, "CORE_FUZZY", score=95.0)
        assert arbitrate([a, b]).amfi_scheme_code == "555555"


class TestCandidate:
    def test_is_hashable_and_comparable(self):
        a = c("100669", 98, "STRUCT_EXACT")
        b = c("100669", 98, "STRUCT_EXACT")
        assert a == b
        assert len({a, b}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v
```

Expected: `ModuleNotFoundError: No module named 'scheme_matching.rules'`.

- [ ] **Step 3: Write the implementation**

`python_scripts/scheme_matching/rules.py`:

```python
"""Matching rules as pure functions over an ordered registry.

Every rule runs on every row, even after a confident match is found. Early exit
would save nothing at 515 rows and would make the audit table (which records
each rule's outcome) and the top-3 review candidates impossible to populate.
"""

from dataclasses import dataclass, field

# Registry order. Also the tie-breaker when two rules return equal confidence.
RULE_ORDER = [
    "OVERRIDE",
    "ISIN_MATCH",
    "PRODUCT_MATCH",
    "STRUCT_EXACT",
    "NAV_MATCH",
    "STRUCT_TIEBREAK",
    "CORE_FUZZY",
]

CONFIDENCE = {
    "OVERRIDE": 100,
    "ISIN_MATCH": 100,
    "PRODUCT_MATCH": 100,
    "STRUCT_EXACT": 98,
    "NAV_MATCH": 97,
    "STRUCT_TIEBREAK": 95,
    "CORE_FUZZY": 90,
}


@dataclass(frozen=True)
class Candidate:
    amfi_scheme_code: str
    score: float
    rule_name: str
    confidence: int


@dataclass
class MatchContext:
    """Everything the rules read. Built once per run, never mutated by a rule."""

    amfi_by_key: dict = field(default_factory=dict)
    amfi_by_bucket: dict = field(default_factory=dict)
    amfi_names: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    nav_lookup: object = None


def arbitrate(candidates):
    """Pick the winner: highest confidence, then registry order, then score."""
    if not candidates:
        return None

    def sort_key(cand):
        try:
            order = RULE_ORDER.index(cand.rule_name)
        except ValueError:
            order = len(RULE_ORDER)
        return (-cand.confidence, order, -cand.score)

    return sorted(candidates, key=sort_key)[0]


# Rules are appended to this list by Tasks 7-11.
RULE_REGISTRY = []


def run_all(row, context):
    """Execute every registered rule and return the union of their candidates."""
    found = []
    for rule in RULE_REGISTRY:
        found.extend(rule(row, context) or [])
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/rules.py python_scripts/tests/test_rules.py
git commit -m "feat: add candidate model and rule registry with confidence arbitration"
```

---

### Task 7: STRUCT_EXACT rule

The rule that recovers the measured 151 schemes.

**Files:**
- Modify: `python_scripts/scheme_matching/rules.py`
- Modify: `python_scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `MatchContext.amfi_by_key` keyed by `SchemeKey`, valued as a list of AMFI scheme codes
- Produces: `rules.rule_struct_exact(row, context) -> list[Candidate]`; `row` must carry key `scheme_key`

- [ ] **Step 1: Write the failing tests**

Append to `python_scripts/tests/test_rules.py`:

```python
from scheme_matching.rules import MatchContext, rule_struct_exact
from scheme_matching.scheme_key import parse_scheme_key


def ctx_with(pairs):
    """pairs: list of (amfi_name, amc_code, amfi_code)."""
    by_key = {}
    names = {}
    for amfi_name, amc, amfi_code in pairs:
        k = parse_scheme_key(amfi_name, amc_code=amc)
        by_key.setdefault(k, []).append(amfi_code)
        names[amfi_code] = amfi_name
    return MatchContext(amfi_by_key=by_key, amfi_names=names)


class TestStructExact:
    def test_matches_when_exactly_one_amfi_row_shares_the_key(self):
        context = ctx_with(
            [("CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", "101", "150816")]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "Canara Robeco Mid Cap Fund - Regular Growth", amc_code="101"
            )
        }
        out = rule_struct_exact(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "150816"
        assert out[0].confidence == 98
        assert out[0].rule_name == "STRUCT_EXACT"

    def test_returns_nothing_when_no_amfi_row_shares_the_key(self):
        context = ctx_with(
            [("CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION", "101", "150816")]
        )
        row = {"scheme_key": parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")}
        assert rule_struct_exact(row, context) == []

    def test_returns_all_candidates_when_key_is_ambiguous(self):
        """Two AMFI rows on one key: emit both, let the tiebreak rule decide."""
        context = ctx_with(
            [
                ("AXIS TREASURY ADVANTAGE FUND REGULAR PLAN GROWTH", "128", "111111"),
                ("AXIS TREASURY ADVANTAGE FUND REGULAR GROWTH OPTION", "128", "222222"),
            ]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "Axis Treasury Advantage Fund - Regular Growth", amc_code="128"
            )
        }
        out = rule_struct_exact(row, context)
        assert len(out) == 2
        assert {c.amfi_scheme_code for c in out} == {"111111", "222222"}

    def test_growth_row_never_matches_an_idcw_key(self):
        context = ctx_with(
            [("HDFC HYBRID EQUITY FUND IDCW PLAN", "H", "102947")]
        )
        row = {
            "scheme_key": parse_scheme_key(
                "HDFC Hybrid Equity Fund - Regular Plan - Growth", amc_code="H"
            )
        }
        assert rule_struct_exact(row, context) == []

    def test_returns_nothing_when_row_has_no_key(self):
        assert rule_struct_exact({"scheme_key": None}, MatchContext()) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v -k StructExact
```

Expected: `ImportError: cannot import name 'rule_struct_exact'`.

- [ ] **Step 3: Write the implementation**

Append to `python_scripts/scheme_matching/rules.py`:

```python
def rule_struct_exact(row, context):
    """Exact SchemeKey equality against the AMFI master.

    Emits every code sharing the key. A single candidate is a confident match;
    two or three are handed to rule_struct_tiebreak, which resolves them or
    routes them to review. Nothing is written from here on ambiguity.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    codes = context.amfi_by_key.get(key, [])
    return [
        Candidate(
            amfi_scheme_code=str(code),
            score=100.0,
            rule_name="STRUCT_EXACT",
            confidence=CONFIDENCE["STRUCT_EXACT"],
        )
        for code in codes
    ]


RULE_REGISTRY.append(rule_struct_exact)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/rules.py python_scripts/tests/test_rules.py
git commit -m "feat: add STRUCT_EXACT rule matching on the full structured key"
```

---

### Task 8: OVERRIDE rule and NOT_IN_AMFI

Curated mappings beat every algorithm, and absent funds get an honest terminal state.

**Files:**
- Modify: `python_scripts/scheme_matching/rules.py`
- Modify: `python_scripts/scheme_matching/reference.py`
- Modify: `python_scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `public.scheme_mapping_override` (Task 2)
- Produces:
  - `reference.load_overrides(master_engine) -> dict[(rta, rta_scheme_code), str | None]`
  - `rules.rule_override(row, context) -> list[Candidate]`
  - `rules.NOT_IN_AMFI` sentinel — an override present with a NULL code

- [ ] **Step 1: Write the failing tests**

Append to `python_scripts/tests/test_rules.py`:

```python
from scheme_matching.rules import NOT_IN_AMFI, rule_override


class TestOverride:
    def test_override_produces_a_candidate_at_confidence_100(self):
        context = MatchContext(overrides={("CAMS", "B02G"): "107745"})
        row = {"rta": "CAMS", "rta_scheme_code": "B02G"}
        out = rule_override(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "107745"
        assert out[0].confidence == 100
        assert out[0].rule_name == "OVERRIDE"

    def test_null_override_signals_not_in_amfi(self):
        """A curator asserting the fund does not exist in AMFI."""
        context = MatchContext(overrides={("KFIN", "906HLRG"): None})
        row = {"rta": "KFIN", "rta_scheme_code": "906HLRG"}
        out = rule_override(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code is NOT_IN_AMFI

    def test_no_override_returns_nothing(self):
        context = MatchContext(overrides={})
        assert rule_override({"rta": "CAMS", "rta_scheme_code": "B02G"}, context) == []

    def test_override_is_keyed_by_rta_and_code_together(self):
        """rta_scheme_code alone is not unique across RTAs."""
        context = MatchContext(overrides={("CAMS", "X1"): "111111"})
        assert rule_override({"rta": "KFIN", "rta_scheme_code": "X1"}, context) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v -k Override
```

Expected: `ImportError: cannot import name 'NOT_IN_AMFI'`.

- [ ] **Step 3: Write the implementation**

Append to `python_scripts/scheme_matching/rules.py`:

```python
class _NotInAmfi:
    """Sentinel: an override asserts this scheme has no AMFI counterpart."""

    def __repr__(self):
        return "NOT_IN_AMFI"

    def __bool__(self):
        return False


NOT_IN_AMFI = _NotInAmfi()


def rule_override(row, context):
    """Hand-curated mapping. Wins over every algorithmic rule.

    A key present with a NULL value is a positive assertion of absence, not a
    missing entry — it yields NOT_IN_AMFI so the scheme stops being re-examined.
    """
    key = (row.get("rta"), row.get("rta_scheme_code"))
    if key not in context.overrides:
        return []

    code = context.overrides[key]
    return [
        Candidate(
            amfi_scheme_code=NOT_IN_AMFI if code is None else str(code),
            score=100.0,
            rule_name="OVERRIDE",
            confidence=CONFIDENCE["OVERRIDE"],
        )
    ]


RULE_REGISTRY.insert(0, rule_override)
```

Append to `python_scripts/scheme_matching/reference.py`:

```python
def load_overrides(master_engine):
    """(rta, rta_scheme_code) -> amfi_scheme_code, where None means NOT_IN_AMFI."""
    df = pd.read_sql(
        """
        SELECT rta, rta_scheme_code, amfi_scheme_code
        FROM public.scheme_mapping_override
        WHERE is_active IS TRUE
        """,
        master_engine,
    )
    return {
        (r.rta, r.rta_scheme_code): (
            None if pd.isna(r.amfi_scheme_code) else str(r.amfi_scheme_code)
        )
        for r in df.itertuples()
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Seed the two known NOT_IN_AMFI schemes**

KFIN `906HLRG` (Altiva) and `908S1GP` (Diviniti) belong to AMCs with no rows in `amfi_scheme_master`:

```bash
python_scripts/venv/bin/python -c "
import sys; sys.path.insert(0,'python_scripts')
import uuid
from sqlalchemy import text
from utils.db import master_engine
rows = [
    ('KFIN', '906HLRG', 'Altiva AMC has no schemes in amfi_scheme_master'),
    ('KFIN', '908S1GP', 'Diviniti AMC has no schemes in amfi_scheme_master'),
]
with master_engine.begin() as c:
    for rta, code, reason in rows:
        c.execute(text('''
            INSERT INTO public.scheme_mapping_override
                (override_id, rta, rta_scheme_code, amfi_scheme_code, reason, mapped_by)
            VALUES (:id, :rta, :code, NULL, :reason, 'phase2-plan')
            ON CONFLICT (rta, rta_scheme_code) DO NOTHING
        '''), {'id': str(uuid.uuid4()), 'rta': rta, 'code': code, 'reason': reason})
print('seeded')
"
```

- [ ] **Step 6: Commit**

```bash
git add python_scripts/scheme_matching/ python_scripts/tests/test_rules.py
git commit -m "feat: add OVERRIDE rule and NOT_IN_AMFI terminal state"
```

---

### Task 9: CORE_FUZZY rule with margin guard

The only inexact rule. Its three guards are what keep confidence at 95%+.

**Files:**
- Modify: `python_scripts/scheme_matching/rules.py`
- Modify: `python_scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `MatchContext.amfi_by_bucket` — `SchemeKey.bucket() -> list[(core_name, amfi_code)]`
- Produces: `rules.rule_core_fuzzy(row, context) -> list[Candidate]`; module constants `FUZZY_CUTOFF = 88`, `FUZZY_MARGIN = 5`

- [ ] **Step 1: Write the failing tests**

Append to `python_scripts/tests/test_rules.py`:

```python
from scheme_matching.rules import FUZZY_CUTOFF, FUZZY_MARGIN, rule_core_fuzzy


def bucket_ctx(key, entries):
    return MatchContext(amfi_by_bucket={key.bucket(): entries})


class TestCoreFuzzy:
    def test_matches_a_close_core_name(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(key, [("HDFC LARGECAP", "102001")])
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "102001"
        assert out[0].confidence == 90

    def test_rejects_below_the_cutoff(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(key, [("TOTALLY UNRELATED BOND", "999999")])
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_rejects_a_near_tie_even_when_both_clear_the_cutoff(self):
        """The margin guard. A near-tie means the name does not distinguish them."""
        key = parse_scheme_key("Axis Treasury Advantage Fund - Regular Growth", "128")
        context = bucket_ctx(
            key,
            [
                ("AXIS TREASURY ADVANTAGE", "111111"),
                ("AXIS TREASURY ADVANTAG", "222222"),
            ],
        )
        assert rule_core_fuzzy({"scheme_key": key}, context) == []

    def test_accepts_when_the_margin_is_wide_enough(self):
        key = parse_scheme_key("HDFC Large Cap Fund - Regular Plan - Growth", "H")
        context = bucket_ctx(
            key,
            [
                ("HDFC LARGECAP", "111111"),
                ("HDFC SHORT TERM DEBT SOMETHING ELSE", "222222"),
            ],
        )
        out = rule_core_fuzzy({"scheme_key": key}, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "111111"

    def test_never_crosses_bucket_boundaries(self):
        """A Growth row must not fuzzy-match an IDCW candidate, however similar."""
        growth = parse_scheme_key("HDFC Flexi Cap Fund - Regular Plan - Growth", "H")
        idcw = parse_scheme_key("HDFC Flexi Cap Fund - Regular Plan - IDCW", "H")
        context = bucket_ctx(idcw, [("HDFC FLEXI CAP", "101763")])
        assert rule_core_fuzzy({"scheme_key": growth}, context) == []

    def test_returns_nothing_for_an_empty_bucket(self):
        key = parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")
        assert rule_core_fuzzy({"scheme_key": key}, MatchContext()) == []

    def test_guard_constants_match_the_spec(self):
        assert FUZZY_CUTOFF == 88
        assert FUZZY_MARGIN == 5
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v -k CoreFuzzy
```

Expected: `ImportError: cannot import name 'FUZZY_CUTOFF'`.

- [ ] **Step 3: Write the implementation**

Append to `python_scripts/scheme_matching/rules.py`:

```python
from rapidfuzz import fuzz

# Guards for the only inexact rule.
FUZZY_CUTOFF = 88
FUZZY_MARGIN = 5


def rule_core_fuzzy(row, context):
    """Fuzzy match on core_name only, inside an identical-attribute bucket.

    Three guards, all required:
      1. Candidates share the row's exact bucket, so plan/option/frequency/
         qualifiers are never fuzzy-matched.
      2. token_sort_ratio >= FUZZY_CUTOFF. token_sort_ratio rather than ratio
         because word order differs between RTA and AMFI.
      3. The top score beats the runner-up by >= FUZZY_MARGIN. A near-tie means
         the name does not distinguish the funds, so returning nothing and
         routing to review is better than guessing.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    entries = context.amfi_by_bucket.get(key.bucket(), [])
    if not entries:
        return []

    scored = sorted(
        (
            (fuzz.token_sort_ratio(key.core_name, core), code)
            for core, code in entries
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    top_score, top_code = scored[0]
    if top_score < FUZZY_CUTOFF:
        return []

    if len(scored) > 1 and top_score - scored[1][0] < FUZZY_MARGIN:
        return []

    return [
        Candidate(
            amfi_scheme_code=str(top_code),
            score=float(top_score),
            rule_name="CORE_FUZZY",
            confidence=CONFIDENCE["CORE_FUZZY"],
        )
    ]


RULE_REGISTRY.append(rule_core_fuzzy)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/rules.py python_scripts/tests/test_rules.py
git commit -m "feat: add CORE_FUZZY rule with bucket, cutoff and margin guards"
```

---

### Task 10: NAV verification module

Extracts the NAV logic from `scheme_mapping.py` so both the `NAV_MATCH` rule and the Task 12 verification gate share one implementation.

**Files:**
- Create: `python_scripts/scheme_matching/nav_verify.py`
- Create: `python_scripts/tests/test_nav_verify.py`

**Interfaces:**
- Consumes: `gold.scheme_nav`, `gold.scheme` (project db); `public.nav_master` (master db)
- Produces:
  - `nav_verify.load_rta_fingerprints(engine, master_engine, rta_codes) -> dict[(rta, code), list[(nav_date, nav_round)]]`
  - `nav_verify.load_amfi_navs(master_engine, dates) -> pd.DataFrame` with `scheme_code`, `nav_date`, `nav_round`
  - `nav_verify.verify(fingerprint, amfi_code, amfi_navs) -> bool`
  - `nav_verify.RTAS_WITH_NAV = {"CAMS"}`

- [ ] **Step 1: Write the failing tests**

`python_scripts/tests/test_nav_verify.py`:

```python
import pandas as pd

from scheme_matching.nav_verify import RTAS_WITH_NAV, verify

AMFI_NAVS = pd.DataFrame(
    [
        {"scheme_code": "100669", "nav_date": "2026-07-09", "nav_round": 100.1234},
        {"scheme_code": "100669", "nav_date": "2026-07-08", "nav_round": 99.5000},
        {"scheme_code": "100669", "nav_date": "2026-07-07", "nav_round": 99.1111},
        {"scheme_code": "999999", "nav_date": "2026-07-09", "nav_round": 55.0000},
    ]
)


class TestVerify:
    def test_passes_when_every_nav_agrees(self):
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 99.5000),
            ("2026-07-07", 99.1111),
        ]
        assert verify(fingerprint, "100669", AMFI_NAVS) is True

    def test_fails_when_one_nav_disagrees(self):
        fingerprint = [
            ("2026-07-09", 100.1234),
            ("2026-07-08", 88.8888),
            ("2026-07-07", 99.1111),
        ]
        assert verify(fingerprint, "100669", AMFI_NAVS) is False

    def test_fails_when_the_amfi_code_has_no_navs(self):
        fingerprint = [("2026-07-09", 100.1234)]
        assert verify(fingerprint, "123456", AMFI_NAVS) is False

    def test_fails_on_an_empty_fingerprint(self):
        """No evidence is not the same as verified."""
        assert verify([], "100669", AMFI_NAVS) is False

    def test_fails_when_a_date_is_missing_from_amfi(self):
        fingerprint = [("2026-01-01", 100.1234)]
        assert verify(fingerprint, "100669", AMFI_NAVS) is False


class TestCoverage:
    def test_only_cams_has_nav_data(self):
        """gold.scheme_nav holds 68,424 rows across 332 codes, all CAMS.

        KFIN has zero. Any verification path must not assume KFIN NAVs exist.
        """
        assert RTAS_WITH_NAV == {"CAMS"}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_nav_verify.py -v
```

Expected: `ModuleNotFoundError: No module named 'scheme_matching.nav_verify'`.

- [ ] **Step 3: Write the implementation**

`python_scripts/scheme_matching/nav_verify.py`:

```python
"""NAV fingerprint matching and verification.

gold.scheme_nav currently holds NAV history for CAMS only — 68,424 rows across
332 scheme codes, zero for KFIN. Every KFIN scheme is therefore unverifiable by
NAV and must rely on name signals plus human review.
"""

import pandas as pd
from sqlalchemy import text

RTAS_WITH_NAV = {"CAMS"}

FINGERPRINT_SIZE = 3
NAV_PRECISION = 4


def load_rta_fingerprints(engine, master_engine, rta_codes):
    """Most recent NAVs per RTA scheme, restricted to dates AMFI also publishes.

    Returns {(rta, scheme_code): [(nav_date, nav_round), ...]}. Schemes with
    fewer than FINGERPRINT_SIZE usable dates are omitted — a partial fingerprint
    is not strong enough to identify a fund.
    """
    codes = [c for c in rta_codes if c]
    if not codes:
        return {}

    rta_nav = pd.read_sql(
        text(
            """
            SELECT s.rta, s.scheme_code AS rta_scheme_code, sn.nav_date, sn.nav
            FROM gold.scheme_nav sn
            JOIN gold.scheme s ON sn.scheme_id = s.id
            WHERE sn.nav_date IS NOT NULL
              AND sn.nav IS NOT NULL
              AND s.scheme_code = ANY(:codes)
            """
        ),
        engine,
        params={"codes": codes},
    )
    if rta_nav.empty:
        return {}

    amfi_dates = set(
        pd.read_sql("SELECT DISTINCT nav_date FROM public.nav_master", master_engine)[
            "nav_date"
        ]
    )
    rta_nav = rta_nav[rta_nav.nav_date.isin(amfi_dates)]
    if rta_nav.empty:
        return {}

    rta_nav = (
        rta_nav.sort_values(["rta", "rta_scheme_code", "nav_date", "nav"])
        .drop_duplicates(subset=["rta", "rta_scheme_code", "nav_date"], keep="last")
        .sort_values("nav_date", ascending=False)
    )
    rta_nav["nav_round"] = rta_nav["nav"].round(NAV_PRECISION)

    top = rta_nav.groupby(["rta", "rta_scheme_code"]).head(FINGERPRINT_SIZE)

    out = {}
    for (rta, code), grp in top.groupby(["rta", "rta_scheme_code"]):
        if len(grp) < FINGERPRINT_SIZE:
            continue
        out[(rta, code)] = list(zip(grp.nav_date, grp.nav_round))
    return out


def load_amfi_navs(master_engine, dates):
    """AMFI NAVs for the given dates, rounded to NAV_PRECISION."""
    date_list = [str(d) for d in set(dates)]
    if not date_list:
        return pd.DataFrame(columns=["scheme_code", "nav_date", "nav_round"])

    return pd.read_sql(
        text(
            f"""
            SELECT scheme_code, nav_date, ROUND(nav, {NAV_PRECISION}) AS nav_round
            FROM public.nav_master
            WHERE nav_date = ANY(:dates)
            """
        ),
        master_engine,
        params={"dates": date_list},
    )


def verify(fingerprint, amfi_code, amfi_navs):
    """True only when every (date, nav) in the fingerprint agrees with AMFI.

    An empty fingerprint returns False: absence of evidence is not verification.
    """
    if not fingerprint:
        return False

    subset = amfi_navs[amfi_navs.scheme_code.astype(str) == str(amfi_code)]
    if subset.empty:
        return False

    by_date = dict(zip(subset.nav_date.astype(str), subset.nav_round))

    for nav_date, nav_round in fingerprint:
        published = by_date.get(str(nav_date))
        if published is None:
            return False
        if round(float(published), NAV_PRECISION) != round(
            float(nav_round), NAV_PRECISION
        ):
            return False

    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_nav_verify.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/nav_verify.py python_scripts/tests/test_nav_verify.py
git commit -m "feat: extract NAV fingerprint matching and verification module"
```

---

### Task 11: STRUCT_TIEBREAK rule

Resolves the 21 schemes whose structured key matches 2–3 AMFI rows.

**Files:**
- Modify: `python_scripts/scheme_matching/rules.py`
- Modify: `python_scripts/tests/test_rules.py`

**Interfaces:**
- Consumes: `rule_struct_exact` candidates; `MatchContext.nav_lookup`
- Produces: `rules.rule_struct_tiebreak(row, context) -> list[Candidate]`; `rules.option_from_prodcode(code: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `python_scripts/tests/test_rules.py`:

```python
from scheme_matching.rules import option_from_prodcode, rule_struct_tiebreak


class TestOptionFromProdcode:
    """KFIN encodes the option in the product code suffix."""

    def test_growth_suffixes(self):
        assert option_from_prodcode("117IORG") == "GROWTH"
        assert option_from_prodcode("120EFGP") == "GROWTH"
        assert option_from_prodcode("108EQGP") == "GROWTH"

    def test_idcw_suffixes(self):
        assert option_from_prodcode("117IORD") == "IDCW"
        assert option_from_prodcode("120EFDP") == "IDCW"
        assert option_from_prodcode("120COID") == "IDCW"

    def test_unknown_suffix_returns_none(self):
        assert option_from_prodcode("B02X") is None

    def test_handles_none_and_empty(self):
        assert option_from_prodcode(None) is None
        assert option_from_prodcode("") is None


class TestStructTiebreak:
    def test_resolves_ambiguity_when_prodcode_agrees_with_one_candidate(self):
        key = parse_scheme_key("UTI Flexi Cap Fund - Regular Plan", "108")
        idcw_key = parse_scheme_key("UTI FLEXI CAP FUND REGULAR PLAN IDCW", "108")
        context = MatchContext(
            amfi_by_key={key: ["100669"], idcw_key: ["100668"]},
            amfi_names={
                "100669": "UTI FLEXI CAP FUND GROWTH OPTION",
                "100668": "UTI FLEXI CAP FUND REGULAR PLAN IDCW",
            },
        )
        row = {
            "rta": "KFIN",
            "rta_scheme_code": "108EQGP",
            "scheme_key": key,
        }
        out = rule_struct_tiebreak(row, context)
        assert len(out) == 1
        assert out[0].amfi_scheme_code == "100669"
        assert out[0].confidence == 95

    def test_returns_nothing_when_the_key_is_unambiguous(self):
        """Unambiguous keys are STRUCT_EXACT's job, not the tiebreaker's."""
        key = parse_scheme_key("HDFC Flexi Cap Fund - Growth", "H")
        context = MatchContext(amfi_by_key={key: ["101763"]})
        row = {"rta": "CAMS", "rta_scheme_code": "H01", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []

    def test_returns_nothing_when_prodcode_gives_no_signal(self):
        """No signal means route to review, not guess."""
        key = parse_scheme_key("Axis Treasury Advantage Fund - Regular Growth", "128")
        context = MatchContext(amfi_by_key={key: ["111111", "222222"]})
        row = {"rta": "KFIN", "rta_scheme_code": "128XXXX", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []

    def test_returns_nothing_when_prodcode_contradicts_the_name(self):
        """Name says IDCW, code says Growth. Disagreement is never resolved silently."""
        key = parse_scheme_key("Mirae Asset Large Cap Fund - Regular Plan IDCW", "117")
        context = MatchContext(amfi_by_key={key: ["107579", "118826"]})
        row = {"rta": "KFIN", "rta_scheme_code": "117IORG", "scheme_key": key}
        assert rule_struct_tiebreak(row, context) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_rules.py -v -k Tiebreak
```

Expected: `ImportError: cannot import name 'option_from_prodcode'`.

- [ ] **Step 3: Write the implementation**

Append to `python_scripts/scheme_matching/rules.py`:

```python
# KFIN product code suffixes carrying the option. CAMS codes have no comparable
# convention, so CAMS rows return None here and fall back to NAV verification.
_PRODCODE_GROWTH_SUFFIXES = ("GP", "GR", "SG", "G")
_PRODCODE_IDCW_SUFFIXES = ("DP", "ID", "RD", "RW", "MR", "DD", "D")


def option_from_prodcode(code):
    """Read the option from a KFIN product code suffix, or None if unreadable."""
    if not code:
        return None

    upper = str(code).upper()
    for suffix in _PRODCODE_IDCW_SUFFIXES:
        if upper.endswith(suffix):
            return "IDCW"
    for suffix in _PRODCODE_GROWTH_SUFFIXES:
        if upper.endswith(suffix):
            return "GROWTH"
    return None


def rule_struct_tiebreak(row, context):
    """Resolve a key that matched 2-3 AMFI rows, using the product code suffix.

    Fires only on genuine ambiguity. Requires the code signal and the name
    signal to agree; a contradiction or a missing signal returns nothing, which
    routes the scheme to review rather than guessing between real funds.
    """
    key = row.get("scheme_key")
    if key is None:
        return []

    codes = context.amfi_by_key.get(key, [])
    if len(codes) < 2:
        return []

    code_option = option_from_prodcode(row.get("rta_scheme_code"))
    if code_option is None or code_option != key.option:
        return []

    matching = [
        code
        for code in codes
        if context.amfi_names.get(str(code))
        and parse_option_of(context.amfi_names[str(code)]) == code_option
    ]

    if len(matching) != 1:
        return []

    return [
        Candidate(
            amfi_scheme_code=str(matching[0]),
            score=100.0,
            rule_name="STRUCT_TIEBREAK",
            confidence=CONFIDENCE["STRUCT_TIEBREAK"],
        )
    ]


def parse_option_of(amfi_name):
    """Option carried by an AMFI name, via the shared attribute extractor."""
    from scheme_matching.scheme_key import extract_attributes

    _, attrs = extract_attributes(amfi_name)
    return attrs["option"]


RULE_REGISTRY.append(rule_struct_tiebreak)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_matching/rules.py python_scripts/tests/test_rules.py
git commit -m "feat: add STRUCT_TIEBREAK rule using KFIN product code suffixes"
```

---

### Task 12: Wire the orchestrator

Rewrites `scheme_mapping.py` to use the engine. This is where coverage actually moves.

**Files:**
- Modify: `python_scripts/scheme_mapping.py`
- Modify: `python_scripts/scheme_matching/reference.py`

**Interfaces:**
- Consumes: every module from Tasks 3–11
- Produces:
  - `scheme_mapping.build_context(df, amfi_df, ...) -> MatchContext`
  - `reference.write_audit(engine, audit_rows)`, `reference.write_review(master_engine, review_rows)`
  - `bronze.scheme_mapping.mapping_status` populated with `MATCHED` / `PENDING_REVIEW` / `NOT_IN_AMFI` / `UNMATCHED`

- [ ] **Step 1: Add the audit and review writers**

Append to `python_scripts/scheme_matching/reference.py`:

```python
import uuid

from sqlalchemy import text


def write_audit(engine, audit_rows):
    """Replace the audit table with this run's full evaluation history."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE bronze.scheme_mapping_audit"))
        if not audit_rows:
            return
        for row in audit_rows:
            row.setdefault("audit_id", str(uuid.uuid4()))
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_audit
                    (audit_id, rta, rta_scheme_code, rule_name,
                     execution_outcome, confidence_score, candidate_scheme_id)
                VALUES
                    (:audit_id, :rta, :rta_scheme_code, :rule_name,
                     :execution_outcome, :confidence_score, :candidate_scheme_id)
                """
            ),
            audit_rows,
        )


def write_review(master_engine, review_rows):
    """Replace pending review candidates, preserving rows already decided."""
    with master_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM public.scheme_mapping_review "
                "WHERE reviewer_decision IS NULL"
            )
        )
        if not review_rows:
            return
        for row in review_rows:
            row.setdefault("review_id", str(uuid.uuid4()))
        conn.execute(
            text(
                """
                INSERT INTO public.scheme_mapping_review
                    (review_id, rta, rta_scheme_code, rta_scheme_name,
                     candidate_rank, candidate_amfi_code, candidate_amfi_name,
                     candidate_score, rule_name)
                VALUES
                    (:review_id, :rta, :rta_scheme_code, :rta_scheme_name,
                     :candidate_rank, :candidate_amfi_code, :candidate_amfi_name,
                     :candidate_score, :rule_name)
                ON CONFLICT (rta, rta_scheme_code, candidate_rank) DO NOTHING
                """
            ),
            review_rows,
        )
```

- [ ] **Step 2: Replace Rules 1–4 in scheme_mapping.py with the engine**

In `python_scripts/scheme_mapping.py`, delete the rule blocks spanning `RULE 1 : EXACT NAME MATCH` through the end of `RULE 4 : FUZZY NAME MATCH` (currently lines 349–847), keeping `RULE 0 : ISIN MATCH` and `RULE 2 : PRODUCT MATCH` intact, and insert this in their place. Also add the imports at the top of the file and remove the now-stale comment at line 152 about loading inactive schemes.

`ISIN_MATCH` and `PRODUCT_MATCH` stay as the existing inline loops rather than moving into `RULE_REGISTRY`. They already write at confidence 100 through `update_best_match`, which only overwrites on strictly higher confidence, so no engine rule (98 or below) can ever displace them. That is precisely what makes the Task 1 regression guarantee hold: every one of the 223 baseline mappings came from `PRODUCT_MATCH` or `NAV_MATCH`, and both still run first at their original confidence. Moving them into the registry would be a behaviour change with no benefit here, so it is deliberately out of scope.

```python
from scheme_matching import rules as rule_mod
from scheme_matching.aliases import build_alias_fn, load_aliases
from scheme_matching.reference import (
    load_amc_map,
    load_overrides,
    write_audit,
    write_review,
)
from scheme_matching.rules import NOT_IN_AMFI, MatchContext, arbitrate, run_all
from scheme_matching.scheme_key import parse_scheme_key


def build_context(df, amfi_df, alias_fn, overrides):
    """Index the AMFI master by SchemeKey and by bucket, once per run."""
    amfi_by_key = {}
    amfi_by_bucket = {}
    amfi_names = {}

    for amfi_row in amfi_df.itertuples():
        key = parse_scheme_key(
            amfi_row.name_norm, amc_code=amfi_row.amc_code, alias_fn=alias_fn
        )
        if key is None:
            continue
        code = str(amfi_row.amfi_scheme_code)
        amfi_by_key.setdefault(key, []).append(code)
        amfi_by_bucket.setdefault(key.bucket(), []).append((key.core_name, code))
        amfi_names[code] = amfi_row.name_norm

    return MatchContext(
        amfi_by_key=amfi_by_key,
        amfi_by_bucket=amfi_by_bucket,
        amfi_names=amfi_names,
        overrides=overrides,
    )
```

Then, after the AMC merge and before the finalize block, run the engine:

```python
    # -------------------------------------------------
    # STRUCTURED MATCHING ENGINE
    # -------------------------------------------------

    alias_fn = build_alias_fn(load_aliases(master_engine))
    overrides = load_overrides(master_engine)

    amc_map = load_amc_map(master_engine)
    df = df.merge(
        amc_map[["rta", "rta_amc_code", "amfi_amc_code"]],
        on=["rta", "rta_amc_code"],
        how="left",
    )

    context = build_context(df, amfi_df, alias_fn, overrides)

    df["mapping_status"] = None
    df["scheme_key"] = [
        parse_scheme_key(
            r.rta_scheme_name, amc_code=r.amfi_amc_code, alias_fn=alias_fn
        )
        for r in df.itertuples()
    ]

    audit_rows = []
    review_rows = []

    for idx, row in df.iterrows():
        record = row.to_dict()
        candidates = run_all(record, context)

        for cand in candidates:
            audit_rows.append({
                "rta": record["rta"],
                "rta_scheme_code": record["rta_scheme_code"],
                "rule_name": cand.rule_name,
                "execution_outcome": "CANDIDATE",
                "confidence_score": cand.confidence,
                "candidate_scheme_id": (
                    None if cand.amfi_scheme_code is NOT_IN_AMFI
                    else str(cand.amfi_scheme_code)
                ),
            })

        winner = arbitrate(candidates)

        if winner is None:
            df.at[idx, "mapping_status"] = "UNMATCHED"
            continue

        if winner.amfi_scheme_code is NOT_IN_AMFI:
            df.at[idx, "mapping_status"] = "NOT_IN_AMFI"
            continue

        # Ambiguity that no rule resolved: write nothing, send to review.
        ambiguous = (
            winner.rule_name == "STRUCT_EXACT"
            and len(context.amfi_by_key.get(record["scheme_key"], [])) > 1
        )
        if ambiguous:
            df.at[idx, "mapping_status"] = "PENDING_REVIEW"
            for rank, cand in enumerate(
                sorted(candidates, key=lambda x: -x.score)[:3], start=1
            ):
                review_rows.append({
                    "rta": record["rta"],
                    "rta_scheme_code": record["rta_scheme_code"],
                    "rta_scheme_name": record["rta_scheme_name"],
                    "candidate_rank": rank,
                    "candidate_amfi_code": str(cand.amfi_scheme_code),
                    "candidate_amfi_name": context.amfi_names.get(
                        str(cand.amfi_scheme_code)
                    ),
                    "candidate_score": cand.score,
                    "rule_name": cand.rule_name,
                })
            continue

        update_best_match(
            df, idx, winner.amfi_scheme_code, winner.rule_name, winner.confidence
        )
        df.at[idx, "mapping_status"] = "MATCHED"

    write_audit(engine, audit_rows)
    write_review(master_engine, review_rows)
    df.drop(columns=["scheme_key"], inplace=True)
```

Add `mapping_status` to the INSERT statement's column list, `VALUES` clause, and `DO UPDATE SET` clause at `scheme_mapping.py:990-1028`.

- [ ] **Step 3: Run the pipeline**

```bash
cd /var/www/html/intelliwealth_layer_old_code/python_scripts && venv/bin/python scheme_mapping.py 2>&1 | tail -40
```

Expected: completes without error, and the `MAPPING SOURCE SUMMARY` block reports `Matched` at 370 or above out of 515. A number materially below 374 means the parser regressed — compare against `scheme_mapping_analysis/scheme_mapping_results.csv` before continuing.

- [ ] **Step 4: Run the regression harness**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass, including both regression tests. **A regression failure blocks this task.** If a baseline mapping changed, do not adjust the baseline — find out which rule took the scheme and why.

- [ ] **Step 5: Commit**

```bash
git add python_scripts/scheme_mapping.py python_scripts/scheme_matching/reference.py
git commit -m "feat: wire structured matching engine into the mapping pipeline"
```

---

### Task 13: Verification gate and report

The four gates from spec §5. Nothing here changes matching — it proves the matching is right.

**Files:**
- Create: `python_scripts/verify_scheme_mapping.py`
- Create: `python_scripts/tests/test_verification_gate.py`

**Interfaces:**
- Consumes: `nav_verify` (Task 10), `bronze.scheme_mapping` after Task 12
- Produces: `scheme_mapping_analysis/phase2_kfin_review.csv`, `scheme_mapping_analysis/phase2_nav_audit.csv`, `scheme_mapping_analysis/phase2_summary.md`

- [ ] **Step 1: Write the failing test**

`python_scripts/tests/test_verification_gate.py`:

```python
import pandas as pd


def test_coverage_improved_beyond_the_floor(current_df):
    """Spec success criterion: >= 370 of 515."""
    matched = len(current_df)
    assert matched >= 370, f"coverage regressed to {matched}"


def test_no_scheme_was_written_below_its_rule_threshold():
    from utils.db import engine

    df = pd.read_sql(
        """
        SELECT mapping_source, mapping_confidence
        FROM bronze.scheme_mapping
        WHERE amfi_scheme_code IS NOT NULL
        """,
        engine,
    )
    below = df[df.mapping_confidence < 90]
    assert below.empty, f"{len(below)} mappings written below confidence 90"


def test_pending_review_rows_have_no_amfi_code():
    """PENDING_REVIEW must never carry a written mapping."""
    from utils.db import engine

    df = pd.read_sql(
        """
        SELECT count(*) AS n
        FROM bronze.scheme_mapping
        WHERE mapping_status = 'PENDING_REVIEW'
          AND amfi_scheme_code IS NOT NULL
        """,
        engine,
    )
    assert df.n.iloc[0] == 0


def test_every_status_is_a_known_value():
    from utils.db import engine

    df = pd.read_sql(
        "SELECT DISTINCT mapping_status FROM bronze.scheme_mapping", engine
    )
    known = {"MATCHED", "PENDING_REVIEW", "NOT_IN_AMFI", "UNMATCHED", None}
    unknown = set(df.mapping_status) - known
    assert not unknown, f"unknown statuses: {unknown}"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/test_verification_gate.py -v
```

Expected: `test_every_status_is_a_known_value` and the coverage test may already pass after Task 12; any that fail identify real gaps to fix before proceeding.

- [ ] **Step 3: Write the verification script**

`python_scripts/verify_scheme_mapping.py`:

```python
"""Post-run verification gate for the scheme mapping engine.

Four gates, per the Phase 2 design §5:
  1. Regression   — the 223 pre-Phase-2 mappings are unchanged (pytest).
  2. NAV audit    — every new CAMS match agrees with nav_master.
  3. KFIN review  — every new KFIN match is written out for human sign-off.
  4. Collisions   — distinct RTA codes resolving to one AMFI code are reported.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scheme_matching import nav_verify  # noqa: E402
from utils.db import engine, master_engine  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "scheme_mapping_analysis"
BASELINE = Path(__file__).resolve().parent / "tests" / "baseline_mappings.csv"


def load_current():
    return pd.read_sql(
        """
        SELECT rta, rta_amc_code, rta_scheme_code, rta_scheme_name,
               amfi_scheme_code, mapping_source, mapping_confidence, mapping_status
        FROM bronze.scheme_mapping
        ORDER BY rta, rta_scheme_code
        """,
        engine,
        dtype=str,
    )


def new_matches(current):
    baseline = pd.read_csv(BASELINE, dtype=str)
    base_keys = set(zip(baseline.rta, baseline.rta_scheme_code))
    matched = current[current.amfi_scheme_code.notna()]
    mask = [
        (r.rta, r.rta_scheme_code) not in base_keys for r in matched.itertuples()
    ]
    return matched[mask]


def gate_nav_audit(new):
    """Gate 2. CAMS only — gold.scheme_nav has no KFIN rows."""
    cams = new[new.rta.isin(nav_verify.RTAS_WITH_NAV)]
    if cams.empty:
        print("[NAV AUDIT] no new CAMS matches to verify")
        return pd.DataFrame()

    fingerprints = nav_verify.load_rta_fingerprints(
        engine, master_engine, cams.rta_scheme_code.tolist()
    )
    all_dates = {d for fp in fingerprints.values() for d, _ in fp}
    amfi_navs = nav_verify.load_amfi_navs(master_engine, all_dates)

    results = []
    for row in cams.itertuples():
        fingerprint = fingerprints.get((row.rta, row.rta_scheme_code), [])
        results.append({
            "rta": row.rta,
            "rta_scheme_code": row.rta_scheme_code,
            "rta_scheme_name": row.rta_scheme_name,
            "amfi_scheme_code": row.amfi_scheme_code,
            "mapping_source": row.mapping_source,
            "nav_dates_available": len(fingerprint),
            "nav_verified": nav_verify.verify(
                fingerprint, row.amfi_scheme_code, amfi_navs
            ),
        })

    audit = pd.DataFrame(results)
    audit.to_csv(OUT_DIR / "phase2_nav_audit.csv", index=False)

    failed = audit[(~audit.nav_verified) & (audit.nav_dates_available > 0)]
    print(
        f"[NAV AUDIT] {len(audit)} new CAMS matches | "
        f"verified {int(audit.nav_verified.sum())} | "
        f"contradicted {len(failed)} | "
        f"no NAV data {int((audit.nav_dates_available == 0).sum())}"
    )
    if not failed.empty:
        print("[NAV AUDIT] CONTRADICTED — these must be investigated:")
        print(failed.to_string(index=False))
    return audit


def gate_kfin_review(new):
    """Gate 3. KFIN cannot be NAV-verified, so it goes to a human."""
    kfin = new[new.rta == "KFIN"].copy()
    amfi = pd.read_sql(
        "SELECT amfi_scheme_code, name_norm FROM public.amfi_scheme_master",
        master_engine,
        dtype=str,
    )
    kfin = kfin.merge(amfi, on="amfi_scheme_code", how="left")
    cols = [
        "rta_amc_code", "rta_scheme_code", "rta_scheme_name",
        "amfi_scheme_code", "name_norm", "mapping_source", "mapping_confidence",
    ]
    kfin[cols].to_csv(OUT_DIR / "phase2_kfin_review.csv", index=False)
    print(f"[KFIN REVIEW] {len(kfin)} new matches written for sign-off")
    return kfin


def gate_collisions(current):
    """Gate 4. Known-legitimate collisions: 100900, 153419."""
    matched = current[current.amfi_scheme_code.notna()]
    counts = matched.groupby("amfi_scheme_code").rta_scheme_code.nunique()
    collisions = counts[counts > 1]
    if collisions.empty:
        print("[COLLISIONS] none")
        return
    print(f"[COLLISIONS] {len(collisions)} AMFI codes shared by multiple RTA codes:")
    for amfi_code in collisions.index:
        dupes = matched[matched.amfi_scheme_code == amfi_code]
        print(f"  AMFI {amfi_code}:")
        for d in dupes.itertuples():
            print(f"    [{d.rta}] {d.rta_scheme_code} -> {d.rta_scheme_name}")


def write_summary(current, new):
    total = len(current)
    matched = int(current.amfi_scheme_code.notna().sum())
    status = current.mapping_status.fillna("UNKNOWN").value_counts()

    lines = [
        "# Scheme Mapping — Phase 2 Run Summary",
        "",
        f"| Metric | Count | % |",
        f"|---|---|---|",
        f"| Total RTA schemes | {total} | 100% |",
        f"| Matched | {matched} | {matched * 100 // total}% |",
        f"| Newly matched this phase | {len(new)} | — |",
        "",
        "## By status",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in status.items()]
    lines += ["", "## By rule", "", "| Rule | Count |", "|---|---|"]
    for k, v in current.mapping_source.fillna("NONE").value_counts().items():
        lines.append(f"| {k} | {v} |")

    (OUT_DIR / "phase2_summary.md").write_text("\n".join(lines) + "\n")
    print(f"[SUMMARY] {matched}/{total} matched, {len(new)} new")


def main():
    current = load_current()
    new = new_matches(current)
    gate_nav_audit(new)
    gate_kfin_review(new)
    gate_collisions(current)
    write_summary(current, new)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the gate**

```bash
cd /var/www/html/intelliwealth_layer_old_code/python_scripts && venv/bin/python verify_scheme_mapping.py
```

Expected: `[NAV AUDIT]` reports zero contradicted rows. **Any contradicted row is a wrong mapping and must be investigated before this task is complete** — the NAV disagreeing means the assigned AMFI code is a different fund.

- [ ] **Step 5: Run the full test suite**

```bash
cd /var/www/html/intelliwealth_layer_old_code && python_scripts/venv/bin/pytest python_scripts/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add python_scripts/verify_scheme_mapping.py python_scripts/tests/test_verification_gate.py scheme_mapping_analysis/phase2_*.csv scheme_mapping_analysis/phase2_summary.md
git commit -m "feat: add four-gate verification with NAV audit and KFIN review export"
```

---

## Final handoff

After Task 13, `scheme_mapping_analysis/phase2_kfin_review.csv` needs human sign-off. That file is the deliverable the user asked for — new KFIN matches cannot be NAV-verified and a person must confirm them. Approved rows are promoted into `public.scheme_mapping_override`, which is also the contract the Python Developer's approval API will implement.

Remaining unmatched schemes after this plan are dominated by matured Fixed Term Plans with maturity dates, capital-protection series, and legacy Retail plans. Closing those is override curation — data entry, not engineering — and is out of scope here, as are the roadmap's Priority 7 (dashboard) and Priority 8 (gold-layer migration).
