import pandas as pd
import numpy as np
from utils.db import engine
from utils.upsert import upsert_dataframe
from pyparsing import col
# from sqlalchemy import create_engine
# from utils.db import engine, master_engine
from mapping import INVESTOR_MASTER_MAPPING

# =====================================================
# CLEAN COLUMN NAMES
# =====================================================

def clean_columns(df):

    if df is None:
        return df

    df = df.copy()

    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
        .str.lower()
        .str.replace(" ", "_", regex=False)
        .str.replace("-", "_", regex=False)
        .str.replace("/", "_", regex=False)
        .str.replace("#", "", regex=False)
    )

    return df


# =====================================================
# DATE COLUMNS
# =====================================================

DATE_COLUMNS = [
    "dob",
    "report_date",
    "rep_date",
    "folio_date",
    "jh1_dob",
    "jh2_dob",
    "guardian_dob",
    "lastupdateddate",
    "nominee_dob"
]


# =====================================================
# IDENTIFIER COLUMNS
# (.0 SHOULD NEVER APPEAR)
# =====================================================

IDENTIFIER_COLUMNS = [

    # Folios
    "folio_no",
    "folio",
    "foliochk",
    "folio_old",
    "old_folio",
    "scheme_folio_number",
    "scheme_fol",

    # Pincodes
    "pincode",
    "pin",
    "b_pincode",
    "nominee1_pincode",
    "nominee2_pincode",
    "nominee3_pincode",
    "nom_pincode",
    "nom2_pincode",
    "nom3_pincode",

    # Bank
    "bank_account_no",
    "account_no",
    "bnkacno",

    # Phones
    "mobile_no",
    "mobile",

    "phone_res",
    "phone_off",

    "phone_res1",
    "phone_res2",

    "phone_off1",
    "phone_off2",

    "rphone",
    "rphone1",
    "rphone2",

    "ophone",
    "ophone1",
    "ophone2",

    "bank_phone",
    "bphone",

    "nominee1_phone",
    "nominee2_phone",
    "nominee3_phone",

    "nom_ph_off",
    "nom2_ph_off",
    "nom3_ph_off",

    # KYC & Identifiers
    "dp_id",
    "dpid",
    "ckyc_no",
    "fh_ckyc_no",
    "fh_ckyc",
    "fh_ckyc_n",
    "jh1_ckyc",
    "jh2_ckyc",
    "guardian_ckyc_no",
    "g_ckyc_no",
    "g_ckyc_n",
    "aadhaar",
    "broker_code",
    "brokcode"
]


# =====================================================
# NORMALIZE
# =====================================================

def normalize(df):

    if df is None:
        return df

    df = df.copy()

    for col in df.columns:

        if col in DATE_COLUMNS:
            continue

        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.replace("'", "", regex=False)
            .str.replace('"', "", regex=False)
            .str.strip()
            .replace({
                "nan": "",
                "None": "",
                "<NA>": "",
                "NaT": ""
            })
        )
        
    return df


# =====================================================
# CLEAN IDENTIFIER COLUMNS
# =====================================================

def clean_identifier_columns(df):

    if df is None:
        return df

    df = df.copy()

    for col in IDENTIFIER_COLUMNS:

        if col not in df.columns:
            continue

        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .replace({
                "": None,
                "nan": None,
                "None": None,
                "<NA>": None
            })
        )

    return df

# =====================================================
# FORMAT DATE COLUMNS
# =====================================================

def format_dates(df):

    if df is None:
        return df

    df = df.copy()

    for col in DATE_COLUMNS:

        if col in df.columns:

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce",
                    # dayfirst=False
                )
                .dt.date
            )

            df[col] = df[col].where(
                pd.notnull(df[col]),
                None
            )

    return df

# =====================================================
# APPLY INVESTOR MAPPING
# =====================================================

def apply_investor_mapping(raw_df, mapping, source):

    raw_df = clean_columns(raw_df)

    print("=" * 80)
    print("Rows received for mapping :", len(raw_df))
    print("Columns :", len(raw_df.columns))
    print(raw_df.columns.tolist())
    print(raw_df.head())
    print("=" * 80)

    mapped_df = pd.DataFrame(index=raw_df.index)

    for target_col, source_cols in mapping.items():

        if target_col in [
            "flag",
            "created_at",
            "updated_at"
        ]:
            continue

        if target_col == "source":
            mapped_df[target_col] = source
            continue

        value = None

        for src in source_cols:

            src = src.lower()

            if src in raw_df.columns:
                value = raw_df[src]
                break

        if value is None:

            value = pd.Series(
                [None] * len(raw_df),
                index=raw_df.index
            )

        mapped_df[target_col] = value

    # =====================================================
    # SOURCE-SPECIFIC OCCUPATION MAPPING
    # =====================================================

    if source.upper() == "CAMS":

        # CAMS OCCUPATION contains the actual
        # occupation description.
        #
        # Example:
        # OCCUPATION = BUSINESS
        # OCCUPATION = HOUSEWIFE
        # OCCUPATION = Private Sector Service

        if "occupation" in raw_df.columns:

            mapped_df["occupation_description"] = raw_df["occupation"]

        # CAMS does not provide an occupation code
        mapped_df["occupation"] = None


    elif source.upper() == "KFIN":

        # KFIN has separate occupation code
        # and occupation description.

        if "occ_code" in raw_df.columns:

            mapped_df["occupation"] = raw_df["occ_code"]

        if "occupation_description" in raw_df.columns:

            mapped_df["occupation_description"] = (
                raw_df["occupation_description"]
            )

    return mapped_df


# =====================================================
# PROCESS INVESTOR MASTER
# =====================================================

def process_investor_master(cams=None, kfin=None):

    dfs = []

    # =====================================================
    # CAMS
    # =====================================================

    if cams is not None and not cams.empty:

        cams_df = apply_investor_mapping(
            cams,
            INVESTOR_MASTER_MAPPING,
            "CAMS"
        )

        cams_df = normalize(cams_df)

        # CLEAN ALL IDENTIFIER COLUMNS
        cams_df = clean_identifier_columns(cams_df)

        cams_df = format_dates(cams_df)

        dfs.append(cams_df)

    # =====================================================
    # KFIN
    # =====================================================

    if kfin is not None and not kfin.empty:

        kfin_df = apply_investor_mapping(
            kfin,
            INVESTOR_MASTER_MAPPING,
            "KFIN"
        )

        kfin_df = normalize(kfin_df)

        # CLEAN ALL IDENTIFIER COLUMNS
        kfin_df = clean_identifier_columns(kfin_df)

        kfin_df = format_dates(kfin_df)

        dfs.append(kfin_df)

    # =====================================================
    # NO FILES
    # =====================================================

    if not dfs:

        print("No Investor Master file found.")
        return

    # =====================================================
    # MERGE
    # =====================================================

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    # now = pd.Timestamp.now()
    now = pd.Timestamp.now(tz="Asia/Kolkata")

    df["created_at"] = now
    df["updated_at"] = now

    try:

        existing = pd.read_sql(
            "SELECT * FROM bronze.investor_master",
            engine
        )

        existing = normalize(existing)

        # CLEAN ALL IDENTIFIER COLUMNS
        existing = clean_identifier_columns(existing)

        existing = format_dates(existing)

    except Exception:

        existing = pd.DataFrame()

    # =====================================================
    # DUPLICATE FLAG
    # =====================================================   
    ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source"
    }

    if existing.empty:

        df["flag"] = 0

    else:

        compare_cols = [

            c

            for c in df.columns

            if c in existing.columns

            and c not in ignore_cols

        ]

        new_df = df[compare_cols].copy()

        old_df = existing[compare_cols].copy()

        # =====================================================
        # CLEAN IDENTIFIER COLUMNS BEFORE COMPARISON
        # =====================================================

        new_df = clean_identifier_columns(new_df)
        old_df = clean_identifier_columns(old_df)

        # =====================================================
        # NORMALIZE VALUES
        # =====================================================

        for col in compare_cols:

            if col in DATE_COLUMNS:

                new_df[col] = (
                    pd.to_datetime(
                        new_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

                old_df[col] = (
                    pd.to_datetime(
                        old_df[col],
                        errors="coerce"
                    )
                    .dt.strftime("%Y-%m-%d")
                    .fillna("")
                )

            else:

                new_df[col] = (
                    new_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                old_df[col] = (
                    old_df[col]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

        # =====================================================
        # COMPARE COMPLETE ROW
        # =====================================================

        new_keys = new_df.astype(str).agg("|".join, axis=1)

        old_keys = set(
            old_df.astype(str).agg("|".join, axis=1)
        )

        df["flag"] = new_keys.isin(old_keys).astype(int)

    # =====================================================
    # CLEAN IDENTIFIER COLUMNS AGAIN BEFORE INSERT
    # =====================================================

    df = clean_identifier_columns(df)

    # =====================================================
    # GET COLUMN ORDER FROM POSTGRES
    # =====================================================

    db_columns = pd.read_sql(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
        AND table_name = 'investor_master'
        ORDER BY ordinal_position
        """,
        engine
    )["column_name"].tolist()

    # =====================================================
    # ADD MISSING COLUMNS
    # =====================================================

    for col in db_columns:

        if col not in df.columns:

            df[col] = None

    # =====================================================
    # KEEP ONLY DATABASE COLUMNS
    # =====================================================

    df = df[db_columns]

    # =====================================================
    # FINAL DATE CLEANING
    # =====================================================

    for col in DATE_COLUMNS:

        if col in df.columns:

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce"
                )
                .dt.date
            )

            df[col] = df[col].where(
                pd.notnull(df[col]),
                None
            )

    # =====================================================
    # CLEAN NON-DATE COLUMNS
    # =====================================================

    for col in df.columns:

        if col not in DATE_COLUMNS:

            df[col] = (
                df[col]
                .replace({
                    "": None,
                    "nan": None,
                    "None": None,
                    "<NA>": None,
                    "NaT": None
                })
            )

    # =====================================================
    # FINAL CLEAN IDENTIFIER COLUMNS
    # (ENSURES .0 NEVER REACHES POSTGRES)
    # =====================================================

    df = clean_identifier_columns(df)

    # =====================================================
    # DEBUG DATE COLUMNS
    # =====================================================

    print("=" * 80)
    print("Checking DATE columns before insert")

    for col in DATE_COLUMNS:

        if col in df.columns:

            print(f"{col} : {df[col].dtype}")

            bad = df[df[col].astype(str) == ""]

            if len(bad):

                print(f"{col} has {len(bad)} empty strings")

            print(df[col].head())

    print("=" * 80)

    # =====================================================
    # REPLACE REMAINING NaN
    # =====================================================

    df = df.where(pd.notnull(df), None)

    # =====================================================
    # FINAL SAFETY CHECK FOR DATE COLUMNS
    # =====================================================

    for col in DATE_COLUMNS:

        if col in df.columns:

            df.loc[
                df[col].astype(str).isin(
                    ["", "NaT", "nan", "None"]
                ),
                col
            ] = None

    # =====================================================
    # FINAL CLEAN IDENTIFIER COLUMNS
    # =====================================================

    df = clean_identifier_columns(df)

    # =====================================================
    # REMOVE EXACT DUPLICATE ROWS
    # =====================================================

    #before = len(df)

    #df = (
    #    df
    #    .drop_duplicates(keep="first")
    #    .reset_index(drop=True)
    #)

    #print(f"Removed {before - len(df)} exact duplicate rows")

    # =====================================================
    # FINAL COLUMN ORDER CHECK
    # =====================================================

    df = df[db_columns]

    # =====================================================
    # INSERT INTO POSTGRES
    # =====================================================

    print("=" * 80)
    print("Loading Investor Master...")
    print(f"Rows to insert : {len(df)}")
    print("=" * 80)

    conflict_target_sql = (
        "((source IS NULL), (COALESCE(source, '')), "
        "(folio_no IS NULL), (COALESCE(folio_no, '')), "
        "(product_code IS NULL), (COALESCE(product_code, '')))"
    )
    inserted = upsert_dataframe(
        engine, df, schema="bronze", table="investor_master",
        conflict_target_sql=conflict_target_sql, update_set_sql=None,
    )
    print(f"Rows inserted (duplicates skipped): {inserted} of {len(df)} attempted")

    print("=" * 80)
    print("Investor Master Loaded Successfully")
    print(f"Inserted {inserted} rows")
    print("=" * 80)

    return df