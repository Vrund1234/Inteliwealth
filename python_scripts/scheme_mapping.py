import re
import uuid
import pandas as pd
from sqlalchemy import text
from rapidfuzz import fuzz, process

from utils.db import engine, master_engine


# =====================================================
# NORMALIZE SCHEME NAME
# =====================================================

def normalize_scheme_name(name):
    """
    Normalize scheme name:
    - Convert to uppercase
    - Remove special characters
    - Remove extra spaces
    """
    if pd.isna(name):
        return None

    name = str(name).upper()
    name = re.sub(r"[^A-Z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


# =====================================================
# MAIN FUNCTION
# =====================================================

def load_scheme_mapping():
    print("=" * 80)
    print("STARTING SCHEME MAPPING")
    print("=" * 80)

    # =================================================
    # LOAD DISTINCT RTA SCHEMES
    # =================================================

    query = """
        SELECT DISTINCT ON (source, prodcode)
            source,
            amc_code,
            prodcode,
            scheme
        FROM silver.transaction_master_new
        WHERE source IS NOT NULL
          AND scheme IS NOT NULL
          AND NULLIF(TRIM(prodcode), '') IS NOT NULL
        ORDER BY source, prodcode;
    """

    df = pd.read_sql(query, engine)
    print(f"Distinct Schemes Found : {len(df)}")

    # =================================================
    # RENAME RTA COLUMNS
    # =================================================

    df.rename(
        columns={
            "source": "rta",
            "amc_code": "rta_amc_code",
            "prodcode": "rta_scheme_code",
            "scheme": "rta_scheme_name",
        },
        inplace=True,
    )

    # =================================================
    # GENERATE STABLE MAPPING ID
    # =================================================

    # df["mapping_id"] = df.apply(
    #     lambda x: str(
    #         uuid.uuid5(
    #             uuid.NAMESPACE_DNS,
    #             f"{x['rta']}|{x['rta_scheme_code']}|{x['amfi_scheme_code']}"
    #         )
    #         if ("amfi_scheme_code" in x and pd.notna(x["amfi_scheme_code"]))
    #         else uuid.uuid5(
    #             uuid.NAMESPACE_DNS,
    #             f"{x['rta']}|{x['rta_scheme_code']}"
    #         )
    #     ),
    #     axis=1
    # )

    # =================================================
    # SHORT NAME NORMALIZATION
    # =================================================

    def normalize_short_name(name):
        if pd.isna(name):
            return None

        name = normalize_scheme_name(name)
        remove_words = {"FUND", "SCHEME", "PLAN"}
        words = [w for w in name.split() if w not in remove_words]

        return " ".join(words)

    # =================================================
    # NORMALIZE RTA SCHEME NAMES
    # =================================================

    df["normalized_scheme_name"] = df["rta_scheme_name"].apply(normalize_scheme_name)
    df["short_scheme_name"] = df["rta_scheme_name"].apply(normalize_short_name)

    # =================================================
    # RTA ISIN
    # =================================================

    # Placeholder until RTA starts providing ISIN
    df["rta_isin"] = None

    # =================================================
    # LOAD AMFI SCHEME MASTER
    # =================================================

    amfi_query = """
        SELECT
            amfi_scheme_code,
            amc_code,
            normalized_scheme_name,
            normalized_nav_name,
            isin_growth,
            isin_idcw
        FROM public.amfi_scheme_master
        WHERE status = 'ACTIVE';
    """

    print("START: Loading AMFI master")
    amfi_df = pd.read_sql(amfi_query, master_engine)
    print(f"DONE: AMFI master loaded: {len(amfi_df)}")

    # =================================================
    # NORMALIZE AMFI NAMES
    # =================================================

    amfi_df["normalized_scheme_name"] = (
        amfi_df["normalized_scheme_name"]
        .apply(normalize_scheme_name)
    )

    amfi_df["normalized_nav_name"] = (
        amfi_df["normalized_nav_name"]
        .apply(normalize_scheme_name)
    )

    amfi_df["short_name"] = (
        amfi_df["normalized_nav_name"]
        .apply(normalize_short_name)
    )

    # =================================================
    # IDENTIFY DUPLICATE AMFI NORMALIZED NAMES
    # =================================================

    amfi_name_counts = (
        amfi_df[amfi_df["normalized_nav_name"].notna()]
        .groupby("normalized_nav_name")
        .size()
        .reset_index(name="amfi_count")
    )

    duplicate_amfi_names = set(
        amfi_name_counts[amfi_name_counts["amfi_count"] > 1]["normalized_nav_name"]
    )

    print(f"AMFI duplicate normalized names found: {len(duplicate_amfi_names)}")

    # =================================================
    # LOAD RTA -> AMC SLUG MAPPING
    # =================================================

    amc_mapping_query = """
        SELECT
            rta,
            amc_code AS rta_amc_code,
            amc_slug
        FROM public.rta_amc_code;
    """

    print("START: Loading AMC mapping")
    amc_mapping_df = pd.read_sql(amc_mapping_query, master_engine)
    print(f"DONE: AMC mapping loaded: {len(amc_mapping_df)}")

    df = df.merge(amc_mapping_df, on=["rta", "rta_amc_code"], how="left")

    # =================================================
    # BEST MATCH COLUMNS
    # =================================================

    df["best_amfi_scheme_code"] = None
    df["best_mapping_source"] = None
    df["best_mapping_confidence"] = None

    # =================================================
    # HELPER: UPDATE BEST MATCH
    # =================================================

    def update_best_match(df, idx, amfi_code, source, confidence):
        if amfi_code is None or pd.isna(amfi_code):
            return

        current_confidence = df.at[idx, "best_mapping_confidence"]

        if pd.isna(current_confidence) or confidence > current_confidence:
            df.at[idx, "best_amfi_scheme_code"] = amfi_code
            df.at[idx, "best_mapping_source"] = source
            df.at[idx, "best_mapping_confidence"] = confidence

    # =================================================
    # RULE 0 : ISIN MATCH (100)
    # =================================================

    isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

    for idx, row in df.iterrows():
        if not row["rta_isin"]:
            continue

        if not isin_pattern.match(row["rta_isin"]):
            continue

        matches = amfi_df[
            (amfi_df["isin_growth"] == row["rta_isin"])
            | (amfi_df["isin_idcw"] == row["rta_isin"])
        ]

        if len(matches) == 1:
            update_best_match(
                df, idx, matches.iloc[0]["amfi_scheme_code"], "ISIN_MATCH", 100
            )

    # =================================================
    # RULE 1 : EXACT NAME MATCH (99)
    # =================================================

    print("=" * 80)
    print("RULE 1 : EXACT NAME MATCH")
    print("=" * 80)

    for idx, row in df.iterrows():

        rta_name = row["normalized_scheme_name"]

        if pd.isna(rta_name) or not rta_name:
            continue

        # -------------------------------------------------
        # MATCH RTA normalized_scheme_name AGAINST
        # BOTH AMFI normalized_scheme_name AND normalized_nav_name
        # -------------------------------------------------

        matches = amfi_df[
            (amfi_df["normalized_scheme_name"] == rta_name)
            |
            (amfi_df["normalized_nav_name"] == rta_name)
        ]

        # -------------------------------------------------
        # NO MATCH
        # -------------------------------------------------

        if len(matches) == 0:
            print(
                "[EXACT NAME NOT FOUND]:",
                row["rta_scheme_name"]
            )
            continue

        # -------------------------------------------------
        # EXACT MATCH FOUND
        # -------------------------------------------------

        print(
            "EXACT NAME MATCH:",
            row["rta_scheme_name"],
            "=>",
            rta_name,
            "=>",
            len(matches),
            "AMFI records"
        )

        # -------------------------------------------------
        # UPDATE MATCH
        # -------------------------------------------------

        for _, amfi_row in matches.iterrows():

            amfi_code = amfi_row["amfi_scheme_code"]

            update_best_match(
                df,
                idx,
                amfi_code,
                "EXACT_NAME",
                99
            )

    # =================================================
    # RULE 2 : PRODUCT MATCH (100)
    # =================================================

    print("=" * 80)
    print("RULE 2 : PRODUCT MATCH")
    print("=" * 80)

    for idx, row in df.iterrows():

        rta_amc_code = (
            str(row["rta_amc_code"]).strip().upper()
            if pd.notna(row["rta_amc_code"])
            else None
        )

        rta_scheme_name = (
            str(row["normalized_scheme_name"]).strip()
            if pd.notna(row["normalized_scheme_name"])
            else None
        )

        if not rta_amc_code or not rta_scheme_name:
            continue

        matches = amfi_df[
            (
                amfi_df["amc_code"]
                .astype(str)
                .str.strip()
                .str.upper()
                == rta_amc_code
            )
            &
            (
                amfi_df["normalized_nav_name"]
                .astype(str)
                .str.strip()
                == rta_scheme_name
            )
        ]

        if len(matches) == 1:

            print(
                "PRODUCT MATCH:",
                row["rta_scheme_name"],
                "=>",
                matches.iloc[0]["normalized_nav_name"],
                "=> AMFI:",
                matches.iloc[0]["amfi_scheme_code"]
            )

            update_best_match(
                df,
                idx,
                matches.iloc[0]["amfi_scheme_code"],
                "PRODUCT_MATCH",
                100
            )

        elif len(matches) > 1:

            print(
                "[PRODUCT MATCH AMBIGUOUS]:",
                row["rta_scheme_name"],
                "=>",
                len(matches),
                "AMFI records"
            )

    # =================================================
    # RULE 3 : AMC + SCHEME NAME MATCH (97)
    # =================================================

    print("=" * 80)
    print("RULE 3 : AMC + SCHEME NAME MATCH")
    print("=" * 80)

    unmatched_amc_df = df[df["best_amfi_scheme_code"].isna()].copy()

    for idx, row in unmatched_amc_df.iterrows():
        if pd.isna(row["amc_slug"]):
            continue

        # ---------------------------------------------
        # First try full normalized name
        # ---------------------------------------------

        matches = amfi_df[
            (amfi_df["amc_code"] == row["amc_slug"])
            & (amfi_df["normalized_nav_name"] == row["normalized_scheme_name"])
        ]

        # ---------------------------------------------
        # If not unique, try short name
        # ---------------------------------------------

        if len(matches) != 1:
            matches = amfi_df[
                (amfi_df["amc_code"] == row["amc_slug"])
                & (amfi_df["short_name"] == row["short_scheme_name"])
            ]

        if len(matches) == 1:
            update_best_match(
                df, idx, matches.iloc[0]["amfi_scheme_code"], "AMC_NAME", 97
            )

    # =================================================
    # RULE 4 : FUZZY NAME MATCH (95)
    # =================================================

    print("=" * 80)
    print("RULE 4 : FUZZY NAME MATCH")
    print("=" * 80)

    def fuzzy_match(row):
        rta_name = row["normalized_scheme_name"]

        if pd.isna(rta_name) or not rta_name:
            return None, None, None

        # -------------------------------------------------
        # Restrict fuzzy candidates to same AMC
        # -------------------------------------------------

        if pd.notna(row["amc_slug"]) and row["amc_slug"]:
            amfi_candidates = amfi_df[amfi_df["amc_code"] == row["amc_slug"]][
                ["amfi_scheme_code", "normalized_nav_name"]
            ].dropna(subset=["normalized_nav_name"])
        else:
            # AMC unknown -> do not perform global fuzzy matching
            return None, None, None

        if amfi_candidates.empty:
            return None, None, None

        # -------------------------------------------------
        # Remove duplicate name/code combinations
        # -------------------------------------------------

        amfi_candidates = amfi_candidates.drop_duplicates(
            subset=["normalized_nav_name", "amfi_scheme_code"]
        )

        # -------------------------------------------------
        # Get top 2 fuzzy matches
        # -------------------------------------------------

        amfi_name_list = amfi_candidates["normalized_nav_name"].tolist()

        matches = process.extract(
            rta_name,
            amfi_name_list,
            scorer=fuzz.ratio,
            score_cutoff=98,
            limit=2,
        )

        # -------------------------------------------------
        # No fuzzy match
        # -------------------------------------------------

        if not matches:
            return None, None, None

        # -------------------------------------------------
        # SAFETY CHECK
        #
        # If top two candidates have the same score, do NOT guess.
        # -------------------------------------------------

        if len(matches) > 1 and matches[0][1] == matches[1][1]:
            print(
                "[FUZZY AMBIGUOUS - TIED SCORE]:",
                row["rta_scheme_name"],
                "=>",
                matches[0][0],
                "score:",
                matches[0][1],
                "|",
                matches[1][0],
                "score:",
                matches[1][1],
            )
            return None, None, None

        # -------------------------------------------------
        # Unique top fuzzy match
        # -------------------------------------------------

        matched_name = matches[0][0]
        fuzzy_score = matches[0][1]

        matched_rows = amfi_candidates[
            amfi_candidates["normalized_nav_name"] == matched_name
        ]

        # -------------------------------------------------
        # Safety check: Same name mapped to multiple AMFI codes
        # -------------------------------------------------

        if len(matched_rows) != 1:
            print(
                "[FUZZY AMBIGUOUS - MULTIPLE AMFI CODES]:",
                row["rta_scheme_name"],
                "=>",
                matched_name,
                "score:",
                fuzzy_score,
            )
            return None, None, None

        amfi_code = matched_rows.iloc[0]["amfi_scheme_code"]

        print(
            "FUZZY MATCH:",
            row["rta_scheme_name"],
            "=>",
            matched_name,
            "score:",
            fuzzy_score,
            "AMFI:",
            amfi_code,
        )

        return (amfi_code, "NAME_FUZZY", 95)

    # -------------------------------------------------
    # APPLY FUZZY MATCH ONLY TO UNMATCHED RECORDS
    # -------------------------------------------------

    unmatched_fuzzy_df = df[df["best_amfi_scheme_code"].isna()].copy()

    for idx, row in unmatched_fuzzy_df.iterrows():
        amfi_code, source, confidence = fuzzy_match(row)
        update_best_match(df, idx, amfi_code, source, confidence)

    # =================================================
    # FINALIZE NORMAL MATCH RESULTS
    # =================================================

    df["amfi_scheme_code"] = df["best_amfi_scheme_code"]
    df["mapping_source"] = df["best_mapping_source"]
    df["mapping_confidence"] = df["best_mapping_confidence"]

    # =================================================
    # GENERATE INTERNAL SCHEME ID
    # =================================================

    matched_amfi = amfi_df[["amfi_scheme_code", "amc_code"]].drop_duplicates()

    df = df.merge(
        matched_amfi,
        on="amfi_scheme_code",
        how="left",
        suffixes=("", "_master"),
    )

    df["scheme_id"] = (
        df["amc_code"].fillna("").astype(str)
        + df["amfi_scheme_code"].fillna("").astype(str)
    )

    # =================================================
    # GENERATE STABLE MAPPING ID
    # =================================================

    df["mapping_id"] = df.apply(
        lambda x: uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{x['rta']}|{x['rta_scheme_code']}|{x['amfi_scheme_code']}",
        )
        if pd.notna(x["amfi_scheme_code"])
        else uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{x['rta']}|{x['rta_scheme_code']}",
        ),
        axis=1,
    )

    # =================================================
    # CLEAN DATA
    # =================================================

    df.drop(
        columns=["normalized_nav_name", "match_count"],
        errors="ignore",
        inplace=True,
    )

    df = df.where(pd.notna(df), None)
    df["mapping_confidence"] = df["mapping_confidence"].astype("Int64")

    # =================================================
    # DEDUPLICATE NORMAL RTA SCHEMES
    # =================================================

    print(f"Before dedup: {len(df)}")

    df = df.drop_duplicates(
        subset=["rta", "rta_scheme_code", "amfi_scheme_code"],
        keep="first",
    )

    print(f"After dedup: {len(df)}")

    # =================================================
    # INSERT NORMAL MAPPINGS
    # =================================================

    insert_query = text("""
        INSERT INTO bronze.scheme_mapping (
            mapping_id,
            scheme_id,
            rta,
            rta_amc_code,
            rta_scheme_code,
            rta_scheme_name,
            normalized_scheme_name,
            amfi_scheme_code,
            mapping_source,
            mapping_confidence
        )
        VALUES (
            :mapping_id,
            :scheme_id,
            :rta,
            :rta_amc_code,
            :rta_scheme_code,
            :rta_scheme_name,
            :normalized_scheme_name,
            :amfi_scheme_code,
            :mapping_source,
            :mapping_confidence
        )
        ON CONFLICT (mapping_id)
        DO UPDATE SET
            scheme_id = EXCLUDED.scheme_id,
            rta_amc_code = EXCLUDED.rta_amc_code,
            rta_scheme_code = EXCLUDED.rta_scheme_code,
            rta_scheme_name = EXCLUDED.rta_scheme_name,
            normalized_scheme_name = EXCLUDED.normalized_scheme_name,
            amfi_scheme_code = EXCLUDED.amfi_scheme_code,
            mapping_source = EXCLUDED.mapping_source,
            mapping_confidence = EXCLUDED.mapping_confidence;
    """)

    with engine.begin() as conn:
        conn.execute(
            insert_query,
            df.to_dict(orient="records")
        )

    print(f"Normal mappings processed: {len(df)}")

    # =================================================
    # DUPLICATE AMFI NAME EXPANSION
    # =================================================

    print("=" * 80)
    print("START: DUPLICATE AMFI NAME EXPANSION")
    print("=" * 80)

    # -------------------------------------------------
    # LOAD EXISTING SCHEME MAPPINGS
    # -------------------------------------------------

    existing_mapping_query = """
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
            mapping_confidence
        FROM bronze.scheme_mapping
        WHERE normalized_scheme_name IS NOT NULL;
    """

    existing_mapping_df = pd.read_sql(existing_mapping_query, engine)
    print(f"Existing scheme mappings loaded: {len(existing_mapping_df)}")

    # -------------------------------------------------
    # COUNT MAPPINGS PER NORMALIZED NAME
    # -------------------------------------------------

    mapping_counts = (
        existing_mapping_df
        .groupby("normalized_scheme_name")
        .size()
        .reset_index(name="mapping_count")
    )

    # -------------------------------------------------
    # FIND TARGET NAMES
    #
    # AMFI count > 1
    # AND
    # scheme_mapping count == 1
    # -------------------------------------------------

    target_names = amfi_name_counts.merge(
        mapping_counts,
        left_on="normalized_nav_name",
        right_on="normalized_scheme_name",
        how="inner",
    )

    target_names = target_names[
        (target_names["amfi_count"] > 1) & (target_names["mapping_count"] == 1)
    ]

    print(f"Names requiring duplicate expansion: {len(target_names)}")

    # -------------------------------------------------
    # NO TARGETS
    # -------------------------------------------------

    if target_names.empty:
        print("No duplicate AMFI mappings require expansion.")
    else:
        target_name_list = target_names["normalized_nav_name"].tolist()

        # ---------------------------------------------
        # GET SOURCE MAPPING
        # ---------------------------------------------

        source_mappings = existing_mapping_df[
            existing_mapping_df["normalized_scheme_name"].isin(target_name_list)
        ].copy()

        # ---------------------------------------------
        # GET ALL AMFI RECORDS
        # ---------------------------------------------

        duplicate_amfi_df = amfi_df[
            amfi_df["normalized_nav_name"].isin(target_name_list)
        ].copy()

        print(f"AMFI records to expand: {len(duplicate_amfi_df)}")

        # ---------------------------------------------
        # CREATE EXPANDED ROWS
        # ---------------------------------------------

        expanded_rows = []

        for _, mapping in source_mappings.iterrows():
            matching_amfi = duplicate_amfi_df[
                duplicate_amfi_df["normalized_nav_name"] == mapping["normalized_scheme_name"]
            ]

            for _, amfi in matching_amfi.iterrows():
                expanded_rows.append({
                    "mapping_id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{mapping['rta']}|{mapping['rta_scheme_code']}|{amfi['amfi_scheme_code']}",
                        )
                    ),
                    "scheme_id": (
                        str(amfi["amc_code"]) + str(amfi["amfi_scheme_code"])
                    ),
                    "rta": mapping["rta"],
                    "rta_amc_code": mapping["rta_amc_code"],
                    "rta_scheme_code": mapping["rta_scheme_code"],
                    "rta_scheme_name": mapping["rta_scheme_name"],
                    "normalized_scheme_name": mapping["normalized_scheme_name"],
                    "amfi_scheme_code": amfi["amfi_scheme_code"],
                    "mapping_source": "NAME_EXACT",
                    # Ambiguous by name, therefore no confidence.
                    "mapping_confidence": 99,
                })

        expanded_df = pd.DataFrame(expanded_rows)

        # ---------------------------------------------
        # INSERT EXPANDED MAPPINGS
        # ---------------------------------------------

        if not expanded_df.empty:
            print(f"Duplicate mapping rows generated: {len(expanded_df)}")

            expanded_df = expanded_df.where(pd.notna(expanded_df), None)

            insert_duplicate_query = text("""
                INSERT INTO bronze.scheme_mapping (
                    mapping_id,
                    scheme_id,
                    rta,
                    rta_amc_code,
                    rta_scheme_code,
                    rta_scheme_name,
                    normalized_scheme_name,
                    amfi_scheme_code,
                    mapping_source,
                    mapping_confidence
                )
                VALUES (
                    :mapping_id,
                    :scheme_id,
                    :rta,
                    :rta_amc_code,
                    :rta_scheme_code,
                    :rta_scheme_name,
                    :normalized_scheme_name,
                    :amfi_scheme_code,
                    :mapping_source,
                    :mapping_confidence
                )
                ON CONFLICT (
                    rta,
                    rta_scheme_code,
                    amfi_scheme_code
                )
                DO UPDATE SET
                    scheme_id = EXCLUDED.scheme_id,
                    mapping_source = EXCLUDED.mapping_source,
                    mapping_confidence = EXCLUDED.mapping_confidence;
            """)

            with engine.begin() as conn:
                conn.execute(
                    insert_duplicate_query,
                    expanded_df.to_dict(orient="records"),
                )

            print("DONE: Duplicate AMFI Name Expansion")
        else:
            print("No duplicate rows generated.")

    # =================================================
    # FINAL SUMMARY
    # =================================================

    print("=" * 80)
    print("SCHEME MAPPING COMPLETED")
    print("=" * 80)

    print(f"Processed RTA schemes : {len(df)}")
    print("Normal matching rules completed.")
    print("Duplicate AMFI name expansion completed.")


# =====================================================
# ENTRY POINT
# =====================================================

if __name__ == "__main__":
    load_scheme_mapping()

