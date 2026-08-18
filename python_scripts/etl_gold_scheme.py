import pandas as pd
import uuid
from datetime import datetime

from sqlalchemy import text

from utils.db import engine, master_engine


# =====================================================
# FIXED SCHEME UUID NAMESPACE
# NEVER CHANGE THIS VALUE
# =====================================================

SCHEME_NAMESPACE = uuid.UUID(
    "a3f8c9d1-5b8e-4f11-9d2a-7e6c4b8f1234"
)


# =====================================================
# COMMON CLEANING
# =====================================================

def clean_text(series):

    return (
        series
        .astype("string")
        .str.strip()
        .str.upper()
    )


def clean_value(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    if value.upper() in ["", "<NA>", "NAN", "NONE"]:
        return None

    return value.upper()


def normalize_scheme_name(series):

    s = (
        series
        .astype("string")
        .str.upper()
        .str.strip()
    )

    s = s.str.replace(r"[^A-Z0-9]+", " ", regex=True)
    s = s.str.replace(r"\s+", " ", regex=True)
    s = s.str.strip()

    return s


# =====================================================
# EXTRACT SILVER DATA
# =====================================================

def extract_scheme():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD SCHEME")
    print("=" * 80)

    transaction_query = """

        SELECT
            source,
            amc_code,
            prodcode,
            scheme,
            funddesc,
            scheme_type,
            brokcode,
            src_brk_code
        FROM silver.transaction_master_new

    """

    investor_query = """

        SELECT
            source,
            amc_code,
            product_code,
            scheme_name,
            fund_description,
            categorydesc
        FROM silver.investor_master

    """

    transaction_df = pd.read_sql(
        transaction_query,
        engine
    )

    investor_df = pd.read_sql(
        investor_query,
        engine
    )

    print("\nExtraction Completed")
    print("-" * 80)

    print(
        "Transaction Rows :",
        len(transaction_df)
    )

    print(
        "Investor Rows    :",
        len(investor_df)
    )

    return transaction_df, investor_df


# =====================================================
# LOAD SCHEME MAPPING
#
# ONLY SOURCE USED TO POPULATE amfi_code
#
# RTA + RTA Scheme Code
#          ↓
# bronze.scheme_mapping
#          ↓
# amfi_scheme_code
# =====================================================

def load_scheme_mapping():

    print("=" * 80)
    print("LOADING SCHEME MAPPING")
    print("=" * 80)

    query = """

        SELECT
            rta,
            rta_scheme_code,
            amfi_scheme_code
        FROM bronze.scheme_mapping
        WHERE amfi_scheme_code IS NOT NULL

    """

    mapping_df = pd.read_sql(
        query,
        engine
    )

    print(
        "Scheme mapping rows :",
        len(mapping_df)
    )

    if mapping_df.empty:

        print(
            "WARNING: Scheme mapping table is empty."
        )

        return mapping_df

    mapping_df["rta"] = clean_text(
        mapping_df["rta"]
    )

    mapping_df["rta_scheme_code"] = (
        mapping_df["rta_scheme_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    mapping_df["amfi_scheme_code"] = (
        mapping_df["amfi_scheme_code"]
        .astype("string")
        .str.strip()
    )

    mapping_df = mapping_df[
        mapping_df["rta"].notna()
        &
        mapping_df["rta"].ne("")
        &
        mapping_df["rta"].ne("<NA>")
        &
        mapping_df["rta_scheme_code"].notna()
        &
        mapping_df["rta_scheme_code"].ne("")
        &
        mapping_df["rta_scheme_code"].ne("<NA>")
        &
        mapping_df["amfi_scheme_code"].notna()
        &
        mapping_df["amfi_scheme_code"].ne("")
        &
        mapping_df["amfi_scheme_code"].ne("<NA>")
    ].copy()

    conflict_check = (
        mapping_df
        .groupby(
            [
                "rta",
                "rta_scheme_code"
            ]
        )["amfi_scheme_code"]
        .nunique()
        .reset_index(name="amfi_count")
    )

    conflicts = conflict_check[
        conflict_check["amfi_count"] > 1
    ]

    if not conflicts.empty:

        print(
            "\nERROR: Conflicting AMFI mappings found."
        )

        print(
            conflicts.to_string(index=False)
        )

        raise Exception(
            "One RTA + RTA scheme code maps to "
            "multiple AMFI scheme codes."
        )

    mapping_df = (
        mapping_df
        .drop_duplicates(
            subset=[
                "rta",
                "rta_scheme_code"
            ],
            keep="first"
        )
        .reset_index(drop=True)
    )

    print(
        "Valid scheme mappings :",
        len(mapping_df)
    )

    return mapping_df


# =====================================================
# LOAD AMFI MASTER
#
# NOTE:
# ISIN COLUMNS ARE INTENTIONALLY NOT LOADED.
# gold.scheme.isin WILL REMAIN NULL.
# =====================================================

def load_amfi_master():

    print("=" * 80)
    print("LOADING AMFI SCHEME MASTER")
    print("=" * 80)

    query = """

        SELECT
            amfi_scheme_code,
            amfi_unique_code,
            amc_code,
            amc_name,
            scheme_name,
            scheme_nav_name,
            scheme_type,
            scheme_category,
            asset_category,
            sub_category,
            plan_type,
            option_type,
            min_amount,
            launch_date,
            closure_date,
            risk_category,
            status,
            created_at,
            updated_at,
            is_deleted,
            deleted_at,
            created_by,
            updated_by
        FROM public.amfi_scheme_master

    """

    amfi_df = pd.read_sql(
        query,
        master_engine
    )

    print(
        "AMFI master rows :",
        len(amfi_df)
    )

    if amfi_df.empty:

        print(
            "WARNING: AMFI scheme master is empty."
        )

        return amfi_df

    amfi_df["amfi_scheme_code"] = (
        amfi_df["amfi_scheme_code"]
        .astype("string")
        .str.strip()
    )

    amfi_df["amfi_unique_code"] = (
        amfi_df["amfi_unique_code"]
        .astype("string")
        .str.strip()
    )

    amfi_df["amc_code"] = clean_text(
        amfi_df["amc_code"]
    )

    amfi_df["scheme_name"] = (
        amfi_df["scheme_name"]
        .astype("string")
        .str.strip()
    )

    amfi_df["name_norm"] = normalize_scheme_name(
        amfi_df["scheme_name"]
    )

    amfi_df = amfi_df[
        amfi_df["amfi_scheme_code"].notna()
        &
        amfi_df["amfi_scheme_code"].ne("")
        &
        amfi_df["amfi_scheme_code"].ne("<NA>")
    ].copy()

    amfi_df = (
        amfi_df
        .drop_duplicates(
            subset=[
                "amc_code",
                "name_norm"
            ],
            keep="first"
        )
        .reset_index(drop=True)
    )

    print(
        "Valid AMFI master rows :",
        len(amfi_df)
    )

    return amfi_df


# =====================================================
# LOAD AMC MASTER
# =====================================================

def load_amc_master():

    print("=" * 80)
    print("LOADING AMC MASTER")
    print("=" * 80)

    query = """

        SELECT
            amc_id,
            amc_code
        FROM bronze.amc_master

    """

    amc_df = pd.read_sql(
        query,
        engine
    )

    if amc_df.empty:
        return amc_df

    amc_df["amc_code"] = clean_text(
        amc_df["amc_code"]
    )

    amc_df = (
        amc_df
        .drop_duplicates(
            subset=["amc_code"],
            keep="first"
        )
    )

    return amc_df


# =====================================================
# TRANSFORM GOLD SCHEME
# =====================================================

def transform_scheme(
    transaction_df,
    investor_df
):

    print("=" * 80)
    print("TRANSFORMING GOLD SCHEME")
    print("=" * 80)

    transaction_df = transaction_df.copy()
    investor_df = investor_df.copy()

    # =================================================
    # NORMALIZE SOURCE
    # =================================================

    transaction_df["source"] = clean_text(
        transaction_df["source"]
    )

    investor_df["source"] = clean_text(
        investor_df["source"]
    )

    # =================================================
    # NORMALIZE AMC CODE
    # =================================================

    transaction_df["amc_code"] = clean_text(
        transaction_df["amc_code"]
    )

    investor_df["amc_code"] = clean_text(
        investor_df["amc_code"]
    )

    # =================================================
    # RTA SCHEME CODE
    # =================================================

    transaction_df["scheme_code"] = clean_text(
        transaction_df["prodcode"]
    )

    investor_df["scheme_code"] = clean_text(
        investor_df["product_code"]
    )

    # =================================================
    # REMOVE INVALID CODES
    # =================================================

    transaction_df = transaction_df[
        transaction_df["scheme_code"].notna()
        &
        transaction_df["scheme_code"].ne("")
        &
        transaction_df["scheme_code"].ne("<NA>")
    ].copy()

    investor_df = investor_df[
        investor_df["scheme_code"].notna()
        &
        investor_df["scheme_code"].ne("")
        &
        investor_df["scheme_code"].ne("<NA>")
    ].copy()

    # =================================================
    # ARN
    # =================================================

    transaction_df["arn"] = (
        transaction_df["brokcode"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:50]
    )

    transaction_df.loc[
        transaction_df["arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "arn"
    ] = pd.NA

    # =================================================
    # SUB ARN
    # =================================================

    transaction_df["sub_arn"] = (
        transaction_df["src_brk_code"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:50]
    )

    transaction_df.loc[
        transaction_df["sub_arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "sub_arn"
    ] = pd.NA

    # =================================================
    # UNIQUE TRANSACTION SCHEMES
    # =================================================

    transaction_scheme = (
        transaction_df
        .drop_duplicates(
            subset=[
                "source",
                "amc_code",
                "scheme_code"
            ],
            keep="first"
        )
        .copy()
    )

    # =================================================
    # UNIQUE INVESTOR SCHEMES
    # =================================================

    investor_scheme = (
        investor_df
        .drop_duplicates(
            subset=[
                "source",
                "amc_code",
                "scheme_code"
            ],
            keep="first"
        )
        .copy()
    )

    print(
        "\nUnique Transaction Schemes :",
        len(transaction_scheme)
    )

    print(
        "Unique Investor Schemes    :",
        len(investor_scheme)
    )

    # =================================================
    # RENAME INVESTOR COLUMNS
    # =================================================

    investor_scheme = investor_scheme.rename(
        columns={
            "scheme_name": "investor_scheme_name",
            "fund_description": "investor_fund_description",
            "categorydesc": "investor_category"
        }
    )

    # =================================================
    # MERGE TRANSACTION + INVESTOR
    # =================================================

    gold_base = transaction_scheme.merge(

        investor_scheme[
            [
                "source",
                "amc_code",
                "scheme_code",
                "investor_scheme_name",
                "investor_fund_description",
                "investor_category"
            ]
        ],

        on=[
            "source",
            "amc_code",
            "scheme_code"
        ],

        how="left"

    )

    print("\nRows After Transaction + Investor Merge")
    print("-" * 80)

    print(len(gold_base))

    # =================================================
    # SCHEME NAME
    # =================================================

    scheme_name = (

        gold_base["funddesc"]

        .combine_first(
            gold_base["scheme"]
        )

        .combine_first(
            gold_base["investor_scheme_name"]
        )

        .combine_first(
            gold_base["investor_fund_description"]
        )

    )

    scheme_name = (
        scheme_name
        .astype("string")
        .str.strip()
    )

    # =================================================
    # CATEGORY
    # =================================================

    category = (

        gold_base["scheme_type"]

        .combine_first(
            gold_base["investor_category"]
        )

    )

    # =================================================
    # PLAN
    # =================================================

    plan = (
        scheme_name
        .str.extract(
            r"(?i)\b(Direct|Regular)\b",
            expand=False
        )
    )

    # =================================================
    # CREATE GOLD DATAFRAME
    #
    # IMPORTANT:
    # isin IS INTENTIONALLY NULL.
    # =================================================

    gold_df = pd.DataFrame({

        "rta":
            gold_base["source"],

        "scheme_code":
            gold_base["scheme_code"],

        "scheme_name":
            scheme_name,

        "category":
            category,

        "plan":
            plan,

        # =================================================
        # ISIN REMOVED
        # KEEP NULL
        # =================================================

        "isin":
            None,

        "amc_code":
            gold_base["amc_code"],

        "amfi_code":
            None,

        "category_id":
            None,

        "plan_type":
            None,

        "option_type":
            None,

        "rta_scheme_code":
            gold_base["scheme_code"],

        "benchmark_id":
            None,

        "expense_ratio":
            None,

        "exit_load_json":
            None,

        "lock_in_months":
            None,

        "riskometer":
            None,

        "status":
            None,

        "arn":
            gold_base["arn"],

        "sub_arn":
            gold_base["sub_arn"]

    })

    # =================================================
    # CLEAN BASIC COLUMNS
    # =================================================

    gold_df["rta"] = clean_text(
        gold_df["rta"]
    )

    gold_df["scheme_code"] = clean_text(
        gold_df["scheme_code"]
    )

    gold_df["amc_code"] = clean_text(
        gold_df["amc_code"]
    )

    gold_df = gold_df[
        gold_df["scheme_code"].notna()
        &
        gold_df["scheme_code"].ne("")
        &
        gold_df["scheme_code"].ne("<NA>")
    ].copy()

    # =================================================
    # INTERNAL NORMALIZED NAME
    # =================================================

    gold_df["_name_norm"] = normalize_scheme_name(
        gold_df["scheme_name"]
    )

    # =================================================
    # AUTHORITATIVE AMFI CODE MAPPING
    #
    # RTA + RTA SCHEME CODE
    #          ↓
    # bronze.scheme_mapping
    #          ↓
    # amfi_scheme_code
    #
    # THIS IS THE AMFI CODE LOGIC
    # =================================================

    scheme_mapping_df = load_scheme_mapping()

    if not scheme_mapping_df.empty:

        mapping_key = (
            scheme_mapping_df["rta"].fillna("")
            + "|"
            + scheme_mapping_df["rta_scheme_code"].fillna("")
        )

        mapping_lookup = pd.Series(
            scheme_mapping_df["amfi_scheme_code"].values,
            index=mapping_key
        ).to_dict()

        gold_key = (
            gold_df["rta"].fillna("")
            + "|"
            + gold_df["rta_scheme_code"].fillna("")
        )

        gold_df["amfi_code"] = gold_key.map(
            mapping_lookup
        )

    else:

        print(
            "WARNING: Scheme mapping empty. "
            "AMFI code mapping skipped."
        )

    # =================================================
    # LOAD AMFI MASTER
    # =================================================

    amfi_df = load_amfi_master()

    # =================================================
    # AMFI MASTER ATTRIBUTES
    #
    # ISIN IS NOT INCLUDED.
    # =================================================

    if not amfi_df.empty:

        amfi_attribute_df = (
            amfi_df[
                [
                    "amfi_scheme_code",
                    "scheme_type",
                    "scheme_category",
                    "plan_type",
                    "option_type",
                    "risk_category",
                    "status"
                ]
            ]
            .drop_duplicates(
                subset=["amfi_scheme_code"],
                keep="first"
            )
        )

        amfi_attr = (
            amfi_attribute_df
            .set_index("amfi_scheme_code")
        )

        scheme_category_map = (
            amfi_attr["scheme_category"]
            .to_dict()
        )

        plan_type_map = (
            amfi_attr["plan_type"]
            .to_dict()
        )

        option_type_map = (
            amfi_attr["option_type"]
            .to_dict()
        )

        risk_map = (
            amfi_attr["risk_category"]
            .to_dict()
        )

        status_map = (
            amfi_attr["status"]
            .to_dict()
        )

        # =================================================
        # AMFI ATTRIBUTE ENRICHMENT
        # =================================================

        gold_df["category"] = (
            gold_df["amfi_code"]
            .map(scheme_category_map)
            .combine_first(
                gold_df["category"]
            )
        )

        gold_df["plan_type"] = (
            gold_df["amfi_code"]
            .map(plan_type_map)
        )

        gold_df["option_type"] = (
            gold_df["amfi_code"]
            .map(option_type_map)
        )

        gold_df["riskometer"] = (
            gold_df["amfi_code"]
            .map(risk_map)
        )

        gold_df["status"] = (
            gold_df["amfi_code"]
            .map(status_map)
        )

        # =================================================
        # PLAN
        # =================================================

        gold_df["plan"] = (
            gold_df["plan_type"]
            .combine_first(
                gold_df["plan"]
            )
        )

    else:

        print(
            "WARNING: AMFI master empty. "
            "AMFI enrichment skipped."
        )

    # =================================================
    # FORCE ISIN TO NULL
    #
    # This makes sure that no future logic accidentally
    # populates it.
    # =================================================

    gold_df["isin"] = None

    # =================================================
    # AMFI CODE CLEANING
    # =================================================

    gold_df["amfi_code"] = (
        gold_df["amfi_code"]
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["amfi_code"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "amfi_code"
    ] = pd.NA

    # =================================================
    # ARN CLEANING
    # =================================================

    gold_df["arn"] = (
        gold_df["arn"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:50]
    )

    gold_df.loc[
        gold_df["arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "arn"
    ] = pd.NA

    # =================================================
    # SUB ARN CLEANING
    # =================================================

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:50]
    )

    gold_df.loc[
        gold_df["sub_arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "sub_arn"
    ] = pd.NA

    # =================================================
    # AMC ID
    # =================================================

    amc_master = load_amc_master()

    if not amc_master.empty:

        amc_lookup = (
            amc_master
            .drop_duplicates(
                subset=["amc_code"],
                keep="first"
            )
            .set_index("amc_code")["amc_id"]
            .to_dict()
        )

        gold_df["amc_id"] = (
            gold_df["amc_code"]
            .map(amc_lookup)
        )

    else:

        gold_df["amc_id"] = None

    # =================================================
    # DROP TEMPORARY COLUMNS
    # =================================================

    gold_df.drop(
        columns=[
            "amc_code",
            "_name_norm"
        ],
        inplace=True,
        errors="ignore"
    )

    # =================================================
    # FINAL DEDUPLICATION
    # =================================================

    print("\nRows Before Final Dedup")
    print("-" * 80)

    print(len(gold_df))

    gold_df = (
        gold_df
        .sort_values(
            [
                "rta",
                "scheme_code"
            ]
        )
        .drop_duplicates(
            subset=[
                "rta",
                "scheme_code"
            ],
            keep="first"
        )
        .reset_index(drop=True)
    )

    print("\nRows After Final Dedup")
    print("-" * 80)

    print(len(gold_df))

    # =================================================
    # GENERATE DETERMINISTIC UUID
    # =================================================

    gold_df["id"] = gold_df.apply(

        lambda x: uuid.uuid5(

            SCHEME_NAMESPACE,

            f"{str(x['rta']).strip().lower()}|"
            f"{str(x['scheme_code']).strip().lower()}"

        ),

        axis=1
    )

    # =================================================
    # UUID VALIDATION
    # =================================================

    duplicate_ids = gold_df[
        gold_df.duplicated(
            subset=["id"],
            keep=False
        )
    ]

    if not duplicate_ids.empty:

        print("\nDuplicate UUIDs Found")
        print("-" * 80)

        print(
            duplicate_ids[
                [
                    "rta",
                    "scheme_code",
                    "scheme_name",
                    "id"
                ]
            ]
        )

        raise Exception(
            "Duplicate UUIDs generated."
        )

    print(
        "\nUUID Validation Passed"
    )

    # =================================================
    # CREATED AT
    # =================================================

    gold_df["created_at"] = datetime.now()

    # =================================================
    # FINAL COLUMN ORDER
    # =================================================

    final_columns = [

        "id",
        "rta",
        "scheme_code",
        "scheme_name",
        "category",
        "plan",
        "isin",
        "amfi_code",
        "category_id",
        "plan_type",
        "option_type",
        "rta_scheme_code",
        "benchmark_id",
        "expense_ratio",
        "exit_load_json",
        "lock_in_months",
        "riskometer",
        "status",
        "arn",
        "sub_arn",
        "amc_id",
        "created_at"

    ]

    gold_df = gold_df[
        final_columns
    ].copy()

    # =================================================
    # PRINT FINAL COLUMNS
    # =================================================

    print("\nFINAL GOLD.SCHEME COLUMNS")
    print("-" * 80)

    print(
        list(gold_df.columns)
    )

    # =================================================
    # CHECK UNEXPECTED SUFFIX COLUMNS
    # =================================================

    bad_columns = [
        c
        for c in gold_df.columns
        if c.endswith("_x")
        or c.endswith("_y")
    ]

    if bad_columns:

        raise Exception(
            f"ERROR: Pandas generated unexpected columns: "
            f"{bad_columns}"
        )

    # =================================================
    # VALIDATION
    # =================================================

    print("\nAMFI CODE Validation")
    print("-" * 80)

    print(
        "AMFI code populated :",
        gold_df["amfi_code"].notna().sum()
    )

    print(
        "AMFI code null      :",
        gold_df["amfi_code"].isna().sum()
    )

    # =================================================
    # ISIN VALIDATION
    # =================================================

    print("\nISIN Validation")
    print("-" * 80)

    print(
        "ISIN populated      :",
        gold_df["isin"].notna().sum()
    )

    print(
        "ISIN null            :",
        gold_df["isin"].isna().sum()
    )

    if gold_df["isin"].notna().any():

        raise Exception(
            "ERROR: ISIN should remain NULL for all schemes."
        )

    # =================================================
    # ARN VALIDATION
    # =================================================

    print("\nARN Validation")
    print("-" * 80)

    print(
        "ARN populated       :",
        gold_df["arn"].notna().sum()
    )

    print(
        "ARN null            :",
        gold_df["arn"].isna().sum()
    )

    print(
        "SUB ARN populated   :",
        gold_df["sub_arn"].notna().sum()
    )

    print(
        "SUB ARN null        :",
        gold_df["sub_arn"].isna().sum()
    )

    print("\nTransformation Completed")
    print("-" * 80)

    print(
        "Gold Rows :",
        len(gold_df)
    )

    # ============================================================
    # CHECK VARCHAR COLUMN LENGTHS BEFORE GOLD INSERT
    # ============================================================

    print("\nVARCHAR LENGTH CHECK")
    print("-" * 80)

    varchar_limits = {
        "rta": 10,
        "scheme_code": 30,
        "scheme_name": 255,
        "category": 100,
        "plan": 40,
        "isin": 20,
        "amfi_code": 20,
        "plan_type": 20,
        "option_type": 20,
        "rta_scheme_code": 30,
        "riskometer": 20,
        "status": 20,
        "arn": 50,
        "sub_arn": 50,
    }

    for col, limit in varchar_limits.items():

        if col not in gold_df.columns:
            continue

        bad = gold_df[
            gold_df[col].notna()
            &
            (
                gold_df[col]
                .astype(str)
                .str.len()
                > limit
            )
        ]

        if len(bad) > 0:

            print(
                f"\n❌ {col}: {len(bad)} values exceed VARCHAR({limit})"
            )

            print(
                bad[[col]]
                .assign(
                    length=bad[col]
                    .astype(str)
                    .str.len()
                )
                .to_string(index=False)
            )

        else:

            print(
                f"✅ {col}: OK"
            )

    # =================================================
    # GOLD SCHEME PREVIEW
    # =================================================

    print("\nGold Scheme Preview")
    print("-" * 80)

    print(
        gold_df[
            [
                "id",
                "rta",
                "scheme_code",
                "scheme_name",
                "amfi_code",
                "category",
                "plan",
                "plan_type",
                "option_type",
                "isin",
                "rta_scheme_code",
                "amc_id",
                "arn",
                "sub_arn",
                "status"
            ]
        ]
        .head(10)
        .to_string(index=False)
    )

    return gold_df


# =====================================================
# LOAD GOLD.SCHEME
# =====================================================

def load_scheme(gold_df):

    print("=" * 80)
    print("LOADING GOLD SCHEME")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No scheme records to process."
        )

        return True

    # =================================================
    # FINAL DATABASE COLUMN CHECK
    # =================================================

    expected_columns = [

        "id",
        "rta",
        "scheme_code",
        "scheme_name",
        "category",
        "plan",
        "isin",
        "amfi_code",
        "category_id",
        "plan_type",
        "option_type",
        "rta_scheme_code",
        "benchmark_id",
        "expense_ratio",
        "exit_load_json",
        "lock_in_months",
        "riskometer",
        "status",
        "arn",
        "sub_arn",
        "amc_id",
        "created_at"

    ]

    gold_df = gold_df[
        [
            c
            for c in expected_columns
            if c in gold_df.columns
        ]
    ].copy()

    unexpected = [
        c
        for c in gold_df.columns
        if c not in expected_columns
    ]

    if unexpected:

        raise Exception(
            f"Unexpected columns before insert: {unexpected}"
        )

    bad_columns = [
        c
        for c in gold_df.columns
        if c.endswith("_x")
        or c.endswith("_y")
    ]

    if bad_columns:

        raise Exception(
            f"x/y columns detected before database insert: "
            f"{bad_columns}"
        )

    # =================================================
    # NORMALIZE KEYS
    # =================================================

    gold_df["rta"] = clean_text(
        gold_df["rta"]
    )

    gold_df["scheme_code"] = clean_text(
        gold_df["scheme_code"]
    )

    # =================================================
    # READ EXISTING GOLD SCHEMES
    # =================================================

    existing_scheme = pd.read_sql(

        """
        SELECT
            id,
            rta,
            scheme_code,
            created_at
        FROM gold.scheme
        """,

        engine
    )

    if not existing_scheme.empty:

        existing_scheme["rta"] = clean_text(
            existing_scheme["rta"]
        )

        existing_scheme["scheme_code"] = clean_text(
            existing_scheme["scheme_code"]
        )

    print(
        "\nExisting schemes :",
        len(existing_scheme)
    )

    # =================================================
    # EXISTING KEYS
    # =================================================

    existing_keys = set(
        zip(
            existing_scheme["rta"],
            existing_scheme["scheme_code"]
        )
    )

    # =================================================
    # UPDATE COLUMNS
    #
    # ISIN IS INTENTIONALLY NOT UPDATED.
    # =================================================

    update_columns = [

        "scheme_name",
        "category",
        "plan",

        # ISIN REMOVED FROM UPDATE
        # "isin",

        "amfi_code",
        "category_id",
        "plan_type",
        "option_type",
        "rta_scheme_code",
        "benchmark_id",
        "expense_ratio",
        "exit_load_json",
        "lock_in_months",
        "riskometer",
        "status",
        "arn",
        "sub_arn",
        "amc_id"

    ]

    # =================================================
    # UPDATE EXISTING
    # =================================================

    existing_updates = 0

    print(
        "\nUpdating existing schemes..."
    )

    print("-" * 80)

    with engine.begin() as connection:

        for _, row in gold_df.iterrows():

            key = (
                row["rta"],
                row["scheme_code"]
            )

            if key not in existing_keys:
                continue

            set_parts = []

            params = {
                "id": row["id"]
            }

            for column in update_columns:

                set_parts.append(
                    f'"{column}" = :{column}'
                )

                value = row[column]

                if pd.isna(value):
                    value = None

                params[column] = value

            sql = text(
                f"""
                UPDATE gold.scheme
                SET
                    {", ".join(set_parts)}
                WHERE
                    id = :id
                """
            )

            result = connection.execute(
                sql,
                params
            )

            if result.rowcount > 0:
                existing_updates += 1

    print(
        "Existing schemes updated :",
        existing_updates
    )

    # =================================================
    # FIND NEW SCHEMES
    # =================================================

    new_mask = ~gold_df.apply(
        lambda row:
        (
            row["rta"],
            row["scheme_code"]
        ) in existing_keys,
        axis=1
    )

    new_gold_df = gold_df[
        new_mask
    ].copy()

    print(
        "\nNew schemes to insert :",
        len(new_gold_df)
    )

    # =================================================
    # INSERT NEW SCHEMES
    # =================================================

    if not new_gold_df.empty:

        new_gold_df = new_gold_df[
            expected_columns
        ].copy()

        # =================================================
        # FORCE ISIN NULL BEFORE INSERT
        # =================================================

        new_gold_df["isin"] = None

        bad_columns = [
            c
            for c in new_gold_df.columns
            if c.endswith("_x")
            or c.endswith("_y")
        ]

        if bad_columns:

            raise Exception(
                f"Cannot insert. Unexpected x/y columns: "
                f"{bad_columns}"
            )

        print("\nColumns being inserted:")
        print(list(new_gold_df.columns))

        try:

            new_gold_df.to_sql(

                name="scheme",

                schema="gold",

                con=engine,

                if_exists="append",

                index=False,

                chunksize=1000

            )

            print(
                "New schemes inserted :",
                len(new_gold_df)
            )

        except Exception as e:

            print(
                "\nERROR WHILE INSERTING NEW SCHEMES"
            )

            print(
                type(e).__name__
            )

            print(e)

            return False

    else:

        print(
            "No new schemes to insert"
        )

    # =================================================
    # FINAL RESULT
    # =================================================

    print("\nLoad Completed")
    print("-" * 80)

    print(
        "Existing Updated :",
        existing_updates
    )

    print(
        "New Inserted     :",
        len(new_gold_df)
    )

    return True


# =====================================================
# MAIN
# =====================================================

def main():

    print("\n")

    print("=" * 80)
    print("STARTING GOLD SCHEME ETL")
    print("=" * 80)

    try:

        # =================================================
        # EXTRACT
        # =================================================

        transaction_df, investor_df = extract_scheme()

        # =================================================
        # TRANSFORM
        # =================================================

        gold_scheme = transform_scheme(
            transaction_df,
            investor_df
        )

        # =================================================
        # LOAD
        # =================================================

        status = load_scheme(
            gold_scheme
        )

        if status:

            print("\n")

            print("=" * 80)
            print(
                "GOLD SCHEME ETL COMPLETED SUCCESSFULLY"
            )
            print("=" * 80)

        else:

            print("\n")

            print("=" * 80)
            print(
                "GOLD SCHEME ETL FAILED"
            )
            print("=" * 80)

    except Exception as e:

        print("\n")

        print("=" * 80)
        print(
            "GOLD SCHEME ETL ERROR"
        )
        print("=" * 80)

        print(
            type(e).__name__
        )

        print(e)


# =====================================================
# EXECUTE
# =====================================================

if __name__ == "__main__":
    main()