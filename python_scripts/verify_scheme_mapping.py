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
    """Gate 2. CAMS only — gold.scheme_nav has no KFIN rows.

    Uses nav_verify.classify() rather than the strict verify() so that
    "AMFI has no opinion for this date" (NO_EVIDENCE) and "one stale RTA
    row alongside otherwise-exact dates" (WEAK) are told apart from a real
    conflict (CONTRADICTED) — collapsing all three into one boolean is what
    made the old gate noisy.
    """
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
        outcome = nav_verify.classify(fingerprint, row.amfi_scheme_code, amfi_navs)
        results.append({
            "rta": row.rta,
            "rta_scheme_code": row.rta_scheme_code,
            "rta_scheme_name": row.rta_scheme_name,
            "amfi_scheme_code": row.amfi_scheme_code,
            "mapping_source": row.mapping_source,
            "nav_dates_available": len(fingerprint),
            "nav_matched": outcome["matched"],
            "nav_missing": outcome["missing"],
            "nav_differed": outcome["differed"],
            "nav_verdict": outcome["verdict"],
            "nav_max_pct_diff": outcome["max_pct_diff"],
        })

    audit = pd.DataFrame(results)
    audit.to_csv(OUT_DIR / "phase2_nav_audit.csv", index=False)

    counts = audit.nav_verdict.value_counts()
    verified = int(counts.get("VERIFIED", 0))
    low_precision = int(counts.get("VERIFIED_LOW_PRECISION", 0))
    weak = int(counts.get("WEAK", 0))
    no_evidence = int(counts.get("NO_EVIDENCE", 0))
    contradicted_rows = audit[audit.nav_verdict == "CONTRADICTED"]

    print(
        f"[NAV AUDIT] {len(audit)} new CAMS matches | "
        f"verified {verified} | verified(low precision) {low_precision} | "
        f"weak {weak} | "
        f"no evidence {no_evidence} | contradicted {len(contradicted_rows)}"
    )
    if not contradicted_rows.empty:
        print("[NAV AUDIT] CONTRADICTED — these must be investigated:")
        print(contradicted_rows.to_string(index=False))
    return audit


def gate_kfin_review(new):
    """Gate 3. KFIN cannot be NAV-verified, so it goes to a human."""
    kfin = new[new.rta == "KFIN"].copy()
    amfi = pd.read_sql(
        # scheme_nav_name is the successor to the old name_norm; the newer
        # normalized_scheme_name column drops plan/option/frequency detail.
        "SELECT amfi_scheme_code, scheme_nav_name AS name_norm "
        "FROM public.amfi_scheme_master",
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
    """Gate 4. Several RTA codes on one AMFI code, graded by NAV.

    A shared code is not automatically wrong — two share classes of one fund
    are one portfolio with one NAV series and one AMFI code. Grading each
    group keeps a genuine error (WRONG) from being buried among the
    legitimate ones, which is what a flat list of "collisions" does.
    """
    matched = current[current.amfi_scheme_code.notna()]
    counts = matched.groupby("amfi_scheme_code").rta_scheme_code.nunique()
    collisions = counts[counts > 1]
    if collisions.empty:
        print("[COLLISIONS] none")
        return pd.DataFrame()

    members = matched[matched.amfi_scheme_code.isin(collisions.index)]
    cams = members[members.rta.isin(nav_verify.RTAS_WITH_NAV)]
    fingerprints = nav_verify.load_rta_fingerprints(
        engine, master_engine, cams.rta_scheme_code.tolist()
    )
    amfi_navs = nav_verify.load_amfi_navs(
        master_engine, {d for fp in fingerprints.values() for d, _ in fp}
    )

    rows = []
    for amfi_code in collisions.index:
        dupes = members[members.amfi_scheme_code == amfi_code]
        verdicts = [
            nav_verify.classify(
                fingerprints.get((d.rta, d.rta_scheme_code), []),
                amfi_code,
                amfi_navs,
            )["verdict"]
            for d in dupes.itertuples()
        ]
        grade = nav_verify.classify_collision(verdicts)
        for d, verdict in zip(dupes.itertuples(), verdicts):
            rows.append({
                "amfi_scheme_code": amfi_code,
                "grade": grade,
                "rta": d.rta,
                "rta_scheme_code": d.rta_scheme_code,
                "rta_scheme_name": d.rta_scheme_name,
                "mapping_source": d.mapping_source,
                "nav_verdict": verdict,
            })

    report = pd.DataFrame(rows)
    report.to_csv(OUT_DIR / "phase2_collisions.csv", index=False)

    tally = report.drop_duplicates("amfi_scheme_code").grade.value_counts()
    print(
        f"[COLLISIONS] {len(collisions)} AMFI codes shared by multiple RTA codes | "
        + " | ".join(f"{k.lower()} {v}" for k, v in tally.items())
    )
    for grade, label in (
        (nav_verify.COLLISION_WRONG, "WRONG — a member's NAV contradicts this code"),
        (nav_verify.COLLISION_UNVERIFIED, "UNVERIFIED — needs human sign-off"),
        (nav_verify.COLLISION_SAME_FUND, "SAME_FUND — one fund, shared code, correct"),
    ):
        group = report[report.grade == grade]
        if group.empty:
            continue
        print(f"  {label}:")
        for amfi_code, g in group.groupby("amfi_scheme_code"):
            print(f"    AMFI {amfi_code}:")
            for d in g.itertuples():
                print(
                    f"      [{d.rta}] {d.rta_scheme_code} -> "
                    f"{d.rta_scheme_name} (nav={d.nav_verdict})"
                )
    return report


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
