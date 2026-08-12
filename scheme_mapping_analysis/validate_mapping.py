"""
Comprehensive Scheme Mapping Validation Script
================================================
Validates data in bronze.scheme_mapping against public.amfi_scheme_master
to identify mapping issues, inconsistencies, and data quality problems.
"""

import os
import sys
from pathlib import Path

import pandas as pd

# Connections come from python_scripts/utils/db.py — the single place the
# database names and credentials are defined. Duplicating them here meant a
# second copy to update whenever the master database is swapped, and a second
# copy of the password in the repository.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python_scripts"))

from utils.db import engine as project_engine  # noqa: E402
from utils.db import master_engine  # noqa: E402

OUTPUT = []

def log(msg=""):
    print(msg)
    OUTPUT.append(str(msg))

def separator(title=""):
    log("=" * 90)
    if title:
        log(f"  {title}")
        log("=" * 90)

# ═══════════════════════════════════════════════════════════════
# 1. Load data
# ═══════════════════════════════════════════════════════════════
separator("1. LOADING DATA")

mapping_df = pd.read_sql("SELECT * FROM bronze.scheme_mapping", project_engine)
log(f"Total rows in bronze.scheme_mapping: {len(mapping_df)}")
log(f"Columns: {list(mapping_df.columns)}")

amfi_df = pd.read_sql("SELECT * FROM public.amfi_scheme_master", master_engine)
log(f"Total rows in public.amfi_scheme_master: {len(amfi_df)}")
log(f"Columns: {list(amfi_df.columns)}")

# Also load rta_amc_code mapping
try:
    rta_amc_df = pd.read_sql("SELECT * FROM public.rta_amc_code", master_engine)
    log(f"Total rows in public.rta_amc_code: {len(rta_amc_df)}")
except Exception as e:
    rta_amc_df = pd.DataFrame()
    log(f"Could not load rta_amc_code: {e}")

# ═══════════════════════════════════════════════════════════════
# 2. Basic Statistics
# ═══════════════════════════════════════════════════════════════
separator("2. MAPPING OVERVIEW STATISTICS")

total = len(mapping_df)
mapped = mapping_df[mapping_df["amfi_scheme_code"].notna()].shape[0]
unmapped = mapping_df[mapping_df["amfi_scheme_code"].isna()].shape[0]

log(f"Total schemes in mapping table : {total}")
log(f"Successfully mapped            : {mapped} ({mapped/total*100:.1f}%)")
log(f"Unmapped (NULL amfi_scheme_code): {unmapped} ({unmapped/total*100:.1f}%)")

log("\n--- Mapping Source Distribution ---")
source_counts = mapping_df["mapping_source"].value_counts(dropna=False)
for source, count in source_counts.items():
    source_label = source if pd.notna(source) else "UNMAPPED (NULL)"
    log(f"  {source_label:25s} : {count:5d}  ({count/total*100:.1f}%)")

log("\n--- Mapping Confidence Distribution ---")
conf_counts = mapping_df["mapping_confidence"].value_counts(dropna=False)
for conf, count in conf_counts.items():
    conf_label = str(conf) if pd.notna(conf) else "NULL (unmapped)"
    log(f"  Confidence {conf_label:>6s} : {count:5d}")

# ═══════════════════════════════════════════════════════════════
# 3. Validate mapped AMFI codes exist in master
# ═══════════════════════════════════════════════════════════════
separator("3. ORPHAN CHECK: Mapped AMFI codes NOT in amfi_scheme_master")

mapped_codes = set(mapping_df["amfi_scheme_code"].dropna().unique())
master_codes = set(amfi_df["amfi_scheme_code"].astype(str).unique())

# Normalise both to strings for comparison
mapped_codes_str = {str(c) for c in mapped_codes}

orphan_codes = mapped_codes_str - master_codes
log(f"Mapped AMFI codes in scheme_mapping  : {len(mapped_codes_str)}")
log(f"AMFI codes in amfi_scheme_master      : {len(master_codes)}")
log(f"Orphan codes (in mapping but NOT master): {len(orphan_codes)}")

if orphan_codes:
    log("\n⚠  ORPHAN AMFI CODES (mapped to non-existent master records):")
    orphan_rows = mapping_df[mapping_df["amfi_scheme_code"].astype(str).isin(orphan_codes)]
    for _, row in orphan_rows.head(30).iterrows():
        log(f"  Code={row['amfi_scheme_code']}, RTA={row['rta']}, "
            f"Scheme={row.get('rta_scheme_name','')[:60]}, "
            f"Source={row['mapping_source']}")
else:
    log("✅ All mapped AMFI codes exist in amfi_scheme_master.")

# ═══════════════════════════════════════════════════════════════
# 4. Check for INACTIVE scheme mappings
# ═══════════════════════════════════════════════════════════════
separator("4. ACTIVE vs INACTIVE SCHEME STATUS CHECK")

# The mapping script only queries ACTIVE schemes from AMFI master.
# But after mapping, some schemes may have become inactive.
amfi_status = amfi_df[["amfi_scheme_code", "status"]].drop_duplicates()
amfi_status["amfi_scheme_code"] = amfi_status["amfi_scheme_code"].astype(str)

merged = mapping_df[mapping_df["amfi_scheme_code"].notna()].copy()
merged["amfi_scheme_code"] = merged["amfi_scheme_code"].astype(str)
merged = merged.merge(amfi_status, on="amfi_scheme_code", how="left")

if "status" in merged.columns:
    status_dist = merged["status"].value_counts(dropna=False)
    log("Status of mapped AMFI schemes:")
    for s, c in status_dist.items():
        s_label = s if pd.notna(s) else "NOT FOUND IN MASTER"
        log(f"  {s_label:25s} : {c}")
    
    inactive = merged[merged["status"] != "ACTIVE"]
    if len(inactive) > 0:
        log(f"\n⚠  {len(inactive)} mapping(s) point to NON-ACTIVE schemes:")
        for _, row in inactive.head(20).iterrows():
            log(f"  AMFI={row['amfi_scheme_code']}, Status={row.get('status')}, "
                f"RTA={row['rta']}, "
                f"Scheme={row.get('rta_scheme_name','')[:60]}")
else:
    log("Could not determine AMFI status — 'status' column not found.")

# ═══════════════════════════════════════════════════════════════
# 5. AMC Mismatch Detection (CRITICAL)
# ═══════════════════════════════════════════════════════════════
separator("5. AMC MISMATCH CHECK (scheme mapped to WRONG AMC)")

amfi_amc = amfi_df[["amfi_scheme_code", "amc_code"]].drop_duplicates()
amfi_amc["amfi_scheme_code"] = amfi_amc["amfi_scheme_code"].astype(str)
amfi_amc.rename(columns={"amc_code": "amfi_amc_code"}, inplace=True)

amc_check = mapping_df[mapping_df["amfi_scheme_code"].notna()].copy()
amc_check["amfi_scheme_code"] = amc_check["amfi_scheme_code"].astype(str)

# Get the AMC slug for each RTA row from the rta_amc_code table
if not rta_amc_df.empty:
    rta_slug = rta_amc_df[["rta", "amc_code", "amc_slug"]].rename(
        columns={"amc_code": "rta_amc_code"}
    )
    amc_check = amc_check.merge(rta_slug, on=["rta", "rta_amc_code"], how="left")
    amc_check = amc_check.merge(amfi_amc, on="amfi_scheme_code", how="left")
    
    # Compare amc_slug (from RTA) with amfi_amc_code (from AMFI master)
    if "amc_slug" in amc_check.columns and "amfi_amc_code" in amc_check.columns:
        mismatch = amc_check[
            amc_check["amc_slug"].notna() &
            amc_check["amfi_amc_code"].notna() &
            (amc_check["amc_slug"] != amc_check["amfi_amc_code"])
        ]
        
        log(f"Rows with AMC slug available: {amc_check['amc_slug'].notna().sum()}")
        log(f"AMC MISMATCHES detected     : {len(mismatch)}")
        
        if len(mismatch) > 0:
            log("\n🚨 CRITICAL: Schemes mapped to WRONG AMC!")
            for _, row in mismatch.head(30).iterrows():
                log(f"  RTA={row['rta']}, Code={row['rta_scheme_code']}, "
                    f"RTA_AMC={row['amc_slug']}, AMFI_AMC={row['amfi_amc_code']}, "
                    f"Source={row['mapping_source']}, "
                    f"Scheme={row.get('rta_scheme_name','')[:50]}")
        else:
            log("✅ No AMC mismatches — all mapped schemes belong to the correct AMC.")
    else:
        log("Could not perform AMC mismatch check (missing columns).")
else:
    log("⚠  rta_amc_code table not available — skipping AMC mismatch check.")

# ═══════════════════════════════════════════════════════════════
# 6. Duplicate AMFI code mapping check
# ═══════════════════════════════════════════════════════════════
separator("6. DUPLICATE AMFI CODE MAPPING CHECK")

mapped_only = mapping_df[mapping_df["amfi_scheme_code"].notna()].copy()
dup_amfi = mapped_only["amfi_scheme_code"].value_counts()
dups = dup_amfi[dup_amfi > 1]

log(f"Unique mapped AMFI codes: {len(dup_amfi)}")
log(f"AMFI codes mapped to by >1 RTA scheme: {len(dups)}")

if len(dups) > 0:
    log("\nAMFI codes with multiple RTA schemes mapped to them:")
    for code, count in dups.head(20).items():
        rows = mapped_only[mapped_only["amfi_scheme_code"] == code]
        log(f"\n  AMFI Code: {code} (mapped by {count} RTA schemes)")
        for _, r in rows.iterrows():
            log(f"    RTA={r['rta']}, Code={r['rta_scheme_code']}, "
                f"Scheme={r.get('rta_scheme_name','')[:60]}, "
                f"Source={r['mapping_source']}")
else:
    log("✅ Each AMFI code maps to exactly one RTA scheme (no collisions).")

# ═══════════════════════════════════════════════════════════════
# 7. Duplicate RTA scheme code check (primary key violation)
# ═══════════════════════════════════════════════════════════════
separator("7. DUPLICATE RTA SCHEME CODE CHECK")

dup_rta = mapping_df.groupby(["rta", "rta_scheme_code"]).size()
dup_rta = dup_rta[dup_rta > 1]
log(f"Duplicate (rta, rta_scheme_code) entries: {len(dup_rta)}")

if len(dup_rta) > 0:
    log("\n🚨 DUPLICATE RTA ENTRIES:")
    for (rta, code), count in dup_rta.items():
        log(f"  RTA={rta}, Code={code}, Count={count}")

# ═══════════════════════════════════════════════════════════════
# 8. Spot-check: Fuzzy matches quality (high risk rule)
# ═══════════════════════════════════════════════════════════════
separator("8. FUZZY MATCH QUALITY AUDIT")

fuzzy_rows = mapping_df[mapping_df["mapping_source"] == "NAME_FUZZY"].copy()
log(f"Total fuzzy-matched rows: {len(fuzzy_rows)}")

if not fuzzy_rows.empty:
    fuzzy_rows["amfi_scheme_code"] = fuzzy_rows["amfi_scheme_code"].astype(str)
    amfi_names = amfi_df[["amfi_scheme_code", "name_norm", "amc_code"]].copy()
    amfi_names["amfi_scheme_code"] = amfi_names["amfi_scheme_code"].astype(str)
    
    fuzzy_audit = fuzzy_rows.merge(
        amfi_names, on="amfi_scheme_code", how="left"
    )
    
    log("\nFuzzy Match Samples (RTA name vs AMFI name):")
    for _, row in fuzzy_audit.head(30).iterrows():
        rta_name = str(row.get("rta_scheme_name", ""))[:55]
        amfi_name = str(row.get("name_norm", ""))[:55]
        log(f"  RTA : {rta_name}")
        log(f"  AMFI: {amfi_name}")
        log(f"  AMFI Code: {row['amfi_scheme_code']} | Source: {row['mapping_source']}")
        log("")
else:
    log("No fuzzy matches found.")

# ═══════════════════════════════════════════════════════════════
# 9. Unmapped schemes analysis
# ═══════════════════════════════════════════════════════════════
separator("9. UNMAPPED SCHEMES ANALYSIS")

unmapped_df = mapping_df[mapping_df["amfi_scheme_code"].isna()].copy()
log(f"Total unmapped schemes: {len(unmapped_df)}")

if not unmapped_df.empty:
    log("\n--- Unmapped by RTA ---")
    for rta, count in unmapped_df["rta"].value_counts().items():
        log(f"  {rta}: {count}")
    
    log("\n--- Sample unmapped schemes ---")
    for _, row in unmapped_df.head(20).iterrows():
        log(f"  RTA={row['rta']}, Code={row['rta_scheme_code']}, "
            f"AMC={row.get('rta_amc_code','')}, "
            f"Scheme={row.get('rta_scheme_name','')[:70]}")

# ═══════════════════════════════════════════════════════════════
# 10. Scheme Name Comparison for all mapped rows
# ═══════════════════════════════════════════════════════════════
separator("10. NAME SIMILARITY CHECK FOR ALL MAPPED ROWS")

try:
    from rapidfuzz import fuzz
    
    all_mapped = mapping_df[mapping_df["amfi_scheme_code"].notna()].copy()
    all_mapped["amfi_scheme_code"] = all_mapped["amfi_scheme_code"].astype(str)
    
    amfi_name_lookup = amfi_df.set_index(
        amfi_df["amfi_scheme_code"].astype(str)
    )["name_norm"].to_dict()
    
    all_mapped["amfi_name"] = all_mapped["amfi_scheme_code"].map(amfi_name_lookup)
    all_mapped["similarity"] = all_mapped.apply(
        lambda r: fuzz.ratio(
            str(r.get("normalized_scheme_name", "")),
            str(r.get("amfi_name", ""))
        ) if pd.notna(r.get("amfi_name")) else None,
        axis=1
    )
    
    log("Similarity distribution of mapped schemes:")
    bins = [0, 50, 60, 70, 80, 90, 95, 100]
    labels = ["0-50", "51-60", "61-70", "71-80", "81-90", "91-95", "96-100"]
    all_mapped["sim_bucket"] = pd.cut(all_mapped["similarity"], bins=bins, labels=labels)
    sim_dist = all_mapped["sim_bucket"].value_counts().sort_index()
    for bucket, count in sim_dist.items():
        log(f"  {bucket:>8s} : {count}")
    
    # Low similarity = potentially wrong match
    low_sim = all_mapped[all_mapped["similarity"] < 70].sort_values("similarity")
    log(f"\n🚨 LOW SIMILARITY MATCHES (<70%) — Likely WRONG mappings: {len(low_sim)}")
    for _, row in low_sim.head(20).iterrows():
        log(f"  Similarity: {row['similarity']:.0f}%")
        log(f"    RTA : {row.get('rta_scheme_name','')[:65]}")
        log(f"    AMFI: {row.get('amfi_name','')[:65]}")
        log(f"    Source: {row['mapping_source']}, Code: {row['amfi_scheme_code']}")
        log("")
        
    medium_sim = all_mapped[(all_mapped["similarity"] >= 70) & (all_mapped["similarity"] < 85)]
    log(f"\n⚠  MEDIUM SIMILARITY MATCHES (70-85%) — Review needed: {len(medium_sim)}")
    for _, row in medium_sim.head(15).iterrows():
        log(f"  Similarity: {row['similarity']:.0f}%")
        log(f"    RTA : {row.get('rta_scheme_name','')[:65]}")
        log(f"    AMFI: {row.get('amfi_name','')[:65]}")
        log(f"    Source: {row['mapping_source']}, Code: {row['amfi_scheme_code']}")
        log("")

except ImportError:
    log("rapidfuzz not available — skipping name similarity check.")

# ═══════════════════════════════════════════════════════════════
# 11. Script Logic Issues
# ═══════════════════════════════════════════════════════════════
separator("11. SCRIPT LOGIC ISSUES IDENTIFIED")

issues = []

# Issue 1: Only ACTIVE schemes are queried
issues.append(
    "ISSUE-1: AMFI query uses WHERE status = 'ACTIVE' — legacy/matured schemes "
    "from RTA files will NEVER match. This is the #1 cause of unmapped schemes."
)

# Issue 2: restore_engine() returns PROJECT DB, not MASTER DB
issues.append(
    "ISSUE-2: restore_engine() creates engine for PROJECT_DB (inteliwealth_db), but "
    "amfi_scheme_master lives in MASTER_DB (intelli_wealth_28_07_2026). The AMFI query "
    "at line 261 uses restore_engine() — this will FAIL or query the wrong database "
    "unless public.amfi_scheme_master also exists in the project DB."
)

# Issue 3: Rule 2 (PRODUCT_MATCH) is redundant with Rule 1
issues.append(
    "ISSUE-3: Rule 2 (PRODUCT_MATCH) uses amc_code == rta_amc_code AND name_norm == "
    "normalized_scheme_name. But rta_amc_code is the raw RTA code (e.g. 'K'), while "
    "amfi_df.amc_code is the AMFI slug (e.g. 'kotak-mahindra-mutual-fund'). These will "
    "almost NEVER match, making Rule 2 a no-op."
)

# Issue 4: Rule 4 fuzzy has no AMC guard
issues.append(
    "ISSUE-4: Rule 4 (fuzzy match) uses a global name list without filtering by AMC. "
    "A scheme from HDFC AMC could be fuzzy-matched to an ICICI scheme with a similar "
    "name. This is a HIGH-RISK mapping error."
)

# Issue 5: ON CONFLICT DO NOTHING
issues.append(
    "ISSUE-5: The upsert uses ON CONFLICT (rta, rta_scheme_code) DO NOTHING. If a "
    "previous run inserted a WRONG mapping, re-running the script will NOT fix it. "
    "Bad data becomes permanent."
)

# Issue 6: rta_isin is always None
issues.append(
    "ISSUE-6: rta_isin is hardcoded to None (line 247). Rule 0 (ISIN match) will "
    "never match any rows. The highest-confidence rule is dead code."
)

# Issue 7: normalize_scheme_name applied twice
issues.append(
    "ISSUE-7: amfi_df['name_norm'] is normalized TWICE — once in SQL and once in Python "
    "(line 267-268). If the DB already stores normalized names, double-normalization "
    "could introduce subtle differences."
)

for i, issue in enumerate(issues):
    log(f"\n{issue}")

# ═══════════════════════════════════════════════════════════════
# 12. Verify restore_engine targets correct DB
# ═══════════════════════════════════════════════════════════════
separator("12. DATABASE CROSS-CHECK: Where does amfi_scheme_master live?")

try:
    test1 = pd.read_sql("SELECT COUNT(*) as cnt FROM public.amfi_scheme_master", project_engine)
    log(f"amfi_scheme_master in PROJECT DB (inteliwealth_db): {test1.iloc[0]['cnt']} rows")
except Exception as e:
    log(f"amfi_scheme_master NOT in PROJECT DB: {e}")

try:
    test2 = pd.read_sql("SELECT COUNT(*) as cnt FROM public.amfi_scheme_master", master_engine)
    log(f"amfi_scheme_master in MASTER DB (intelli_wealth_28_07_2026): {test2.iloc[0]['cnt']} rows")
except Exception as e:
    log(f"amfi_scheme_master NOT in MASTER DB: {e}")

# ═══════════════════════════════════════════════════════════════
# Save output
# ═══════════════════════════════════════════════════════════════
output_file = os.path.join(os.path.dirname(__file__), "validation_results.txt")
with open(output_file, "w") as f:
    f.write("\n".join(OUTPUT))
log(f"\n\nResults saved to: {output_file}")
