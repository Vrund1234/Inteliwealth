"""Export the scheme mapping as three reviewable CSVs.

  scheme_mapping_export_mapped.csv        — every mapped scheme, RTA vs AMFI side by side
  scheme_mapping_export_unmapped.csv      — every unmapped scheme, why, and its nearest candidate
  scheme_mapping_export_needs_review.csv  — everything a human should look at, most urgent first

Each row carries the RTA data and the AMFI data together so a reviewer can
compare without joining anything by hand. Verification columns say how strongly
each mapping is corroborated: `nav_verdict` is independent of every name signal
the matcher used, `name_similarity_pct` is not.
"""

import sys
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_scheme_mapping import REASONS, classify  # noqa: E402
from scheme_matching import nav_verify  # noqa: E402
from scheme_matching.aliases import build_alias_fn, load_aliases  # noqa: E402
from scheme_matching.reference import load_amc_map  # noqa: E402
from scheme_matching.scheme_key import parse_scheme_key  # noqa: E402
from utils.db import engine, master_engine  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "scheme_mapping_analysis"


def load():
    with engine.connect() as conn:
        mapping = pd.DataFrame(conn.execute(text("""
            SELECT mapping_id, scheme_id, rta, rta_amc_code, rta_scheme_code,
                   rta_scheme_name, normalized_scheme_name, amfi_scheme_code,
                   mapping_source, mapping_confidence, mapping_status
            FROM bronze.scheme_mapping ORDER BY rta, rta_scheme_code
        """)).fetchall(), columns=[
            "mapping_id", "scheme_id", "rta", "rta_amc_code", "rta_scheme_code",
            "rta_scheme_name", "rta_normalized_scheme_name", "amfi_scheme_code",
            "mapping_source", "mapping_confidence", "mapping_status"])

        activity = pd.DataFrame(conn.execute(text("""
            SELECT source AS rta, prodcode AS rta_scheme_code,
                   count(*) AS rta_txn_count,
                   count(DISTINCT folio_no) AS rta_folio_count,
                   min(traddate) AS rta_first_txn, max(traddate) AS rta_last_txn
            FROM silver.transaction_master_new
            WHERE NULLIF(TRIM(prodcode), '') IS NOT NULL
            GROUP BY 1, 2
        """)).fetchall(), columns=["rta", "rta_scheme_code", "rta_txn_count",
                                   "rta_folio_count", "rta_first_txn", "rta_last_txn"])

    with master_engine.connect() as conn:
        amfi = pd.DataFrame(conn.execute(text("""
            SELECT amfi_scheme_code, amc_code, amc_name, scheme_name,
                   scheme_nav_name, normalized_nav_name, plan_type, option_type,
                   scheme_category, sub_category, isin_growth, isin_idcw, status
            FROM public.amfi_scheme_master
        """)).fetchall(), columns=[
            "amfi_scheme_code", "amfi_amc_code", "amfi_amc_name", "amfi_scheme_name",
            "amfi_scheme_nav_name", "amfi_normalized_nav_name", "amfi_plan_type",
            "amfi_option_type", "amfi_scheme_category", "amfi_sub_category",
            "amfi_isin_growth", "amfi_isin_idcw", "amfi_status"])

    with engine.connect() as conn:
        review = pd.DataFrame(conn.execute(text("""
            SELECT rta, rta_scheme_code, candidate_rank, candidate_amfi_code,
                   candidate_amfi_name, candidate_score, rule_name
            FROM bronze.scheme_mapping_review ORDER BY rta, rta_scheme_code, candidate_rank
        """)).fetchall(), columns=["rta", "rta_scheme_code", "candidate_rank",
                                   "candidate_amfi_code", "candidate_amfi_name",
                                   "candidate_score", "rule_name"])
    return mapping, activity, amfi, review


def add_nav_verdicts(df):
    """CAMS only — gold.scheme_nav holds no KFIN rows, so KFIN is NO_EVIDENCE."""
    cams = df[df.rta.isin(nav_verify.RTAS_WITH_NAV) & df.amfi_scheme_code.notna()]
    fps = nav_verify.load_rta_fingerprints(
        engine, master_engine, cams.rta_scheme_code.tolist()
    )
    navs = nav_verify.load_amfi_navs(
        master_engine, {d for fp in fps.values() for d, _ in fp}
    )
    out = []
    for r in df.itertuples():
        if pd.isna(r.amfi_scheme_code):
            out.append({"nav_verdict": "", "nav_dates_checked": 0, "nav_matched": 0,
                        "nav_differed": 0, "nav_max_pct_diff": ""})
            continue
        fp = fps.get((r.rta, r.rta_scheme_code), [])
        v = nav_verify.classify(fp, r.amfi_scheme_code, navs)
        out.append({"nav_verdict": v["verdict"], "nav_dates_checked": len(fp),
                    "nav_matched": v["matched"], "nav_differed": v["differed"],
                    "nav_max_pct_diff": round(v["max_pct_diff"], 4)})
    return pd.DataFrame(out, index=df.index)


def confidence_tier(row):
    """How strongly is this mapping corroborated *independently* of the names?"""
    if row.nav_verdict == "CONTRADICTED":
        return "D - NAV CONTRADICTS, investigate"
    if row.nav_verdict in ("VERIFIED", "VERIFIED_LOW_PRECISION"):
        return "A - NAV corroborated"
    if row.nav_verdict == "WEAK":
        return "C - NAV partially agrees"
    if row.name_similarity_pct >= 90:
        return "B - name near-identical, uncorroborated"
    if row.name_similarity_pct >= 70:
        return "C - name plausible, uncorroborated"
    return "D - weak name, uncorroborated"


def main():
    mapping, activity, amfi, review = load()
    alias_fn = build_alias_fn(load_aliases(engine))
    amc_map = load_amc_map(master_engine)

    df = mapping.merge(
        amc_map[["rta", "rta_amc_code", "amfi_amc_code", "amc_slug"]],
        on=["rta", "rta_amc_code"], how="left",
    ).merge(activity, on=["rta", "rta_scheme_code"], how="left")

    df["scheme_key"] = [
        parse_scheme_key(r.rta_scheme_name, amc_code=r.amfi_amc_code, alias_fn=alias_fn)
        for r in df.itertuples()
    ]
    for field in ("core_name", "plan", "option", "frequency"):
        df[f"rta_parsed_{field}"] = [
            getattr(k, field) if k else None for k in df.scheme_key
        ]

    # ---- indexes the reason classifier and the candidate search both need ----
    key_index, core_index, by_amc = {}, {}, {}
    for r in amfi.itertuples():
        k = parse_scheme_key(r.amfi_scheme_nav_name, amc_code=r.amfi_amc_code,
                             alias_fn=alias_fn)
        if k is None:
            continue
        key_index.setdefault(k, []).append(r.amfi_scheme_code)
        core_index[(k.amc_code, k.core_name)] = core_index.get(
            (k.amc_code, k.core_name), 0) + 1
        by_amc.setdefault(r.amfi_amc_code, []).append(
            (k.core_name, r.amfi_scheme_code, r.amfi_scheme_nav_name))

    amfi_amcs = set(amfi.amfi_amc_code.dropna())
    amc_has_amfi = {(r.rta, r.rta_amc_code): (r.amfi_amc_code in amfi_amcs)
                    for r in amc_map.itertuples()}
    with engine.connect() as conn:
        review_codes = {
            (r[0], r[1]): int(r[2]) for r in conn.execute(text(
                "SELECT rta, rta_scheme_code, count(*) FROM bronze.scheme_mapping_review "
                "GROUP BY 1,2")).fetchall()
        }

    df = df.merge(amfi, on="amfi_scheme_code", how="left", suffixes=("", "_amfi"))
    df["name_similarity_pct"] = [
        round(fuzz.token_set_ratio(str(a).upper(), str(b).upper()), 1)
        if pd.notna(b) else 0
        for a, b in zip(df.rta_scheme_name, df.amfi_scheme_nav_name)
    ]
    df = pd.concat([df, add_nav_verdicts(df)], axis=1)

    df["amc_agrees"] = [
        "" if pd.isna(r.amfi_scheme_code) else str(r.amfi_amc_code) == str(r.amfi_amc_code_amfi)
        for r in df.itertuples()
    ]
    df["plan_agrees"] = [
        "" if pd.isna(r.amfi_plan_type) or not r.rta_parsed_plan
        else str(r.amfi_plan_type).upper() == r.rta_parsed_plan for r in df.itertuples()
    ]
    df["option_agrees"] = [
        "" if pd.isna(r.amfi_option_type) or not r.rta_parsed_option
        else str(r.amfi_option_type).upper() == r.rta_parsed_option for r in df.itertuples()
    ]

    shared = df[df.amfi_scheme_code.notna()].groupby("amfi_scheme_code").rta_scheme_code
    shared = shared.apply(lambda s: ", ".join(sorted(s)) if len(s) > 1 else "")
    df["shares_amfi_code_with"] = df.amfi_scheme_code.map(shared).fillna("")

    mapped = df[df.amfi_scheme_code.notna()].copy()
    mapped["confidence_tier"] = [confidence_tier(r) for r in mapped.itertuples()]

    unmapped = df[df.amfi_scheme_code.isna()].copy()
    cls = [classify(r, amfi, key_index, core_index, review_codes, amc_has_amfi)
           for r in unmapped.itertuples()]
    unmapped["reason_code"] = [c[0] for c in cls]
    unmapped["reason_detail"] = [c[1] for c in cls]
    unmapped["reason_description"] = unmapped.reason_code.map(REASONS)

    # Nearest AMFI name within the same AMC — a starting point for a reviewer,
    # explicitly NOT a proposed mapping.
    best = []
    for r in unmapped.itertuples():
        pool = by_amc.get(r.amfi_amc_code, [])
        if not pool or not r.rta_parsed_core_name:
            best.append(("", "", ""))
            continue
        scored = max(pool, key=lambda p: fuzz.token_sort_ratio(r.rta_parsed_core_name, p[0]))
        best.append((scored[1], scored[2],
                     round(fuzz.token_sort_ratio(r.rta_parsed_core_name, scored[0]), 1)))
    unmapped["nearest_amfi_code"] = [b[0] for b in best]
    unmapped["nearest_amfi_name"] = [b[1] for b in best]
    unmapped["nearest_amfi_score"] = [b[2] for b in best]

    RTA_COLS = ["rta", "rta_amc_code", "amc_slug", "rta_scheme_code", "rta_scheme_name",
                "rta_normalized_scheme_name", "rta_parsed_core_name", "rta_parsed_plan",
                "rta_parsed_option", "rta_parsed_frequency", "rta_txn_count",
                "rta_folio_count", "rta_first_txn", "rta_last_txn"]
    AMFI_COLS = ["amfi_scheme_code", "amfi_amc_code_amfi", "amfi_amc_name",
                 "amfi_scheme_name", "amfi_scheme_nav_name", "amfi_normalized_nav_name",
                 "amfi_plan_type", "amfi_option_type", "amfi_scheme_category",
                 "amfi_sub_category", "amfi_isin_growth", "amfi_isin_idcw", "amfi_status"]
    MAP_COLS = ["mapping_status", "mapping_source", "mapping_confidence",
                "mapping_id", "scheme_id"]
    CHECK_COLS = ["name_similarity_pct", "amc_agrees", "plan_agrees", "option_agrees",
                  "nav_verdict", "nav_dates_checked", "nav_matched", "nav_differed",
                  "nav_max_pct_diff", "confidence_tier", "shares_amfi_code_with"]

    mapped_out = mapped[RTA_COLS + AMFI_COLS + MAP_COLS + CHECK_COLS].rename(
        columns={"amfi_amc_code_amfi": "amfi_amc_code"})
    mapped_out = mapped_out.sort_values(["confidence_tier", "rta", "rta_scheme_code"])
    mapped_out.to_csv(OUT / "scheme_mapping_export_mapped.csv", index=False)

    unmapped_out = unmapped[RTA_COLS + MAP_COLS + [
        "reason_code", "reason_detail", "reason_description",
        "nearest_amfi_code", "nearest_amfi_name", "nearest_amfi_score"]]
    unmapped_out = unmapped_out.sort_values(["reason_code", "rta", "rta_scheme_code"])
    unmapped_out.to_csv(OUT / "scheme_mapping_export_unmapped.csv", index=False)

    # ---------------- needs human review ----------------
    rows = []

    for r in mapped.itertuples():
        reasons = []
        if r.nav_verdict == "CONTRADICTED":
            reasons.append("NAV contradicts this AMFI code")
        if r.confidence_tier.startswith("D"):
            reasons.append("weak/contradicted evidence")
        if r.shares_amfi_code_with:
            reasons.append(f"AMFI code shared with {r.shares_amfi_code_with}")
        if r.plan_agrees is False:
            reasons.append("plan disagrees with AMFI plan_type")
        if r.option_agrees is False:
            reasons.append("option disagrees with AMFI option_type")
        if r.name_similarity_pct < 70:
            reasons.append(f"low name similarity ({r.name_similarity_pct}%)")
        if not reasons:
            continue
        priority = 1 if r.nav_verdict == "CONTRADICTED" else (
            2 if r.confidence_tier.startswith("D") else 3)
        rows.append({
            "priority": priority, "review_type": "MAPPED - verify",
            "rta": r.rta, "rta_scheme_code": r.rta_scheme_code,
            "rta_scheme_name": r.rta_scheme_name,
            "rta_txn_count": r.rta_txn_count, "rta_folio_count": r.rta_folio_count,
            "current_amfi_code": r.amfi_scheme_code,
            "current_amfi_name": r.amfi_scheme_nav_name,
            "candidate_amfi_code": "", "candidate_amfi_name": "", "candidate_score": "",
            "mapping_source": r.mapping_source, "mapping_confidence": r.mapping_confidence,
            "nav_verdict": r.nav_verdict, "name_similarity_pct": r.name_similarity_pct,
            "confidence_tier": r.confidence_tier,
            "review_reason": "; ".join(reasons),
        })

    pending = unmapped[unmapped.mapping_status == "PENDING_REVIEW"]
    cand = review.set_index(["rta", "rta_scheme_code"])
    for r in pending.itertuples():
        key = (r.rta, r.rta_scheme_code)
        cands = cand.loc[[key]] if key in cand.index else pd.DataFrame()
        if cands.empty:
            cands = pd.DataFrame([{"candidate_amfi_code": "", "candidate_amfi_name": "",
                                   "candidate_score": "", "rule_name": ""}])
        for cr in cands.itertuples():
            rows.append({
                "priority": 2, "review_type": "PENDING_REVIEW - pick a candidate",
                "rta": r.rta, "rta_scheme_code": r.rta_scheme_code,
                "rta_scheme_name": r.rta_scheme_name,
                "rta_txn_count": r.rta_txn_count, "rta_folio_count": r.rta_folio_count,
                "current_amfi_code": "", "current_amfi_name": "",
                "candidate_amfi_code": getattr(cr, "candidate_amfi_code", ""),
                "candidate_amfi_name": getattr(cr, "candidate_amfi_name", ""),
                "candidate_score": getattr(cr, "candidate_score", ""),
                "mapping_source": getattr(cr, "rule_name", ""), "mapping_confidence": "",
                "nav_verdict": "", "name_similarity_pct": "",
                "confidence_tier": "", "review_reason": r.reason_detail,
            })

    recoverable = {"PLAN_OPTION_VARIANT_ABSENT", "FUND_NAME_NOT_IN_AMFI"}
    for r in unmapped[unmapped.reason_code.isin(recoverable)].itertuples():
        rows.append({
            "priority": 4, "review_type": "UNMAPPED - possibly recoverable",
            "rta": r.rta, "rta_scheme_code": r.rta_scheme_code,
            "rta_scheme_name": r.rta_scheme_name,
            "rta_txn_count": r.rta_txn_count, "rta_folio_count": r.rta_folio_count,
            "current_amfi_code": "", "current_amfi_name": "",
            "candidate_amfi_code": r.nearest_amfi_code,
            "candidate_amfi_name": r.nearest_amfi_name,
            "candidate_score": r.nearest_amfi_score,
            "mapping_source": "", "mapping_confidence": "",
            "nav_verdict": "", "name_similarity_pct": "",
            "confidence_tier": "", "review_reason": f"{r.reason_code}: {r.reason_detail}",
        })

    needs = pd.DataFrame(rows).sort_values(
        ["priority", "rta", "rta_scheme_code"], kind="stable")
    needs.to_csv(OUT / "scheme_mapping_export_needs_review.csv", index=False)

    print(f"mapped        : {len(mapped_out):3d} -> scheme_mapping_export_mapped.csv")
    print(f"unmapped      : {len(unmapped_out):3d} -> scheme_mapping_export_unmapped.csv")
    print(f"needs review  : {len(needs):3d} -> scheme_mapping_export_needs_review.csv")
    print(f"\nreconciles: {len(mapped_out)} + {len(unmapped_out)} = "
          f"{len(mapped_out) + len(unmapped_out)} (total RTA schemes {len(df)})")
    print("\nmapped by confidence tier:")
    print(mapped_out.confidence_tier.value_counts().sort_index().to_string())
    print("\nunmapped by reason:")
    print(unmapped_out.reason_code.value_counts().to_string())
    print("\nreview queue by type:")
    print(needs.review_type.value_counts().to_string())


if __name__ == "__main__":
    main()
