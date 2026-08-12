import pandas as pd
import uuid
import re
from datetime import datetime

from sqlalchemy import text

from utils.db import engine
from utils.db import restore_engine


# =====================================================
# FIXED SCHEME UUID NAMESPACE
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
    restore_engine
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
# OPTION TYPE MAPPING
#
# COMMON MAPPING
#
# G -> Growth
# R -> Regular
#
# NO CAMS / KFIN SPECIFIC LOGIC
# =====================================================

OPTION_MAP = {
    "G": "Growth",
    "R": "Regular"
}


def map_option(series):

    cleaned = clean_text(series)

    return cleaned.map(OPTION_MAP)


# =====================================================
# GET FIRST VALID OPTION FROM MULTIPLE COLUMNS
# =====================================================

def get_first_valid_option(df, columns):

    result = pd.Series(
        pd.NA,
        index=df.index,
        dtype="string"
    )

    for column in columns:

        if column not in df.columns:
            continue

        mapped = map_option(
            df[column]
        )

        valid = (
            result.isna()
            &
            mapped.notna()
        )

        result.loc[valid] = mapped.loc[valid]

    return result


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

        divopt,
        reinvest_flag

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
        categorydesc,

        dividend_option,
        reinv_flag

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
    # NORMALIZE SCHEME CODE
    # =================================================

    transaction_df["join_scheme_code"] = clean_text(
        transaction_df["prodcode"]
    )

    investor_df["join_scheme_code"] = clean_text(
        investor_df["product_code"]
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
    # OPTION SOURCE DEBUG
    # =================================================

    print("\nTransaction Option Values")
    print("-" * 80)

    print("\nDIVOPT:")

    print(
        transaction_df[
            "divopt"
        ]
        .astype("string")
        .str.strip()
        .str.upper()
        .value_counts(dropna=False)
        .head(20)
    )

    print("\nREINVEST_FLAG:")

    print(
        transaction_df[
            "reinvest_flag"
        ]
        .astype("string")
        .str.strip()
        .str.upper()
        .value_counts(dropna=False)
        .head(20)
    )

    # =================================================
    # TRANSACTION OPTION MAPPING
    #
    # PRIMARY SOURCE:
    # silver.transaction_master_new
    #
    # Priority:
    #
    # 1. divopt
    # 2. reinvest_flag
    #
    # Mapping:
    #
    # G -> Growth
    # R -> Regular
    #
    # IMPORTANT:
    # Mapping is performed BEFORE deduplication.
    # =================================================

    transaction_df["transaction_option"] = (
        get_first_valid_option(
            transaction_df,
            [
                "divopt",
                "reinvest_flag"
            ]
        )
    )

    # =================================================
    # TRANSACTION OPTION DEBUG
    # =================================================

    print("\nMapped Transaction Options")
    print("-" * 80)

    print(
        transaction_df[
            transaction_df["transaction_option"].notna()
        ][
            [
                "source",
                "join_scheme_code",
                "divopt",
                "reinvest_flag",
                "transaction_option"
            ]
        ]
        .head(20)
        .to_string(index=False)
    )

    print("\nTransaction Option Mapping Count:")
    print(
        transaction_df[
            "transaction_option"
        ]
        .value_counts(dropna=False)
    )

    # =================================================
    # AGGREGATE TRANSACTION OPTION BY SCHEME
    #
    # ALL transaction rows are considered.
    #
    # Example:
    #
    # Row 1 -> NULL
    # Row 2 -> R
    # Row 3 -> NULL
    #
    # Final -> Regular
    # =================================================

    valid_transaction = transaction_df[
        transaction_df["transaction_option"].notna()
    ].copy()

    transaction_option_scheme = (
        valid_transaction
        .groupby(
            [
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            dropna=False
        )[
            "transaction_option"
        ]
        .agg(first_valid)
        .reset_index()
        .rename(
            columns={
                "transaction_option":
                    "transaction_option_final"
            }
        )
    )

    # =================================================
    # TRANSACTION SCHEME LEVEL DATA
    # =================================================

    transaction_scheme = (
        transaction_df
        .groupby(
            [
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            dropna=False
        )
        .agg(
            {
                "scheme": first_valid,
                "funddesc": first_valid,
                "scheme_type": first_valid
            }
        )
        .reset_index()
    )

    # =================================================
    # ADD TRANSACTION OPTION
    # =================================================

    transaction_scheme = transaction_scheme.merge(
        transaction_option_scheme,
        on=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        how="left"
    )

    # =================================================
    # INVESTOR OPTION MAPPING
    #
    # FALLBACK SOURCE:
    # silver.investor_master
    #
    # Priority:
    #
    # 1. dividend_option
    # 2. reinv_flag
    #
    # G -> Growth
    # R -> Regular
    # =================================================

    investor_df["investor_option"] = (
        get_first_valid_option(
            investor_df,
            [
                "dividend_option",
                "reinv_flag"
            ]
        )
    )

    # =================================================
    # INVESTOR OPTION DEBUG
    # =================================================

    print("\nInvestor Option Values")
    print("-" * 80)

    print("\nDIVIDEND_OPTION:")

    print(
        investor_df[
            "dividend_option"
        ]
        .astype("string")
        .str.strip()
        .str.upper()
        .value_counts(dropna=False)
        .head(20)
    )

    print("\nREINV_FLAG:")

    print(
        investor_df[
            "reinv_flag"
        ]
        .astype("string")
        .str.strip()
        .str.upper()
        .value_counts(dropna=False)
        .head(20)
    )

    print("\nMapped Investor Options:")

    print(
        investor_df[
            investor_df["investor_option"].notna()
        ][
            [
                "source",
                "join_scheme_code",
                "dividend_option",
                "reinv_flag",
                "investor_option"
            ]
        ]
        .head(20)
        .to_string(index=False)
    )

    # =================================================
    # INVESTOR SCHEME DATA
    # =================================================

    investor_scheme = (
        investor_df
        .groupby(
            [
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            dropna=False
        )
        .agg(
            {
                "scheme_name": first_valid,
                "fund_description": first_valid,
                "categorydesc": first_valid
            }
        )
        .reset_index()
    )

    # =================================================
    # INVESTOR OPTION BY SCHEME
    # =================================================

    valid_investor = investor_df[
        investor_df["investor_option"].notna()
    ].copy()

    investor_option_scheme = (
        valid_investor
        .groupby(
            [
                "source",
                "amc_code",
                "join_scheme_code"
            ],
            dropna=False
        )[
            "investor_option"
        ]
        .agg(first_valid)
        .reset_index()
        .rename(
            columns={
                "investor_option":
                    "investor_option_final"
            }
        )
    )

    investor_scheme = investor_scheme.merge(
        investor_option_scheme,
        on=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        how="left"
    )

    # =================================================
    # MERGE TRANSACTION + INVESTOR
    #
    # TRANSACTION = PRIMARY
    # INVESTOR = FALLBACK
    # =================================================

    gold_df = transaction_scheme.merge(
        investor_scheme,
        on=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        how="left"
    )

    print("\nRows After Merge")
    print("-" * 80)

    print(
        len(gold_df)
    )

    # =================================================
    # SCHEME NAME
    #
    # TRANSACTION PRIMARY
    # INVESTOR FALLBACK
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
    # CATEGORY
    # =================================================

    category = (
        gold_df["scheme_type"]
        .combine_first(
            gold_df["categorydesc"]
        )
    )

    # =================================================
    # PLAN TYPE
    # =================================================

    plan_type = (
        scheme_name
        .str.upper()
        .str.contains(
            r"\bDIRECT\b",
            regex=True,
            na=False
        )
        .map(
            {
                True: "DIRECT",
                False: "REGULAR"
            }
        )
    )

    # =================================================
    # FINAL OPTION TYPE
    #
    # TRANSACTION MASTER = PRIMARY
    # INVESTOR MASTER = FALLBACK
    #
    # Transaction:
    # divopt -> reinvest_flag
    #
    # Investor:
    # dividend_option -> reinv_flag
    #
    # G -> Growth
    # R -> Regular
    # =================================================

    transaction_option = (
        gold_df[
            "transaction_option_final"
        ]
        .astype("string")
        .str.strip()
    )

    investor_option = (
        gold_df[
            "investor_option_final"
        ]
        .astype("string")
        .str.strip()
    )

    transaction_valid = (
        transaction_option.notna()
        &
        transaction_option.ne("")
        &
        transaction_option.str.upper().ne("UNKNOWN")
    )

    option_type = transaction_option.copy()

    option_type.loc[
        ~transaction_valid
    ] = investor_option.loc[
        ~transaction_valid
    ]

    option_type = (
        option_type
        .fillna("Unknown")
        .astype("string")
    )

    # =================================================
    # FINAL OPTION VALIDATION
    # =================================================

    print("\nFinal Option Type")
    print("-" * 80)

    print(
        option_type
        .value_counts(dropna=False)
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
                category,

            "plan":
                (
                    scheme_name
                    .str.extract(
                        r"(?i)\b(Direct|Regular)\b",
                        expand=False
                    )
                    .fillna("")
                    +
                    " "
                    +
                    scheme_name
                    .str.extract(
                        r"(?i)\b(Growth|IDCW)\b",
                        expand=False
                    )
                    .fillna("")
                )
                .str.strip(),

            "isin":
                None,

            "amc_code":
                gold_df["amc_code"],

            "amfi_code":
                None,

            "category_id":
                None,

            "plan_type":
                plan_type,

            "option_type":
                option_type,

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
                "ACTIVE"
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
    # AMC MASTER
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
    # Option type has already been resolved before
    # this point using ALL transaction rows.
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

    print("\nScheme Mapping Validation")
    print("-" * 80)

    print("\nPlan Type:")
    print(
        gold_df[
            "plan_type"
        ]
        .value_counts(dropna=False)
    )

    print("\nOption Type:")
    print(
        gold_df[
            "option_type"
        ]
        .value_counts(dropna=False)
    )

    print("\nStatus:")
    print(
        gold_df[
            "status"
        ]
        .value_counts(dropna=False)
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
                "plan_type",
                "option_type",
                "rta_scheme_code",
                "amc_id",
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
# This is important because existing rows may currently
# contain "Unknown".
# =====================================================

def load_scheme(gold_df):

    print("=" * 80)
    print("LOADING GOLD SCHEME")
    print("=" * 80)

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
        "status"
    ]

    # =================================================
    # UPDATE EXISTING SCHEMES
    #
    # This will replace:
    #
    # Unknown
    #
    # with:
    #
    # Growth / Regular
    #
    # when the source mapping now provides it.
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