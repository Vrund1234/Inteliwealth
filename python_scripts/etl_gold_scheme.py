import pandas as pd
import uuid
import re
from datetime import datetime

from sqlalchemy import text

from utils.db import engine
from utils.db import master_engine


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
    master_engine
)


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
    # REMOVE SOURCE DUPLICATES
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

        "Unique Transaction Schemes :",

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

    print(len(gold_df))


    # =================================================
    # SCHEME NAME PRIORITY
    # =================================================

    scheme_name = (

        gold_df["funddesc"]

        .fillna(

            gold_df["scheme"]

        )

        .fillna(

            gold_df["scheme_name"]

        )

        .fillna(

            gold_df["fund_description"]

        )

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

                .fillna(

                    gold_df["categorydesc"]

                ),


            "plan":

                scheme_name

                .str.extract(

                    r"(Direct|Regular)",

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

            "arn": (
                gold_df["brokcode"]
                .astype("string")
                .str.strip()
                .str.upper()
            ),

            "sub_arn": (
                gold_df["src_brk_code"]
                .astype("string")
                .str.strip()
                .str.upper()
            )

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

    gold_df["scheme_code"] = (

        gold_df["scheme_code"]

        .astype("string")

        .str.strip()

        .str.upper()

    )

    gold_df["arn"] = (
        gold_df["arn"]
        .astype("string")
        .str[:50]
    )

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .astype("string")
        .str[:50]
    )


    gold_df = gold_df[

        gold_df["scheme_code"].notna()

    ]


    print("\nAMFI Code Populated")
    print("-" * 80)

    print(

        gold_df["amfi_code"]

        .notna()

        .sum()

    )


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


    gold_df = gold_df.merge(

        amc_master,

        on="amc_code",

        how="left"

    )


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

        .reset_index(

            drop=True

        )

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
    # VALIDATE UUID
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


    print("\nUUID Validation Passed")


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


    print("\nTransformation Completed")
    print("-" * 80)

    print(

        "Gold Rows :",

        len(gold_df)

    )


    print("\nGold Scheme Preview")
    print("-" * 80)

    print(

        gold_df.head()

    )


    return gold_df

# =====================================================
# LOAD GOLD SCHEME
# =====================================================

def load_scheme(gold_df):

    print("=" * 80)
    print("LOADING GOLD SCHEME")
    print("=" * 80)


    # =====================================================
    # DUPLICATE CHECK
    # =====================================================

    print("\nChecking existing gold schemes")


    existing_scheme = pd.read_sql(

        """

        SELECT

            rta,

            scheme_code,

            created_at

        FROM gold.scheme

        """,

        engine

    )


    print(

        "Existing schemes :",

        len(existing_scheme)

    )


    if len(existing_scheme) > 0:


        existing_scheme["rta"] = (

            existing_scheme["rta"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        existing_scheme["scheme_code"] = (

            existing_scheme["scheme_code"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        gold_df["rta"] = (

            gold_df["rta"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        gold_df["scheme_code"] = (

            gold_df["scheme_code"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        compare_df = gold_df.merge(

            existing_scheme,

            on=[

                "rta",

                "scheme_code"

            ],

            how="left",

            suffixes=(

                "_new",

                "_old"

            )

        )


        compare_df = compare_df[

            compare_df["created_at_old"].isna()

            |

            (

                compare_df["created_at_new"]

                >

                compare_df["created_at_old"]

            )

        ]


        gold_df = compare_df[

            gold_df.columns

        ]


    print(

        "Rows after duplicate check :",

        len(gold_df)

    )


    if len(gold_df) == 0:


        print(

            "No new schemes to insert"

        )


        return True


    # =====================================================
    # LOAD TO GOLD
    # =====================================================

    try:


        gold_df.to_sql(

            name="scheme",

            schema="gold",

            con=engine,

            if_exists="append",

            index=False,

            chunksize=1000

        )


        print("\nLoading Completed")

        print("-" * 80)

        print(

            "Rows Inserted :",

            len(gold_df)

        )


        return True


    except Exception as e:


        print()

        print(

            "ERROR WHILE LOADING GOLD SCHEME"

        )


        print(

            type(e).__name__

        )


        print(e)


        return False

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
            print("GOLD SCHEME ETL COMPLETED SUCCESSFULLY")
            print("=" * 80)


        else:


            print("\n")

            print("=" * 80)
            print("GOLD SCHEME ETL FAILED")
            print("=" * 80)


    except Exception as e:


        print("\n")

        print("=" * 80)
        print("GOLD SCHEME ETL ERROR")
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