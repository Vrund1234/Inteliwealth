"""
Export a CSV of UNMATCHED and PENDING_REVIEW schemes from the latest
scheme_mapping.py run, with NAV-comparison evidence and a best-effort
reason for why each one didn't map.

Read-only: does not touch bronze.scheme_mapping / audit / review.
Reproduces the same NAV sampling logic as Rule 3.5 in scheme_mapping.py
(top-3 most recent NAV dates per RTA scheme code from
bronze.transaction_master_new, cross-checked against public.nav_master)
purely for reporting.

Usage:
    venv/bin/python export_unmapped_pending_report.py [output_csv_path]
"""

import sys
import csv
from datetime import datetime

import pandas as pd
from rapidfuzz import process, fuzz
from sqlalchemy import text

from utils.db import engine, master_engine
from scheme_mapping import normalize_scheme_name


def main():
    out_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else f"../scheme_mapping_analysis/unmapped_pending_schemes_{datetime.now().date()}.csv"
    )

    # ------------------------------------------------------------------
    # 1. Rows to report: UNMATCHED + PENDING_REVIEW from the last run
    # ------------------------------------------------------------------
    mapping_df = pd.read_sql(
        """
        SELECT rta, rta_amc_code, rta_scheme_code, rta_scheme_name,
               mapping_status, mapping_source, mapping_confidence
        FROM bronze.scheme_mapping
        WHERE mapping_status IN ('UNMATCHED', 'PENDING_REVIEW')
        ORDER BY mapping_status, rta, rta_scheme_code
        """,
        engine,
    )
    print(f"Rows to report: {len(mapping_df)}")

    if mapping_df.empty:
        print("Nothing UNMATCHED or PENDING_REVIEW. No CSV written.")
        return

    codes = tuple(mapping_df["rta_scheme_code"].dropna().unique())

    # ------------------------------------------------------------------
    # 2. AMC mapping status (RTA amc_code -> AMFI amc_code)
    # ------------------------------------------------------------------
    amc_map_df = pd.read_sql(
        """
        SELECT
            r.rta,
            r.amc_code AS rta_amc_code,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM public.amfi_scheme_master a
                    WHERE a.amc_code = r.amc_code
                )
                THEN r.amc_code
                ELSE NULL
            END AS amfi_amc_code
        FROM public.rta_amc_code r
        WHERE r.is_deleted IS NOT TRUE
        """,
        master_engine,
        dtype=str,
    )
    amc_map = {
        (row.rta, row.rta_amc_code): row.amfi_amc_code
        for row in amc_map_df.itertuples()
    }

    # ------------------------------------------------------------------
    # 3. Audit trail: any structured-rule candidates ever raised for
    #    these codes (0 rows == the structured engine found nothing)
    # ------------------------------------------------------------------
    audit_df = pd.read_sql(
        text(
            """
            SELECT rta, rta_scheme_code, rule_name, confidence_score,
                   candidate_scheme_id
            FROM bronze.scheme_mapping_audit
            WHERE rta_scheme_code = ANY(:codes)
            """
        ),
        engine,
        params={"codes": list(codes)},
    )
    audit_by_code = {}
    for row in audit_df.itertuples():
        audit_by_code.setdefault((row.rta, row.rta_scheme_code), []).append(row)

    # ------------------------------------------------------------------
    # 4. Pending-review candidates (already computed by the run)
    # ------------------------------------------------------------------
    review_df = pd.read_sql(
        text(
            """
            SELECT rta, rta_scheme_code, candidate_rank, candidate_amfi_code,
                   candidate_amfi_name, candidate_score, rule_name
            FROM bronze.scheme_mapping_review
            WHERE rta_scheme_code = ANY(:codes) AND reviewer_decision IS NULL
            ORDER BY rta, rta_scheme_code, candidate_rank
            """
        ),
        engine,
        params={"codes": list(codes)},
    )
    review_by_code = {}
    for row in review_df.itertuples():
        review_by_code.setdefault((row.rta, row.rta_scheme_code), []).append(row)

    # ------------------------------------------------------------------
    # 5. NAV evidence — same sampling as Rule 3.5: dedup to one NAV per
    #    (rta, code, date), take the 3 most recent dates per scheme.
    # ------------------------------------------------------------------
    txn_nav_query = text(
        """
        SELECT source AS rta,
               prodcode AS rta_scheme_code,
               traddate::date AS nav_date,
               purprice::numeric AS nav
        FROM bronze.transaction_master_new
        WHERE prodcode = ANY(:codes)
          AND purprice IS NOT NULL
          AND TRIM(purprice) != ''
          AND purprice::numeric > 0
          AND traddate IS NOT NULL
        """
    )
    rta_nav_df = pd.read_sql(txn_nav_query, engine, params={"codes": list(codes)})

    total_nav_rows_by_code = (
        rta_nav_df.groupby(["rta", "rta_scheme_code"]).size().to_dict()
    )

    top3_by_code = {}
    nav_lookup_dict = {}
    if not rta_nav_df.empty:
        rta_nav_df = rta_nav_df.sort_values(
            ["rta", "rta_scheme_code", "nav_date", "nav"]
        ).drop_duplicates(subset=["rta", "rta_scheme_code", "nav_date"], keep="last")
        rta_nav_df["nav_round"] = rta_nav_df["nav"].round(4)
        rta_nav_df = rta_nav_df.sort_values("nav_date", ascending=False)

        top3_navs = rta_nav_df.groupby(["rta", "rta_scheme_code"]).head(3)
        for (rta, code), grp in top3_navs.groupby(["rta", "rta_scheme_code"]):
            top3_by_code[(rta, code)] = grp.sort_values(
                "nav_date", ascending=False
            )

        required_dates = list(
            pd.to_datetime(top3_navs["nav_date"]).dt.date.unique()
        )
        nav_master_query = text(
            """
            SELECT nm.scheme_code, nm.nav_date, ROUND(nm.nav, 4) AS nav_round
            FROM public.nav_master nm
            WHERE nm.nav_date = ANY(:dates)
            """
        )
        amfi_nav_df = pd.read_sql(
            nav_master_query, master_engine, params={"dates": required_dates}
        )
        amfi_nav_df["scheme_code_str"] = amfi_nav_df["scheme_code"].astype(str)
        nav_lookup_dict = (
            amfi_nav_df.groupby(["nav_date", "nav_round"])["scheme_code_str"]
            .apply(set)
            .to_dict()
        )

    # ------------------------------------------------------------------
    # 6. Name references for candidate codes (amfi_scheme_master + scheme_master)
    # ------------------------------------------------------------------
    # Unfiltered, matching the real script's amfi_df (no status/is_deleted
    # filter) — a NAV-fingerprint code only needs to exist here or in
    # scheme_master to be accepted as a match.
    amfi_names_df = pd.read_sql(
        """
        SELECT amfi_scheme_code::text AS code, scheme_nav_name AS name,
               amc_code, normalized_scheme_name, is_deleted
        FROM public.amfi_scheme_master
        """,
        master_engine,
    )
    amfi_names = dict(zip(amfi_names_df["code"], amfi_names_df["name"]))
    amfi_codes_all = set(amfi_names_df["code"])

    sm_names_df = pd.read_sql(
        """
        SELECT scheme_code::text AS code, name
        FROM public.scheme_master
        WHERE is_deleted = false
        """,
        master_engine,
    )
    sm_names = dict(zip(sm_names_df["code"], sm_names_df["name"]))

    def code_name(code):
        if code is None:
            return None
        code = str(code)
        return amfi_names.get(code) or sm_names.get(code)

    # Fuzzy reference match, informational only — scoped to the RTA's mapped
    # AMC when known, else searched across all *active* AMFI schemes.
    active_amfi_df = amfi_names_df[amfi_names_df["is_deleted"] == False].copy()
    active_amfi_df["name_norm"] = active_amfi_df["name"].apply(normalize_scheme_name)
    choices_by_amc = {
        amc: grp[["code", "name", "name_norm"]].reset_index(drop=True)
        for amc, grp in active_amfi_df.groupby("amc_code")
    }
    all_choices = active_amfi_df[["code", "name", "name_norm"]]

    def best_fuzzy(rta_name, amfi_amc_code):
        if not rta_name:
            return (None, None, None)
        norm = normalize_scheme_name(rta_name)
        pool = choices_by_amc.get(amfi_amc_code) if amfi_amc_code else None
        if pool is None or pool.empty:
            pool = all_choices
        match = process.extractOne(
            norm, pool["name_norm"].tolist(), scorer=fuzz.token_sort_ratio
        )
        if not match:
            return (None, None, None)
        _, score, pos = match
        row = pool.iloc[pos]
        return (row["code"], row["name"], round(score, 1))

    # ------------------------------------------------------------------
    # 7. Assemble rows
    # ------------------------------------------------------------------
    out_rows = []
    for row in mapping_df.itertuples():
        rta, code = row.rta, row.rta_scheme_code
        amfi_amc_code = amc_map.get((rta, row.rta_amc_code))
        amc_mapped = amfi_amc_code is not None

        sample = top3_by_code.get((rta, code))
        total_nav_rows = int(total_nav_rows_by_code.get((rta, code), 0))

        nav_dates_prices = ""
        nav_dates_compared = 0
        nav_dates_matched = 0
        per_date_sets = []

        if sample is not None:
            nav_dates_compared = len(sample)
            parts = []
            for s in sample.itertuples():
                d = s.nav_date
                p = s.nav
                matched_codes = nav_lookup_dict.get((d, round(s.nav, 4)), set())
                hit = len(matched_codes) > 0
                per_date_sets.append(matched_codes)
                if hit:
                    nav_dates_matched += 1
                parts.append(f"{d}={p}{'*' if hit else ''} ({len(matched_codes)} sch.)")
            nav_dates_prices = "; ".join(parts)

        # Mirror Rule 3.5 exactly: it only attempts a fingerprint match when
        # EVERY sampled date found at least one nav_master hit, then
        # intersects those per-date candidate sets. A generic price (e.g.
        # the common Rs.10 launch NAV) inflates a single date's candidate
        # set hugely, but the intersection across dates is what the real
        # rule actually uses — reporting the raw union instead would be
        # noisy and misleading.
        nav_rule_attempted = (
            nav_dates_compared >= 2 and nav_dates_matched == nav_dates_compared
        )
        nav_intersection = set.intersection(*per_date_sets) if nav_rule_attempted else set()
        if nav_rule_attempted and not nav_intersection:
            nav_fingerprint_outcome = (
                f"All {nav_dates_compared} sampled dates matched nav_master "
                f"individually, but no single scheme was common across all "
                f"of them (conflicting fingerprint — often because one "
                f"sampled price, e.g. Rs.10 launch NAV, is shared by many "
                f"unrelated schemes)."
            )
        elif nav_rule_attempted and len(nav_intersection) == 1:
            only = next(iter(nav_intersection))
            in_ref = only in amfi_codes_all or only in sm_names
            nav_fingerprint_outcome = (
                f"NAV fingerprint uniquely resolved to {only} - "
                f"{code_name(only)}, but that code is not present in "
                f"amfi_scheme_master or scheme_master (data gap)."
                if not in_ref
                else f"NAV fingerprint uniquely resolved to {only} - "
                f"{code_name(only)} (unexpected — worth a manual look, "
                f"since the run still left this UNMATCHED)."
            )
        elif nav_rule_attempted and len(nav_intersection) > 1:
            nav_fingerprint_outcome = (
                f"NAV fingerprint narrowed to {len(nav_intersection)} "
                f"candidate schemes (name-based disambiguation did not "
                f"single one out): "
                + "; ".join(
                    sorted(f"{c} - {code_name(c)}" for c in nav_intersection)
                )
            )
        elif nav_dates_compared >= 2:
            nav_fingerprint_outcome = (
                f"NAV rule not attempted — only {nav_dates_matched}/"
                f"{nav_dates_compared} sampled dates found any match in "
                f"nav_master (rule requires all of them to)."
            )
        else:
            nav_fingerprint_outcome = "N/A"

        audit_rows = audit_by_code.get((rta, code), [])
        review_rows = review_by_code.get((rta, code), [])

        fz_code, fz_name, fz_score = best_fuzzy(row.rta_scheme_name, amfi_amc_code)

        # ---- reason ----
        if row.mapping_status == "PENDING_REVIEW":
            reason = (
                f"Ambiguous exact-name match: {len(review_rows)} AMFI schemes "
                f"share the identical normalized name/key with equal (100) "
                f"confidence — engine could not pick one automatically; "
                f"needs manual review."
            )
        else:  # UNMATCHED
            reasons = []
            if not amc_mapped:
                reasons.append(
                    f"RTA AMC code '{row.rta_amc_code}' is not mapped to any "
                    f"AMC in public.amfi_scheme_master, so no structured "
                    f"name rule could even search for a candidate."
                )
            if len(audit_rows) == 0:
                reasons.append(
                    "No structured name-matching rule (exact/product/fuzzy) "
                    "produced any candidate."
                )
            if total_nav_rows == 0:
                reasons.append(
                    "No NAV/price history in bronze.transaction_master_new "
                    "for this scheme code — NAV-fingerprint rule (3.5) "
                    "could not run."
                )
            elif nav_dates_compared < 2:
                reasons.append(
                    f"Only {nav_dates_compared} distinct priced NAV date(s) "
                    f"available (fewer than the 2 required for NAV "
                    f"fingerprint matching)."
                )
            elif nav_dates_matched == 0:
                reasons.append(
                    f"{nav_dates_compared} NAV dates sampled, but none of "
                    f"the RTA prices matched any nav_master NAV on that "
                    f"date (stale/incorrect RTA price or wrong scheme)."
                )
            elif nav_fingerprint_outcome != "N/A":
                reasons.append(nav_fingerprint_outcome)
            reason = " | ".join(reasons) if reasons else "Unresolved (see evidence columns)."

        out_rows.append(
            {
                "mapping_status": row.mapping_status,
                "rta": rta,
                "rta_scheme_code": code,
                "rta_scheme_name": row.rta_scheme_name,
                "rta_amc_code": row.rta_amc_code,
                "amfi_amc_mapped": "Y" if amc_mapped else "N",
                "structured_rule_candidates_found": len(audit_rows),
                "total_nav_rows_in_transaction_master": total_nav_rows,
                "nav_dates_prices_compared": nav_dates_prices,
                "nav_dates_sampled_count": nav_dates_compared,
                "nav_dates_matched_in_nav_master": nav_dates_matched,
                "nav_fingerprint_result": (
                    "" if nav_fingerprint_outcome == "N/A" else nav_fingerprint_outcome
                ),
                "pending_review_candidates": (
                    "; ".join(
                        f"#{r.candidate_rank} {r.candidate_amfi_code} - "
                        f"{r.candidate_amfi_name} (score={r.candidate_score}, "
                        f"rule={r.rule_name})"
                        for r in review_rows
                    )
                    if review_rows
                    else ""
                ),
                "reason_not_matched": reason,
                "closest_amfi_name_reference_only": (
                    f"{fz_code} - {fz_name}" if fz_code else ""
                ),
                "closest_amfi_name_score_reference_only": fz_score,
            }
        )

    out_df = pd.DataFrame(out_rows)
    out_df.to_csv(out_path, index=False, quoting=csv.QUOTE_ALL)
    print(f"Wrote {len(out_df)} rows to {out_path}")
    print(
        out_df["mapping_status"].value_counts().to_string()
    )


if __name__ == "__main__":
    main()
