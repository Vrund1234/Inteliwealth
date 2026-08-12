# Scheme Mapping Engine — Phase 2 Design

**Date:** 2026-08-11
**Status:** Approved for planning
**Scope:** `python_scripts/scheme_mapping.py`, `bronze.scheme_mapping`, and supporting reference tables
**Supersedes the diagnosis in:** `scheme_mapping_analysis/scheme_mapping_summary.md` and
`scheme_mapping_analysis/IntelliWealth_Scheme_Mapping_Engine_Phase2_Roadmap.md`

---

## 1. Problem

The current run maps 223 of 515 distinct RTA schemes (43.3%). 292 are unmatched.

Two earlier documents diagnose this. Both are wrong about the cause, and building to
either would waste a sprint. This section records what was measured against the live
database on 2026-08-11 so the plan is not re-litigated later.

### 1.1 The actual root cause

Rules 3 and 4 filter AMFI candidates with:

```python
amfi_df["amc_code"] == row["amc_slug"]
```

`amfi_scheme_master.amc_code` holds the RTA-style code (`B`, `H`, `P`, `RMF`, `117`).
`rta_amc_code.amc_slug` holds a human label (`ABSL`, `HDFC`, `ICICI`, `MIRAE`).

Exactly **1 of 49** slug values is also a valid AMFI `amc_code`. For every other row the
filter returns an empty candidate set before any name comparison happens. This is why
Rule 3 (`AMC_NAME`) and Rule 4 (`NAME_FUZZY`) each matched **0** schemes — for CAMS as
well as KFIN. The rules were never exercised.

### 1.2 Corrections to the existing analysis

| Claim | Source | Measured reality |
|---|---|---|
| ~108 KFIN schemes blocked by numeric AMC codes missing from `rta_amc_code` | summary §5, roadmap P1 | **2 schemes.** 27 of 29 RTA AMC codes in the data exist in both `rta_amc_code` and `amfi.amc_code`, covering 513/515 schemes. Only KFIN `906` (Altiva) and `908` (Diviniti) are absent — 1 scheme each. |
| Completing `rta_amc_code` unlocks 100–120 schemes | roadmap P1 | Unlocks 2. |
| "The bottleneck is not the matching logic — it is incomplete reference data" | roadmap, opening | Inverted. Reference data is 99.6% complete. The bottleneck is §1.1. |
| Loading inactive/matured AMFI schemes helps legacy FTPs | script comment at `scheme_mapping.py:152` | No-op. All 16,345 rows in `amfi_scheme_master` are `status = 'ACTIVE'`. There is no inactive partition. |
| Rule 4 fuzzy matching is "deferred" | summary §2 | It runs, but `score_cutoff=98` with `fuzz.ratio` is exact match in practice. Even with §1.1 fixed it would contribute close to nothing. |

### 1.3 Secondary blockers

**Name shape divergence.** RTA and AMFI describe the same fund with different filler
tokens, so string equality fails:

| RTA | AMFI |
|---|---|
| `Canara Robeco Mid Cap Fund - Regular Growth` | `CANARA ROBECO MID CAP FUND REGULAR PLAN GROWTH OPTION` |
| `HDFC Hybrid Equity Fund - Regular Plan - Growth` | `HDFC HYBRID EQUITY FUND GROWTH PLAN` |

**No NAV fallback for KFIN.** `gold.scheme_nav` holds 68,424 rows across 332 scheme
codes, **all CAMS, zero KFIN**. Rule 3.5 is structurally incapable of matching or
verifying any of the 160 unmatched KFIN schemes. Every confidence mechanism for KFIN
must therefore come from names, codes, and human review.

**Latent crash in duplicate expansion.** The expansion insert at
`scheme_mapping.py:1394` declares `ON CONFLICT (rta, rta_scheme_code, amfi_scheme_code)`,
but the only unique constraint on `bronze.scheme_mapping` is
`uq_scheme_mapping UNIQUE (rta, rta_scheme_code)`. Postgres raises
*"no unique or exclusion constraint matching the ON CONFLICT specification"* whenever
that branch executes with rows. It is currently masked because `target_names` is empty.

**Ambiguity the name cannot resolve.** Some RTA names omit the option entirely:
`UTI Flexi Cap Fund - Regular Plan` (code `108EQGP`) must choose between AMFI `100669`
(Growth) and `100668` (IDCW). The RTA product code suffix carries the missing signal.

### 1.4 Measured recoverability

A throwaway attribute parser was run against all 292 unmatched rows:

| Outcome | Count |
|---|---|
| Resolves to exactly one AMFI code | **151** |
| Resolves to 2–3 candidates, needs a tie-break | 21 |
| No AMFI counterpart found by name | 120 |

151 recoveries move coverage from 223/515 (43.3%) to **374/515 (72.6%)** using an exact
structured key alone, with no fuzzy matching. The 120 are dominated by matured Fixed Term
Plans carrying maturity dates, capital-protection series, segregated portfolios, and
legacy `Retail` plans.

---

## 2. Goals and non-goals

**Goals**

1. Correct the AMC join so name-based rules can execute at all.
2. Replace string equality with attribute-aware structured matching.
3. Reach 76–80% automatic coverage while writing **zero** low-confidence mappings.
4. Make every rule's outcome auditable, not just the winner's.
5. Give unmatchable schemes an honest terminal state instead of counting them as failures.

**Non-goals** — each deserves its own spec and is explicitly out of scope:

- Monitoring dashboard (roadmap P7).
- Gold-layer migration to internal `scheme_id` (roadmap P8).
- Ingesting KFIN NAV data into `gold.scheme_nav`.
- Review/approval APIs. This phase creates the tables those APIs will read.

**Success criteria**

- All 223 currently-matched schemes retain their exact AMFI code. Non-negotiable.
- Automatic coverage ≥ 370/515 (71.8%), targeting 76–80%. The prototype measured 374 from
  the structured key alone; landing materially below that indicates a parser regression.
- Every newly matched CAMS scheme is NAV-verified against `nav_master`.
- Every newly matched KFIN scheme appears in a side-by-side review CSV.
- No mapping is written below its rule's confidence threshold.

---

## 3. Design

### 3.1 The structured scheme key

The core change. Both RTA and AMFI names parse into the same tuple, and matching compares
tuples rather than strings.

```
SchemeKey(amc_code, core_name, plan, option, frequency, qualifiers)
```

| Field | Values | Default when absent | Notes |
|---|---|---|---|
| `amc_code` | RTA/AMFI shared vocabulary (`B`, `H`, `117`) | — | Never defaulted; a row without one cannot match |
| `plan` | `REGULAR`, `DIRECT` | `REGULAR` | RTA data is overwhelmingly regular-plan |
| `option` | `GROWTH`, `IDCW` | `GROWTH` | Justified in §3.2 |
| `frequency` | `DAILY`, `WEEKLY`, `FORTNIGHTLY`, `MONTHLY`, `QUARTERLY`, `HALF_YEARLY`, `ANNUAL` | `NULL` | Only meaningful for IDCW |
| `qualifiers` | set of `SEGREGATED`, `RETAIL`, `INSTITUTIONAL` | empty set | Compared exactly — see below |
| `core_name` | remaining tokens, normalized | — | Everything not captured above |

`qualifiers` must match **exactly**, including empty-set equality. `HDFC Arbitrage Fund`
and `HDFC Arbitrage Fund Retail Plan` are different funds with different NAVs. Treating a
qualifier as noise silently merges them.

The three qualifier values above are the ones observed in the current 515 RTA names. The
implementation task includes a sweep of both name sets for further distinguishing tokens;
any found are added to this closed list rather than being allowed to fall into `core_name`.

Parsing order, applied to both sides identically:

1. Strip `(formerly known as …)`, `(erstwhile …)`, and bare `formerly …` parentheticals.
2. Strip regulatory boilerplate: `(ELSS U/S 80C of IT ACT)`, `U/S 80C`.
3. Strip maturity annotations: `(Maturity Date - 10-JUL-2014)`, `Maturity Date 08-Apr-2013`.
4. Apply `scheme_name_alias` substitutions (§3.3).
5. Extract `plan`, `option`, `frequency`, `qualifiers` into fields.
6. Remove structural filler: `FUND`, `SCHEME`, `PLAN`, `OPTION`, `THE`, `OF`, `MUTUAL`.
7. Whatever remains, uppercased and single-spaced, is `core_name`.

Steps 5 and 6 are ordered deliberately. Attributes are *extracted* before filler is
*deleted*, so `Regular Plan` becomes `plan=REGULAR` rather than vanishing. Deleting these
tokens without extracting them first would collapse a fund's Growth and IDCW variants into
one key and produce confidently wrong mappings — the single most dangerous failure mode in
this design.

### 3.2 Defaulting `option` to `GROWTH`

When an RTA name carries no dividend token, the scheme is treated as Growth. Two
independent signals support this, and the rule requires them to agree:

1. **Name.** Absence of `IDCW`, `DIVIDEND`, `DIV`, `PAYOUT`, `REINVEST`.
2. **KFIN product code suffix.** `117IORG` → `G` → Growth; `117IORD` → `D` → IDCW;
   `120EFGP` → Growth; `120EFDP` → IDCW; `120COID` → daily IDCW; `117SFRW` → weekly IDCW.

Where both signals resolve and **agree**, the rule proceeds at full confidence. Where they
**disagree**, the row is routed to review rather than guessed. CAMS product codes do not
follow a comparable convention, so CAMS relies on the name signal plus NAV verification.

### 3.3 Reference tables

Three new tables in the master database, all read at the start of a run. A fourth table,
`bronze.scheme_mapping_audit`, is written rather than read and is specified in §3.4.

**`public.scheme_name_alias`** — replaces hardcoded substitutions with configurable data.

| Column | Type | Notes |
|---|---|---|
| `alias_id` | uuid PK | |
| `raw_term` | text | Term as it appears in raw names |
| `normalized_term` | text | Replacement (may be empty to delete) |
| `alias_type` | text | `TOKEN` or `FUND_RENAME` |
| `amc_code` | varchar NULL | Scopes a rename to one AMC; NULL = global |
| `is_active` | boolean | |

The `alias_type` split is load-bearing. `TOKEN` aliases are word-level and global
(`GR.` → `GROWTH`, `DIV` → `IDCW`, `FTP` → `FIXED TERM PLAN`). `FUND_RENAME` aliases
rewrite a whole `core_name` and are AMC-scoped (`LONG TERM EQUITY` → `ELSS TAX SAVER`
within `FTI`). Applying a fund rename globally as a token would corrupt unrelated AMCs.

Plan, option, and frequency terms are **not** aliases and must never be added here. They
are parsed attributes per §3.1.

**`public.scheme_mapping_override`** — hand-curated, wins over everything.

| Column | Type | Notes |
|---|---|---|
| `override_id` | uuid PK | |
| `rta` | varchar | Part of natural key |
| `rta_scheme_code` | varchar | Part of natural key |
| `amfi_scheme_code` | varchar | Nullable — NULL asserts `NOT_IN_AMFI` |
| `reason` | text | Required |
| `mapped_by` | varchar | |
| `mapped_at` | timestamp | |
| `is_active` | boolean | |

Unique on `(rta, rta_scheme_code)`. The roadmap keyed on `rta_scheme_code` alone, which is
not unique across RTAs. It also stored `internal_scheme_id`; this design stores
`amfi_scheme_code` and lets `scheme_id` derive as the pipeline already does, so the two
cannot drift apart.

A NULL `amfi_scheme_code` is meaningful: it is a curator asserting the fund does not exist
in AMFI, which sets `NOT_IN_AMFI` and stops the scheme being re-examined every run.

**`public.scheme_mapping_review`** — top-N candidates awaiting a human decision.

| Column | Type | Notes |
|---|---|---|
| `review_id` | uuid PK | |
| `rta` | varchar | |
| `rta_scheme_code` | varchar | |
| `rta_scheme_name` | text | Denormalized for reviewer readability |
| `candidate_rank` | int | 1..3 |
| `candidate_amfi_code` | varchar | |
| `candidate_amfi_name` | text | Denormalized |
| `candidate_score` | numeric | |
| `rule_name` | varchar | Rule that proposed it |
| `reviewer_decision` | varchar | `APPROVED` / `REJECTED` / NULL |
| `reviewed_by` | varchar | |
| `reviewed_at` | timestamp | |

One row per candidate with a rank, rather than the roadmap's three fixed columns. Fixed
columns make "top 5" a migration and make querying approved candidates awkward.

An approved review row is promoted into `scheme_mapping_override`, which is what makes the
decision permanent. This is the intended path for the Python Developer's approval API.

### 3.4 Rule registry

Rules become pure functions with a uniform signature, held in an ordered registry:

```python
def rule(row, context) -> list[Candidate]
```

`Candidate` carries `amfi_scheme_code`, `score`, `rule_name`, and `confidence`. A rule
returning `[]` means no opinion; it never mutates state.

**All rules always execute.** The roadmap specified stopping at the first confident match,
but that contradicts its own audit and top-3 requirements — you cannot record an
evaluation history for rules that never ran. At 515 rows the saving is irrelevant, so the
pipeline evaluates everything and arbitrates afterwards. Arbitration takes the highest
confidence; ties break by registry order.

This is a code-level registry, not a database-driven engine. Rule logic is not
configuration, and the extra indirection would buy nothing at this scale.

Every rule execution appends a row to `bronze.scheme_mapping_audit`
(`rta`, `rta_scheme_code`, `rule_name`, `execution_outcome`, `confidence_score`,
`candidate_scheme_id`, `evaluated_at`), truncated and rewritten per run.

### 3.5 The rules

| Order | Rule | Confidence | Writes when |
|---|---|---|---|
| 1 | `OVERRIDE` | 100 | A matching active override row exists |
| 2 | `ISIN_MATCH` | 100 | Unchanged; dormant until RTAs supply ISIN |
| 3 | `PRODUCT_MATCH` | 100 | Unchanged; exact `amc_code` + exact `name_norm` |
| 4 | `STRUCT_EXACT` | 98 | Full `SchemeKey` matches exactly one AMFI row |
| 5 | `STRUCT_TIEBREAK` | 95 | Key matches 2–3 rows, resolved by code suffix or NAV |
| 6 | `NAV_MATCH` | 97 | Unchanged; CAMS only |
| 7 | `CORE_FUZZY` | 90 | Fuzzy on `core_name` only, inside an identical-attribute bucket |

`CORE_FUZZY` is the only inexact rule and carries three guards, all of which must pass:

- Candidates are bucketed by identical `(amc_code, plan, option, frequency, qualifiers)`.
  Fuzziness applies to `core_name` alone, never to attributes.
- `token_sort_ratio ≥ 88`.
- The top score exceeds the runner-up by **≥ 5 points**. A near-tie means the name does not
  distinguish the funds, and guessing is worse than not answering.

`fuzz.token_sort_ratio` replaces `fuzz.ratio` because word order differs between sources
(`HDFC LARGE CAP FUND IDCW OPTION REGULAR PLAN` vs `HDFC Large Cap Fund - Regular Plan - IDCW`).

Any rule that produces candidates but fails its guards writes **nothing** and emits its top
3 to `scheme_mapping_review`. This is the agreed no-auto-write-below-threshold policy.

### 3.6 Terminal states

`bronze.scheme_mapping.mapping_status` already exists and is unused by the script. This
design populates it; no schema change is needed.

| Status | Meaning |
|---|---|
| `MATCHED` | `amfi_scheme_code` written by a rule at or above threshold |
| `PENDING_REVIEW` | Candidates exist but failed the guards; rows in `scheme_mapping_review` |
| `NOT_IN_AMFI` | Asserted by an override with NULL `amfi_scheme_code` |
| `UNMATCHED` | No rule produced any candidate |

Separating `NOT_IN_AMFI` from `UNMATCHED` matters because ~120 schemes have no AMFI
counterpart at all. Counting them as failures permanently caps the reported rate below 80%
and buries genuine failures in noise.

### 3.7 Fixes to existing defects

- **AMC join.** Add `amfi_amc_code` to `rta_amc_code`, backfill the 27 known codes, add
  rows for KFIN `906` and `908`. Rules join on this column. Making the link explicit data
  rather than relying on the coincidence that RTA and AMFI codes happen to agree means a
  future divergence is a data edit, not a code change. `amc_slug` is retained for display.
- **Duplicate expansion.** Add `uq_scheme_mapping_amfi UNIQUE (rta, rta_scheme_code,
  amfi_scheme_code)` so the existing `ON CONFLICT` clause resolves. The expansion block is
  retained rather than removed, because `STRUCT_EXACT` will start producing the
  duplicate-name cases that block was written to handle.
- **Dead comment.** Remove the "load inactive schemes" comment at `scheme_mapping.py:152`,
  which describes a fix that cannot apply.

---

## 4. Data flow

```
silver.transaction_master_new ──┐
                                ├─→ parse SchemeKey ─→ rule registry ─→ arbitrate
public.amfi_scheme_master ──────┤        ▲                   │
public.scheme_name_alias ───────┘        │                   ├─→ bronze.scheme_mapping
public.scheme_mapping_override ──────────┘                   ├─→ bronze.scheme_mapping_audit
gold.scheme_nav + public.nav_master ─────────────────────────┴─→ public.scheme_mapping_review
                                                                        │
                                                          reviewer approves
                                                                        ▼
                                                    public.scheme_mapping_override
```

The override table is both an input to the next run and the destination of approved
reviews. That loop is what converts one-off human judgement into permanent reference data.

---

## 5. Verification

No database write is accepted until all four gates pass. Gate 1 is blocking; a single
regression fails the run.

1. **Regression.** Snapshot the 223 current `(rta, rta_scheme_code) → amfi_scheme_code`
   pairs before any change. Assert every pair is byte-identical after. A scheme moving from
   matched to unmatched, or to a different AMFI code, is a failure.
2. **NAV audit (CAMS).** For every newly matched CAMS scheme, compare the 3 most recent
   NAVs from `gold.scheme_nav` against `nav_master` for the assigned AMFI code, rounded to
   4 decimals. Any mismatch is reported and blocks that row.
3. **KFIN review CSV.** Every newly matched KFIN scheme is written to a side-by-side CSV
   (`rta_scheme_code`, `rta_scheme_name`, `amfi_scheme_code`, AMFI `name_norm`, rule,
   confidence) for sign-off. NAV verification is impossible here per §1.3.
4. **Collision check.** Existing detection is retained. Distinct RTA codes resolving to one
   AMFI code are reported. Both known collisions (`100900`, `153419`) are legitimate and
   are recorded as expected.

Unit tests cover the parser directly, since it is where wrong mappings originate:
attribute extraction, `GROWTH` defaulting, qualifier preservation, parenthetical stripping,
and the `CORE_FUZZY` margin guard rejecting near-ties.

---

## 6. Expected outcome

| Stage | Matched | Coverage |
|---|---|---|
| Today | 223 | 43.3% |
| AMC join fixed + `STRUCT_EXACT` | ~374 | 72.6% |
| `STRUCT_TIEBREAK` + `CORE_FUZZY` | ~390–400 | 76–78% |
| After override curation | 400+ | 78%+ |

The 72.6% figure is measured, not projected. The 76–78% band is an estimate over the 21
ambiguous and a portion of the 120 unmatched. Beyond that requires override curation, which
is data entry rather than engineering.

Confidence is held at 95%+ by writing nothing below threshold. Coverage that would come
from relaxing the guards is deliberately declined.

---

## 7. Impact on the roadmap's execution split

- **Data Engineer 1.** Priority 1 (`rta_amc_code`) was sized for a sprint but is a
  one-hour change unlocking 2 schemes. That time moves to the §3.1 parser and curating
  `scheme_name_alias`, which is where the 151 recoveries actually come from.
- **Data Engineer 2.** Unchanged in substance. The rule engine is code-level (§3.4), and
  the audit table lands with it.
- **Python Developer.** Blocked until `scheme_mapping_review` and
  `scheme_mapping_override` exist. The approval endpoint promotes a review row into an
  override; that contract is fixed by §3.3 and can be built against before the engine is
  finished.
