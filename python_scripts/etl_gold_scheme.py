import pandas as pd
import uuid
import re
from datetime import datetime

from sqlalchemy import text

from utils.db import engine
from utils.db import master_engine


# =====================================================
# FIXED SCHEME UUID NAMESPACE
#
# NEVER CHANGE THIS VALUE
# =====================================================

SCHEME_NAMESPACE = uuid.UUID(
    "a3f8c9d1-5b8e-4f11-9d2a-7e6c4b8f1234"
)


# =====================================================
# LOAD SCHEME MASTER
# =====================================================

scheme_master = pd.read_sql(
    """
    SELECT
        id,
        scheme_code,
        name,
        name_norm,
        name_norm_loose
    FROM public.scheme_master
    """,
    master_engine
)


# =====================================================
# COMMON CLEANING FUNCTIONS
# =====================================================

def clean_text(series):

    return (
        series
        .astype("string")
        .str.strip()
        .str.upper()
    )


def first_valid(series):

    series = series.dropna()

    series = (
        series
        .astype("string")
        .str.strip()
    )

    series = series[
        series.ne("")
        &
        series.ne("<NA>")
        &
        series.ne("NAN")
        &
        series.ne("NONE")
    ]

    if series.empty:
        return None

    return series.iloc[0]


# =====================================================
# EXTRACT GOLD SCHEME DATA
# =====================================================

def extract_scheme():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD SCHEME")
    print("=" * 80)

    # =================================================
    # TRANSACTION MASTER
    # =================================================

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

    # =================================================
    # INVESTOR MASTER
    # =================================================

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

    print("\nTransaction Preview")
    print("-" * 80)

    print(
        transaction_df.head()
    )

    print("\nInvestor Preview")
    print("-" * 80)

    print(
        investor_df.head()
    )

    return (
        transaction_df,
        investor_df
    )


# =====================================================
# TRANSFORM GOLD SCHEME DATA
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
    # CLEAN SCHEME CODE
    # =================================================

    transaction_df["join_scheme_code"] = (

        transaction_df["prodcode"]

        .astype("string")

        .str.strip()

        .str.upper()

    )

    investor_df["join_scheme_code"] = (

        investor_df["product_code"]

        .astype("string")

        .str.strip()

        .str.upper()

    )

    # =================================================
    # REMOVE INVALID SCHEME CODES
    # =================================================

    transaction_df = transaction_df[
        transaction_df["join_scheme_code"].notna()
        &
        transaction_df["join_scheme_code"].ne("")
        &
        transaction_df["join_scheme_code"].ne("<NA>")
    ].copy()

    investor_df = investor_df[
        investor_df["join_scheme_code"].notna()
        &
        investor_df["join_scheme_code"].ne("")
        &
        investor_df["join_scheme_code"].ne("<NA>")
    ].copy()

    # =================================================
    # CLEAN ARN
    #
    # arn <- brokcode
    # =================================================

    transaction_df["arn"] = (
        transaction_df["brokcode"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    transaction_df.loc[
        transaction_df["arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "arn"
    ] = pd.NA

    transaction_df["arn"] = (
        transaction_df["arn"]
        .str[:50]
    )

    # =================================================
    # CLEAN SUB ARN
    #
    # sub_arn <- src_brk_code
    # =================================================

    transaction_df["sub_arn"] = (
        transaction_df["src_brk_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    transaction_df.loc[
        transaction_df["sub_arn"].isin(
            ["", "<NA>", "NAN", "NONE"]
        ),
        "sub_arn"
    ] = pd.NA

    transaction_df["sub_arn"] = (
        transaction_df["sub_arn"]
        .str[:50]
    )

    # =================================================
    # REMOVE SOURCE DUPLICATES
    #
    # One row per:
    # source + amc_code + scheme_code
    # =================================================

    transaction_scheme = (

        transaction_df

        .drop_duplicates(
            subset=[
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            keep="first"
        )

    )

    investor_scheme = (

        investor_df

        .drop_duplicates(
            subset=[
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            keep="first"
        )

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
    # MERGE TRANSACTION + INVESTOR
    # =================================================

    gold_df = transaction_scheme.merge(

        investor_scheme[
            [
                "source",
                "amc_code",
                "join_scheme_code",
                "scheme_name",
                "fund_description",
                "categorydesc"
            ]
        ],

        on=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],

        how="left",

        suffixes=(
            "_txn",
            "_inv"
        )

    )

    print("\nRows After Merge")
    print("-" * 80)

    print(
        len(gold_df)
    )

    # =================================================
    # SCHEME NAME PRIORITY
    #
    # Transaction:
    # funddesc
    #     ↓
    # scheme
    #
    # Investor:
    # scheme_name
    #     ↓
    # fund_description
    # =================================================

    scheme_name = (

        gold_df["funddesc"]

        .combine_first(
            gold_df["scheme"]
        )

        .combine_first(
            gold_df["scheme_name"]
        )

        .combine_first(
            gold_df["fund_description"]
        )

    )

    scheme_name = (
        scheme_name
        .astype("string")
        .str.strip()
    )

    # =================================================
    # CREATE GOLD DATAFRAME
    # =================================================

    gold_df = pd.DataFrame(
        {

            "rta":
                gold_df["source"],

            "scheme_code":
                gold_df["join_scheme_code"],

            "scheme_name":
                scheme_name,

            "category":
                gold_df["scheme_type"]
                .combine_first(
                    gold_df["categorydesc"]
                ),

            "plan":
                scheme_name
                .str.extract(
                    r"(?i)\b(Direct|Regular)\b",
                    expand=False
                ),

            "isin":
                None,

            "amc_code":
                gold_df["amc_code"],

            "amfi_code":
                None,

            "category_id":
                None,

            "plan_type":
                None,

            "option_type":
                None,

            "rta_scheme_code":
                gold_df["join_scheme_code"],

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

            # =========================================
            # ARN
            # =========================================

            "arn":
                gold_df["arn"],

            # =========================================
            # SUB ARN
            # =========================================

            "sub_arn":
                gold_df["sub_arn"]

        }
    )

    # =================================================
    # NORMALIZE SCHEME NAME
    # =================================================

    def normalize_name(name):

        if pd.isna(name):
            return None

        name = str(name).upper()

        name = re.sub(
            r"[^A-Z0-9 ]",
            " ",
            name
        )

        name = re.sub(
            r"\s+",
            " ",
            name
        ).strip()

        return name

    gold_df["name_norm"] = (
        gold_df["scheme_name"]
        .apply(normalize_name)
    )

    # =================================================
    # SCHEME MASTER LOOKUP
    # =================================================

    gold_df = gold_df.merge(

        scheme_master,

        on="name_norm",

        how="left",

        suffixes=(
            "",
            "_master"
        )

    )

    # =================================================
    # AMFI CODE
    # =================================================

    gold_df["amfi_code"] = (
        gold_df["scheme_code_master"]
    )

    print("\nScheme Master Match")
    print("-" * 80)

    print(
        "Total     :",
        len(gold_df)
    )

    print(
        "Matched   :",
        gold_df["id"].notna().sum()
    )

    print(
        "Unmatched :",
        gold_df["id"].isna().sum()
    )

    # =================================================
    # CLEAN SCHEME CODE
    # =================================================

    gold_df["scheme_code"] = clean_text(
        gold_df["scheme_code"]
    )

    gold_df = gold_df[
        gold_df["scheme_code"].notna()
        &
        gold_df["scheme_code"].ne("")
    ].copy()

    # =================================================
    # CLEAN ARN / SUB ARN AFTER MERGE
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
    # AMC ID LOOKUP
    # =================================================

    amc_master = pd.read_sql(

        """
        SELECT
            amc_id,
            amc_code
        FROM bronze.amc_master
        """,

        engine

    )

    amc_master["amc_code"] = clean_text(
        amc_master["amc_code"]
    )

    gold_df["amc_code"] = clean_text(
        gold_df["amc_code"]
    )

    gold_df = gold_df.merge(
        amc_master,
        on="amc_code",
        how="left"
    )

    # =================================================
    # DROP TEMPORARY COLUMNS
    # =================================================

    gold_df.drop(
        columns=[
            "amc_code",
            "id",
            "name",
            "name_norm",
            "name_norm_loose",
            "scheme_code_master"
        ],
        inplace=True,
        errors="ignore"
    )

    # =================================================
    # FINAL DEDUPLICATION
    #
    # ARN / SUB ARN are taken from the first
    # transaction row for each scheme.
    # =================================================

    print("\nRows Before Final Dedup")
    print("-" * 80)

    print(
        len(gold_df)
    )

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

        .reset_index(
            drop=True
        )

    )

    print("\nRows After Final Dedup")
    print("-" * 80)

    print(
        len(gold_df)
    )

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

    cols = [
        "id"
    ] + [
        c
        for c in gold_df.columns
        if c != "id"
    ]

    gold_df = gold_df[cols]

    # =================================================
    # FINAL VALIDATION
    # =================================================

    print("\nARN Validation")
    print("-" * 80)

    print(
        "ARN populated     :",
        gold_df["arn"].notna().sum()
    )

    print(
        "ARN null          :",
        gold_df["arn"].isna().sum()
    )

    print(
        "SUB ARN populated :",
        gold_df["sub_arn"].notna().sum()
    )

    print(
        "SUB ARN null      :",
        gold_df["sub_arn"].isna().sum()
    )

    print("\nTransformation Completed")
    print("-" * 80)

    print(
        "Gold Rows :",
        len(gold_df)
    )

    print("\nGold Scheme Preview")
    print("-" * 80)

    print(
        gold_df[
            [
                "id",
                "rta",
                "scheme_code",
                "scheme_name",
                "category",
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
# LOAD GOLD SCHEME
#
# Existing schemes are UPDATED.
#
# New schemes are INSERTED.
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
    # =================================================

    update_columns = [

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
        "sub_arn"

    ]

    # =================================================
    # UPDATE EXISTING SCHEMES
    # =================================================

    existing_updates = 0

    print(
        "\nUpdating existing schemes..."
    )

    print(
        "-" * 80
    )

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

    new_gold_df = gold_df[
        ~gold_df.apply(
            lambda row:
                (
                    row["rta"],
                    row["scheme_code"]
                )
                in existing_keys,
            axis=1
        )
    ].copy()

    print(
        "\nNew schemes to insert :",
        len(new_gold_df)
    )

    # =================================================
    # INSERT NEW SCHEMES
    # =================================================

    if not new_gold_df.empty:

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
# MAIN EXECUTION
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