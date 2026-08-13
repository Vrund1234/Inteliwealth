"""
Verify scheme_mapping table accuracy.
Produces:
  1. Summary statistics (mapped vs unmapped, by rule)
  2. CSV exports: mapped results, unmapped results, suspicious mappings
  3. Cross-validation against AMFI master for AMC consistency
"""

import pandas as pd
import os
from sqlalchemy import text
from utils.db import engine, master_engine

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "scheme_mapping_analysis")
os.makedirs(OUT_DIR, exist_ok=True)


def main():
    print("=" * 80)
    print("SCHEME MAPPING VERIFICATION")
    print("=" * 80)

    # ------------------------------------------------------------------
    # 1. Load scheme_mapping table
    # ------------------------------------------------------------------
    mapping_df = pd.read_sql("""
        SELECT
            mapping_id,
            scheme_id,
            rta,
            rta_amc_code,
            rta_scheme_code,
            rta_scheme_name,
            normalized_scheme_name,
            amfi_scheme_code,
            mapping_source,
            mapping_confidence,
            mapping_status
        FROM bronze.scheme_mapping
        ORDER BY rta, rta_scheme_code;
    """, engine)

    total = len(mapping_df)
    print(f"\nTotal rows in bronze.scheme_mapping: {total}")

    # ------------------------------------------------------------------
    # 2. Mapped vs Unmapped breakdown
    # ------------------------------------------------------------------
    mapped_mask = mapping_df["amfi_scheme_code"].notna()
    mapped_df = mapping_df[mapped_mask].copy()
    unmapped_df = mapping_df[~mapped_mask].copy()

    print(f"\n{'─' * 50}")
    print(f"  MAPPED   : {len(mapped_df):>6}  ({len(mapped_df)*100/total:.1f}%)")
    print(f"  UNMAPPED : {len(unmapped_df):>6}  ({len(unmapped_df)*100/total:.1f}%)")
    print(f"{'─' * 50}")

    # ------------------------------------------------------------------
    # 3. Breakdown by mapping_source
    # ------------------------------------------------------------------
    source_counts = (
        mapping_df["mapping_source"]
        .fillna("UNMAPPED (no source)")
        .value_counts()
        .reset_index()
    )
    source_counts.columns = ["mapping_source", "count"]
    print("\n  MAPPING SOURCE BREAKDOWN:")
    for _, r in source_counts.iterrows():
        pct = r["count"] * 100 / total
        print(f"    {r['mapping_source']:<30s} {r['count']:>6}  ({pct:.1f}%)")

    # ------------------------------------------------------------------
    # 4. Breakdown by mapping_status
    # ------------------------------------------------------------------
    status_counts = (
        mapping_df["mapping_status"]
        .fillna("NULL")
        .value_counts()
        .reset_index()
    )
    status_counts.columns = ["mapping_status", "count"]
    print("\n  MAPPING STATUS BREAKDOWN:")
    for _, r in status_counts.iterrows():
        pct = r["count"] * 100 / total
        print(f"    {r['mapping_status']:<30s} {r['count']:>6}  ({pct:.1f}%)")

    # ------------------------------------------------------------------
    # 5. Load AMFI master for cross-validation
    # ------------------------------------------------------------------
    amfi_df = pd.read_sql("""
        SELECT
            amfi_scheme_code,
            amc_code AS amfi_amc_code,
            scheme_nav_name,
            status AS amfi_status
        FROM public.amfi_scheme_master;
    """, master_engine)

    print(f"\n  AMFI master schemes loaded: {len(amfi_df)}")

    # ------------------------------------------------------------------
    # 6. Cross-validate mapped rows against AMFI master
    # ------------------------------------------------------------------
    # Ensure type consistency for join
    mapped_df["amfi_scheme_code"] = mapped_df["amfi_scheme_code"].astype(str).str.strip()
    amfi_df["amfi_scheme_code"] = amfi_df["amfi_scheme_code"].astype(str).str.strip()

    validated_df = mapped_df.merge(
        amfi_df,
        on="amfi_scheme_code",
        how="left"
    )

    # 6a. Rows whose AMFI code doesn't exist in master
    orphan_amfi = validated_df[validated_df["scheme_nav_name"].isna()]
    print(f"\n  Mapped rows with INVALID AMFI code (not in master): {len(orphan_amfi)}")

    # 6b. Load RTA->AMC slug mapping for AMC cross-check
    amc_map_df = pd.read_sql("""
        SELECT rta, amc_code AS rta_amc_code, amc_slug
        FROM public.rta_amc_code;
    """, master_engine)

    # Also get AMFI amc_code -> amc_slug mapping
    amfi_amc_df = amfi_df[["amfi_scheme_code", "amfi_amc_code"]].drop_duplicates()

    # Merge RTA AMC slug
    validated_df = validated_df.merge(
        amc_map_df[["rta", "rta_amc_code", "amc_slug"]].drop_duplicates(),
        on=["rta", "rta_amc_code"],
        how="left"
    )

    # Check AMC mismatch: rta_amc_code maps to amc_slug, but the AMFI scheme
    # belongs to a different AMC
    amc_mismatch = validated_df[
        validated_df["amfi_amc_code"].notna()
        & validated_df["amc_slug"].notna()
        & (validated_df["amfi_amc_code"] != validated_df["amc_slug"])
    ]
    print(f"  Mapped rows with AMC MISMATCH (RTA AMC ≠ AMFI AMC): {len(amc_mismatch)}")

    # ------------------------------------------------------------------
    # 7. Confidence distribution
    # ------------------------------------------------------------------
    print("\n  CONFIDENCE DISTRIBUTION (mapped rows):")
    conf_dist = (
        mapped_df["mapping_confidence"]
        .fillna(-1)
        .astype(int)
        .value_counts()
        .sort_index(ascending=False)
    )
    for conf, cnt in conf_dist.items():
        label = "N/A" if conf == -1 else str(conf)
        print(f"    Confidence {label:>4s}: {cnt:>6}")

    # ------------------------------------------------------------------
    # 8. Distinct RTA scheme codes breakdown (deduplicated view)
    # ------------------------------------------------------------------
    distinct_rta = mapping_df.drop_duplicates(subset=["rta", "rta_scheme_code"])
    distinct_mapped = distinct_rta[distinct_rta["amfi_scheme_code"].notna()]
    distinct_unmapped = distinct_rta[distinct_rta["amfi_scheme_code"].isna()]

    print(f"\n  DISTINCT RTA SCHEME CODES:")
    print(f"    Total    : {len(distinct_rta)}")
    print(f"    Mapped   : {len(distinct_mapped):>6}  ({len(distinct_mapped)*100/len(distinct_rta):.1f}%)")
    print(f"    Unmapped : {len(distinct_unmapped):>6}  ({len(distinct_unmapped)*100/len(distinct_rta):.1f}%)")

    # ------------------------------------------------------------------
    # 9. Export CSVs
    # ------------------------------------------------------------------

    # 9a. Mapped results
    mapped_export = mapped_df[[
        "rta", "rta_amc_code", "rta_scheme_code", "rta_scheme_name",
        "amfi_scheme_code", "mapping_source", "mapping_confidence", "mapping_status"
    ]].sort_values(["rta", "rta_scheme_code"])

    mapped_path = os.path.join(OUT_DIR, "verified_mapped_schemes.csv")
    mapped_export.to_csv(mapped_path, index=False)
    print(f"\n  ✅ Mapped results exported to: {mapped_path}")

    # 9b. Unmapped results
    unmapped_export = unmapped_df[[
        "rta", "rta_amc_code", "rta_scheme_code", "rta_scheme_name",
        "normalized_scheme_name", "mapping_source", "mapping_status"
    ]].sort_values(["rta", "rta_scheme_code"])

    unmapped_path = os.path.join(OUT_DIR, "verified_unmapped_schemes.csv")
    unmapped_export.to_csv(unmapped_path, index=False)
    print(f"  ❌ Unmapped results exported to: {unmapped_path}")

    # 9c. AMC mismatch (suspicious)
    if not amc_mismatch.empty:
        mismatch_export = amc_mismatch[[
            "rta", "rta_amc_code", "rta_scheme_code", "rta_scheme_name",
            "amfi_scheme_code", "scheme_nav_name", "amc_slug", "amfi_amc_code",
            "mapping_source", "mapping_confidence"
        ]].sort_values(["rta", "rta_scheme_code"])

        mismatch_path = os.path.join(OUT_DIR, "verified_amc_mismatches.csv")
        mismatch_export.to_csv(mismatch_path, index=False)
        print(f"  ⚠️  AMC mismatches exported to: {mismatch_path}")

    # 9d. Orphan AMFI codes
    if not orphan_amfi.empty:
        orphan_export = orphan_amfi[[
            "rta", "rta_amc_code", "rta_scheme_code", "rta_scheme_name",
            "amfi_scheme_code", "mapping_source", "mapping_confidence"
        ]].sort_values(["rta", "rta_scheme_code"])

        orphan_path = os.path.join(OUT_DIR, "verified_orphan_amfi_codes.csv")
        orphan_export.to_csv(orphan_path, index=False)
        print(f"  ⚠️  Orphan AMFI codes exported to: {orphan_path}")

    # ------------------------------------------------------------------
    # 10. Sample of unmapped for quick inspection
    # ------------------------------------------------------------------
    print(f"\n{'=' * 80}")
    print("SAMPLE UNMAPPED SCHEMES (first 30)")
    print(f"{'=' * 80}")
    if not unmapped_df.empty:
        sample = unmapped_df.head(30)[
            ["rta", "rta_amc_code", "rta_scheme_code", "rta_scheme_name", "mapping_status"]
        ]
        print(sample.to_string(index=False))
    else:
        print("  (none)")

    # ------------------------------------------------------------------
    # 11. Sample of AMC mismatches for quick inspection
    # ------------------------------------------------------------------
    if not amc_mismatch.empty:
        print(f"\n{'=' * 80}")
        print("SAMPLE AMC MISMATCHES (first 20)")
        print(f"{'=' * 80}")
        sample_mm = amc_mismatch.head(20)[
            ["rta", "rta_scheme_code", "rta_scheme_name",
             "amfi_scheme_code", "scheme_nav_name",
             "amc_slug", "amfi_amc_code", "mapping_source"]
        ]
        print(sample_mm.to_string(index=False))

    print(f"\n{'=' * 80}")
    print("VERIFICATION COMPLETE")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
