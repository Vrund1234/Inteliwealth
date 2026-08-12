"""Accuracy analysis of bronze.scheme_mapping against public.amfi_scheme_master.

Produces:
  scheme_mapping_analysis/scheme_mapping_report.md   — full written report
  scheme_mapping_analysis/unmatched_schemes.csv      — every unmatched scheme + reason
  scheme_mapping_analysis/matched_schemes.csv        — every matched scheme + accuracy checks

The AMFI master was restructured on 2026-08-12: plan and option are now
published as structured plan_type / option_type columns. That gives an
independent axis to audit every match on, separate from the names the matcher
itself used.
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scheme_matching.aliases import build_alias_fn, load_aliases  # noqa: E402
from scheme_matching.reference import load_amc_map  # noqa: E402
from scheme_matching.scheme_key import parse_scheme_key  # noqa: E402
from utils.db import engine, master_engine  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "scheme_mapping_analysis"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load():
    mapping = pd.read_sql(
        """
        SELECT rta, rta_amc_code, rta_scheme_code, rta_scheme_name,
               amfi_scheme_code, mapping_source, mapping_confidence, mapping_status
        FROM bronze.scheme_mapping
        ORDER BY rta, rta_scheme_code
        """,
        engine,
        dtype=str,
    )
    amfi = pd.read_sql(
        """
        SELECT amfi_scheme_code, amc_code, amc_name, scheme_name,
               scheme_nav_name, normalized_scheme_name,
               plan_type, option_type, scheme_category, sub_category, status
        FROM public.amfi_scheme_master
        """,
        master_engine,
        dtype=str,
    )
    review = pd.read_sql(
        "SELECT rta, rta_scheme_code, count(*) AS n_candidates "
        "FROM public.scheme_mapping_review GROUP BY rta, rta_scheme_code",
        master_engine,
        dtype=str,
    )
    return mapping, amfi, review


# ---------------------------------------------------------------------------
# Reason classification for unmatched schemes
# ---------------------------------------------------------------------------

REASONS = {
    "AMC_NOT_IN_AMFI":
        "The scheme's AMC has no schemes at all in amfi_scheme_master, so no "
        "match is possible by any method.",
    "MATURED_FIXED_TERM_PLAN":
        "A closed-end Fixed Term / Fixed Maturity Plan carrying a maturity "
        "date or series code. These are dropped from the AMFI master once they "
        "mature, so the counterpart no longer exists.",
    "CAPITAL_PROTECTION_SERIES":
        "A closed-end capital-protection-oriented series. Same lifecycle as "
        "FTPs — absent from the current AMFI master.",
    "SEGREGATED_PORTFOLIO":
        "A segregated (side-pocketed) portfolio created after a credit event. "
        "AMFI does not publish these as separate schemes.",
    "LEGACY_RETAIL_INSTITUTIONAL_PLAN":
        "A Retail or Institutional share class that AMFI has retired; only the "
        "surviving Regular/Direct plans remain in the master.",
    "PLAN_OPTION_VARIANT_ABSENT":
        "The fund exists in the AMFI master for this AMC, but not in this "
        "specific plan / option / frequency combination.",
    "FUND_NAME_NOT_IN_AMFI":
        "No fund with this core name exists under this AMC in the AMFI master. "
        "A mixed bucket: rebrands still present under an older string "
        "(recoverable with a scheme_name_alias row) sit alongside funds merged "
        "away or wound up (not mappable 1:1 — see section 6).",
    "AMBIGUOUS_PENDING_REVIEW":
        "Candidates were found but none passed the confidence guards, so "
        "nothing was written. Sent to scheme_mapping_review for a human.",
    "ASSERTED_NOT_IN_AMFI":
        "A curator recorded an override asserting this scheme has no AMFI "
        "counterpart.",
}

_FTP = re.compile(
    r"FIXED\s+TERM|FIXED\s+MATURITY|\bFTP\b|\bFMP\b|MATURITY\s*DATE|"
    r"INTERVAL\s+FUND|\bSERIES\b.*\bDAYS\b",
    re.I,
)
_CAPITAL = re.compile(r"CAPITAL\s+PROTECTION|DUAL\s+ADVANTAGE", re.I)
_SEGREGATED = re.compile(r"SEGREGATED", re.I)
_RETAIL = re.compile(r"\bRETAIL\b|\bINSTITUTIONAL\b", re.I)


def classify(row, amfi_by_amc, key_index, core_index, review_codes, amc_has_amfi):
    """Return (reason_code, detail) for one unmatched scheme."""
    name = row.rta_scheme_name or ""

    if row.mapping_status == "NOT_IN_AMFI":
        return "ASSERTED_NOT_IN_AMFI", "override asserts absence"

    if not amc_has_amfi.get((row.rta, row.rta_amc_code), False):
        return (
            "AMC_NOT_IN_AMFI",
            f"AMC {row.rta_amc_code} has 0 rows in amfi_scheme_master",
        )

    if (row.rta, row.rta_scheme_code) in review_codes:
        n = review_codes[(row.rta, row.rta_scheme_code)]
        return "AMBIGUOUS_PENDING_REVIEW", f"{n} candidates, none passed guards"

    if _SEGREGATED.search(name):
        return "SEGREGATED_PORTFOLIO", "name contains SEGREGATED"
    if _CAPITAL.search(name):
        return "CAPITAL_PROTECTION_SERIES", "capital protection / dual advantage"
    if _FTP.search(name):
        return "MATURED_FIXED_TERM_PLAN", "fixed term / maturity date in name"

    key = row.scheme_key
    if key is not None:
        # Does the same core name exist for this AMC in any variant?
        if (key.amc_code, key.core_name) in core_index:
            variants = core_index[(key.amc_code, key.core_name)]
            if _RETAIL.search(name):
                return (
                    "LEGACY_RETAIL_INSTITUTIONAL_PLAN",
                    f"fund exists in {variants} other variant(s), none Retail/Institutional",
                )
            return (
                "PLAN_OPTION_VARIANT_ABSENT",
                f"fund exists for this AMC in {variants} other variant(s), "
                f"but not as {key.plan}/{key.option}"
                + (f"/{key.frequency}" if key.frequency else ""),
            )

    if _RETAIL.search(name):
        return "LEGACY_RETAIL_INSTITUTIONAL_PLAN", "retail/institutional share class"

    return "FUND_NAME_NOT_IN_AMFI", "no fund with this core name under this AMC"


# ---------------------------------------------------------------------------
# Accuracy checks for matched schemes
# ---------------------------------------------------------------------------

def audit_matched(matched, amfi):
    """Cross-check every match against amfi_scheme_master's structured columns."""
    a = amfi.set_index("amfi_scheme_code")
    rows = []
    for r in matched.itertuples():
        rec = a.loc[r.amfi_scheme_code] if r.amfi_scheme_code in a.index else None
        if rec is None:
            rows.append({
                "rta": r.rta, "rta_scheme_code": r.rta_scheme_code,
                "rta_scheme_name": r.rta_scheme_name,
                "amfi_scheme_code": r.amfi_scheme_code,
                "amfi_scheme_nav_name": None, "amfi_amc_code": None,
                "amfi_plan_type": None, "amfi_option_type": None,
                "mapping_source": r.mapping_source,
                "mapping_confidence": r.mapping_confidence,
                "code_exists_in_amfi": False, "amc_agrees": False,
                "plan_agrees": None, "option_agrees": None, "issues": "AMFI_CODE_NOT_FOUND",
            })
            continue

        key = r.scheme_key
        issues = []

        amc_ok = str(rec.amc_code) == str(r.rta_amc_code)
        if not amc_ok:
            issues.append("AMC_MISMATCH")

        plan_ok = option_ok = None
        if key is not None:
            if pd.notna(rec.plan_type) and rec.plan_type:
                plan_ok = str(rec.plan_type).upper() == key.plan
                if not plan_ok:
                    issues.append(f"PLAN_MISMATCH(rta={key.plan},amfi={rec.plan_type})")
            if pd.notna(rec.option_type) and rec.option_type:
                option_ok = str(rec.option_type).upper() == key.option
                if not option_ok:
                    issues.append(
                        f"OPTION_MISMATCH(rta={key.option},amfi={rec.option_type})"
                    )

        rows.append({
            "rta": r.rta, "rta_scheme_code": r.rta_scheme_code,
            "rta_scheme_name": r.rta_scheme_name,
            "amfi_scheme_code": r.amfi_scheme_code,
            "amfi_scheme_nav_name": rec.scheme_nav_name,
            "amfi_amc_code": rec.amc_code,
            "amfi_plan_type": rec.plan_type, "amfi_option_type": rec.option_type,
            "mapping_source": r.mapping_source,
            "mapping_confidence": r.mapping_confidence,
            "code_exists_in_amfi": True, "amc_agrees": amc_ok,
            "plan_agrees": plan_ok, "option_agrees": option_ok,
            "issues": "; ".join(issues),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    mapping, amfi, review = load()
    alias_fn = build_alias_fn(load_aliases(master_engine))
    amc_map = load_amc_map(master_engine)

    mapping = mapping.merge(
        amc_map[["rta", "rta_amc_code", "amfi_amc_code", "amc_slug"]],
        on=["rta", "rta_amc_code"], how="left",
    )

    mapping["scheme_key"] = [
        parse_scheme_key(r.rta_scheme_name, amc_code=r.amfi_amc_code, alias_fn=alias_fn)
        for r in mapping.itertuples()
    ]

    # Index the AMFI master the same way the matcher does.
    key_index, core_index = {}, {}
    for r in amfi.itertuples():
        k = parse_scheme_key(r.scheme_nav_name, amc_code=r.amc_code, alias_fn=alias_fn)
        if k is None:
            continue
        key_index.setdefault(k, []).append(r.amfi_scheme_code)
        core_index[(k.amc_code, k.core_name)] = core_index.get(
            (k.amc_code, k.core_name), 0
        ) + 1

    amfi_amcs = set(amfi.amc_code.dropna())
    amc_has_amfi = {
        (r.rta, r.rta_amc_code): (r.amfi_amc_code in amfi_amcs)
        for r in amc_map.itertuples()
    }
    review_codes = {
        (r.rta, r.rta_scheme_code): int(r.n_candidates) for r in review.itertuples()
    }

    matched = mapping[mapping.amfi_scheme_code.notna()].copy()
    unmatched = mapping[mapping.amfi_scheme_code.isna()].copy()

    # --- unmatched: classify ------------------------------------------------
    cls = [
        classify(r, amfi, key_index, core_index, review_codes, amc_has_amfi)
        for r in unmatched.itertuples()
    ]
    unmatched["reason_code"] = [c[0] for c in cls]
    unmatched["reason_detail"] = [c[1] for c in cls]
    unmatched["reason_description"] = unmatched.reason_code.map(REASONS)
    unmatched["parsed_core_name"] = [
        k.core_name if k else None for k in unmatched.scheme_key
    ]
    unmatched["parsed_plan"] = [k.plan if k else None for k in unmatched.scheme_key]
    unmatched["parsed_option"] = [k.option if k else None for k in unmatched.scheme_key]
    unmatched["parsed_frequency"] = [
        k.frequency if k else None for k in unmatched.scheme_key
    ]

    un_cols = [
        "rta", "rta_amc_code", "amc_slug", "rta_scheme_code", "rta_scheme_name",
        "mapping_status", "reason_code", "reason_detail", "reason_description",
        "parsed_core_name", "parsed_plan", "parsed_option", "parsed_frequency",
    ]
    unmatched[un_cols].to_csv(OUT / "unmatched_schemes.csv", index=False)

    # --- matched: audit -----------------------------------------------------
    audit = audit_matched(matched, amfi)
    audit.to_csv(OUT / "matched_schemes.csv", index=False)

    write_report(mapping, matched, unmatched, audit, amfi)

    print(f"matched   : {len(matched)}")
    print(f"unmatched : {len(unmatched)}")
    print(f"issues    : {(audit.issues != '').sum()}")
    print(f"written   : {OUT}/scheme_mapping_report.md")
    print(f"            {OUT}/unmatched_schemes.csv")
    print(f"            {OUT}/matched_schemes.csv")


def md_table(df, cols=None):
    cols = cols or list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for r in df.itertuples(index=False):
        d = dict(zip(df.columns, r))
        out.append("| " + " | ".join(str(d[c]) for c in cols) + " |")
    return "\n".join(out)


def write_report(mapping, matched, unmatched, audit, amfi):
    total = len(mapping)
    n_match = len(matched)
    pct = round(n_match * 100 / total, 1)

    by_rta = (
        mapping.assign(m=mapping.amfi_scheme_code.notna())
        .groupby("rta").m.agg(["sum", "count"])
        .rename(columns={"sum": "matched", "count": "total"})
    )
    by_rta["unmatched"] = by_rta.total - by_rta.matched
    by_rta["pct"] = (by_rta.matched * 100 / by_rta.total).round(1)
    by_rta = by_rta.reset_index()

    src = matched.mapping_source.value_counts().reset_index()
    src.columns = ["mapping_source", "schemes"]
    conf = {"OVERRIDE": 100, "ISIN_MATCH": 100, "PRODUCT_MATCH": 100,
            "STRUCT_EXACT": 98, "NAV_MATCH": 97, "STRUCT_TIEBREAK": 95,
            "CORE_FUZZY": 90}
    src["confidence"] = src.mapping_source.map(conf)

    status = mapping.mapping_status.fillna("(none)").value_counts().reset_index()
    status.columns = ["mapping_status", "schemes"]

    reasons = unmatched.reason_code.value_counts().reset_index()
    reasons.columns = ["reason_code", "schemes"]
    reasons["what it means"] = reasons.reason_code.map(REASONS)

    by_amc = (
        mapping.assign(m=mapping.amfi_scheme_code.notna())
        .groupby(["rta", "rta_amc_code", "amc_slug"], dropna=False)
        .m.agg(["sum", "count"]).rename(columns={"sum": "matched", "count": "total"})
    )
    by_amc["pct"] = (by_amc.matched * 100 / by_amc.total).round(1)
    by_amc = by_amc.reset_index().sort_values(["pct", "total"], ascending=[True, False])

    issues = audit[audit.issues != ""]
    plan_checked = audit.plan_agrees.notna().sum()
    plan_ok = (audit.plan_agrees == True).sum()  # noqa: E712
    opt_checked = audit.option_agrees.notna().sum()
    opt_ok = (audit.option_agrees == True).sum()  # noqa: E712

    lines = [
        "# Scheme Mapping — Accuracy Report",
        "",
        "> Generated by `python_scripts/analyze_scheme_mapping.py`  ",
        "> Source: `bronze.scheme_mapping` audited against "
        "`public.amfi_scheme_master` (16,345 schemes)",
        "",
        "---",
        "",
        "## 1. Headline",
        "",
        "| Metric | Count | % |",
        "|---|---|---|",
        f"| Total distinct RTA schemes | **{total}** | 100% |",
        f"| Matched to an AMFI code | **{n_match}** | **{pct}%** |",
        f"| Unmatched | {len(unmatched)} | {round(100 - pct, 1)}% |",
        "",
        "Baseline before this work was 223 / 515 (43.3%).",
        "",
        "## 2. By RTA",
        "",
        md_table(by_rta, ["rta", "total", "matched", "unmatched", "pct"]),
        "",
        "## 3. By matching rule",
        "",
        md_table(src, ["mapping_source", "schemes", "confidence"]),
        "",
        "## 4. Terminal status",
        "",
        md_table(status, ["mapping_status", "schemes"]),
        "",
        "- `MATCHED` — an AMFI code was written.",
        "- `PENDING_REVIEW` — candidates existed but failed the confidence "
        "guards, so nothing was written. Awaiting a human in "
        "`public.scheme_mapping_review`.",
        "- `NOT_IN_AMFI` — a curator asserted no AMFI counterpart exists.",
        "- `UNMATCHED` — no rule produced any candidate.",
        "",
        "---",
        "",
        "## 5. Accuracy of the matched schemes",
        "",
        "The AMFI master now publishes `plan_type` and `option_type` as "
        "structured columns. Those were **not** used to produce the mappings, "
        "so checking each match against them is an independent audit rather "
        "than a restatement of the matcher's own logic.",
        "",
        "| Check | Result |",
        "|---|---|",
        f"| AMFI code exists in `amfi_scheme_master` | "
        f"{int(audit.code_exists_in_amfi.sum())} / {len(audit)} |",
        f"| AMC agrees between RTA and AMFI | "
        f"{int(audit.amc_agrees.sum())} / {len(audit)} |",
        f"| Plan agrees (where AMFI publishes `plan_type`) | "
        f"{plan_ok} / {plan_checked} |",
        f"| Option agrees (where AMFI publishes `option_type`) | "
        f"{opt_ok} / {opt_checked} |",
        f"| **Schemes with any discrepancy** | **{len(issues)}** |",
        "",
    ]

    if len(issues):
        lines += [
            f"### The {len(issues)} discrepancies",
            "",
            md_table(
                issues.head(40),
                ["rta", "rta_scheme_code", "rta_scheme_name",
                 "amfi_scheme_code", "amfi_scheme_nav_name", "issues"],
            ),
            "",
        ]
        if len(issues) > 40:
            lines += [f"_{len(issues) - 40} further rows in "
                      "`matched_schemes.csv`._", ""]
    else:
        lines += [
            "No discrepancies. Every matched scheme resolves to a real AMFI "
            "code, under the same AMC, with a plan and option consistent with "
            "the RTA scheme name.",
            "",
        ]

    lines += [
        "---",
        "",
        "## 6. Why the unmatched schemes did not match",
        "",
        md_table(reasons, ["reason_code", "schemes", "what it means"]),
        "",
        "Full per-scheme detail, including the parsed core name, plan, option "
        "and frequency for each, is in **`unmatched_schemes.csv`**.",
        "",
        "### What is and is not recoverable",
        "",
        "Many unmatched schemes are **not defects in the matcher**. Matured "
        "Fixed Term Plans, capital-protection series, segregated portfolios "
        "and retired Retail/Institutional share classes are removed from the "
        "AMFI master once they close, so no algorithm can find them. They need "
        "curated entries in `public.scheme_mapping_override` — data entry, not "
        "engineering.",
        "",
        "`FUND_NAME_NOT_IN_AMFI` is the bucket that rewards inspection, "
        "because it mixes two populations that look identical by name and "
        "must be handled in opposite ways:",
        "",
        "- **Rebrands and spelling drift are recoverable.** The fund is still "
        "in the master under a different string. A row in "
        "`public.scheme_name_alias` fixes the whole class at once rather than "
        "one scheme at a time — `RELIANCE` → `NIPPON INDIA` (AMC `RMF`) "
        "recovered 8 schemes, and `MID CAP` → `MIDCAP` another 3.",
        "- **Mergers are not mappable 1:1 and must not be aliased.** When a "
        "fund is merged away the surviving AMFI scheme is a *different* fund "
        "that absorbed it at a swap ratio, so its NAV series is not the RTA "
        "scheme's. HDFC Prudence, HDFC Balanced, the L&T book and the ABSL "
        "MIP plans all fall here. Their names invite an alias and their NAVs "
        "refute it. These belong in `public.scheme_mapping_override` with a "
        "NULL `amfi_scheme_code` — the positive assertion of absence — not "
        "pointed at the surviving scheme.",
        "",
        "The distinction is not decidable from names, so **NAV-verify every "
        "rename before configuring it**. `scheme_matching.nav_verify.classify` "
        "returns `CONTRADICTED` when AMFI publishes a different price on the "
        "dates the RTA reports, which is what a merger looks like and what a "
        "genuine rebrand never does. Note the asymmetry in evidence: "
        "`gold.scheme_nav` holds CAMS only, so every KFIN rename is "
        "unverifiable this way and rests on name evidence plus human review.",
        "",
        "`PLAN_OPTION_VARIANT_ABSENT` (the fund is present but this exact "
        "variant is not — worth checking whether the RTA name was parsed "
        "correctly) and `AMBIGUOUS_PENDING_REVIEW` (candidates exist and a "
        "reviewer can pick one) remain addressable as before.",
        "",
        "---",
        "",
        "## 7. Coverage by AMC (worst first)",
        "",
        md_table(by_amc, ["rta", "rta_amc_code", "amc_slug",
                          "total", "matched", "pct"]),
        "",
        "---",
        "",
        "## 8. Notes on the AMFI master restructure (2026-08-12)",
        "",
        "The master database was restored and `amfi_scheme_master` reshaped:",
        "",
        "- `name_norm` was replaced by `normalized_scheme_name`.",
        "- Plan and option moved into dedicated `plan_type` / `option_type` "
        "columns.",
        "",
        "`normalized_scheme_name` is **not** a drop-in replacement for "
        "`name_norm`: it holds the bare fund name with plan, option and "
        "frequency stripped, giving only 3,710 distinct values across 16,345 "
        "schemes. Matching against it collapses every Growth/IDCW and "
        "Daily/Weekly variant of a fund onto one string and drops coverage to "
        "145 / 515 (28%).",
        "",
        "`scheme_nav_name` is the true successor — it retains the full detail "
        "(16,308 distinct values) and restores coverage to "
        f"{n_match} / {total} ({pct}%). The pipeline now reads that column.",
        "",
    ]

    (OUT / "scheme_mapping_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
