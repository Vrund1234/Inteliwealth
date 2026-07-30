import pandas as pd
import traceback
import uuid
import re

from utils.db import engine



# =====================================================
# SAFE READ
# =====================================================

def safe_read(query, conn=engine):

    try:

        return pd.read_sql(
            query,
            conn
        )

    except Exception as e:

        print("SQL ERROR :", e)

        return pd.DataFrame()



# =====================================================
# GET LAST PROCESSED TIME
# =====================================================

def get_last_processed_time():

    try:

        df = pd.read_sql(

            """
            SELECT
                MAX(created_at) AS last_time

            FROM gold.scheme
            """,

            engine

        )


        last_time = df.iloc[0]["last_time"]


        if pd.isna(last_time):

            return pd.Timestamp(
                "1900-01-01"
            )


        return pd.to_datetime(
            last_time
        )


    except Exception:

        return pd.Timestamp(
            "1900-01-01"
        )



# =====================================================
# EXTRACT GOLD SCHEME DATA
# =====================================================

def extract_scheme():


    print("=" * 80)
    print("Extracting Gold Scheme")
    print("=" * 80)



    last_time = get_last_processed_time()



    print(
        "Last processed time :",
        last_time
    )



    # =================================================
    # TRANSACTION SOURCE
    # =================================================


    transaction_df = safe_read(

        """

        SELECT

            source,
            amc_code,
            prodcode,
            scheme,
            funddesc,
            scheme_type,
            created_at

        FROM silver.transaction_master_new

        """

    )



    # =================================================
    # INVESTOR MASTER SOURCE
    # =================================================


    investor_df = safe_read(

        """

        SELECT

            source,
            amc_code,
            product_code,
            scheme_name,
            fund_description,
            categorydesc,
            created_at

        FROM silver.investor_master

        """

    )



    # =================================================
    # TIMESTAMP FILTER
    # =================================================


    if not transaction_df.empty:


        transaction_df["created_at"] = pd.to_datetime(

            transaction_df["created_at"],

            errors="coerce"

        )


        transaction_df = transaction_df[

            transaction_df["created_at"] > last_time

        ]



    if not investor_df.empty:


        investor_df["created_at"] = pd.to_datetime(

            investor_df["created_at"],

            errors="coerce"

        )


        investor_df = investor_df[

            investor_df["created_at"] > last_time

        ]




    print()

    print(
        "Transaction Rows :",
        len(transaction_df)
    )


    print(
        "Investor Rows :",
        len(investor_df)
    )


    return (

        transaction_df,

        investor_df

    )

# =====================================================
# NORMALIZE SCHEME NAME
# =====================================================

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





# =====================================================
# TRANSFORM GOLD SCHEME
# =====================================================

def transform_scheme(
        transaction_df,
        investor_df
):


    print("=" * 80)
    print("Transforming Gold Scheme")
    print("=" * 80)



    if transaction_df.empty and investor_df.empty:

        print(
            "No source data available"
        )

        return pd.DataFrame()



    transaction_df = transaction_df.copy()

    investor_df = investor_df.copy()



    # =====================================================
    # CREATE JOIN KEYS
    # =====================================================


    if not transaction_df.empty:


        transaction_df["join_scheme_code"] = (

            transaction_df["prodcode"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



    if not investor_df.empty:


        investor_df["join_scheme_code"] = (

            investor_df["product_code"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



    # =====================================================
    # REMOVE DUPLICATES INSIDE CURRENT BATCH
    # =====================================================


    if not transaction_df.empty:


        transaction_df = transaction_df.drop_duplicates(

            [

                "source",

                "amc_code",

                "join_scheme_code"

            ],

            keep="first"

        )



    if not investor_df.empty:


        investor_df = investor_df.drop_duplicates(

            [

                "source",

                "amc_code",

                "join_scheme_code"

            ],

            keep="first"

        )



    # =====================================================
    # MERGE INVESTOR DETAILS
    # =====================================================


    if not transaction_df.empty:


        gold_df = transaction_df.merge(

            investor_df[

                [

                    "source",

                    "amc_code",

                    "join_scheme_code",

                    "fund_description",

                    "categorydesc"

                ]

            ],

            on=[

                "source",

                "amc_code",

                "join_scheme_code"

            ],

            how="left"

        )


    else:


        gold_df = investor_df.copy()



    # =====================================================
    # SCHEME NAME PRIORITY
    #
    # funddesc
    #    |
    # scheme
    #    |
    # fund_description
    #
    # =====================================================


    if "funddesc" in gold_df.columns:


        scheme_name = (

            gold_df["funddesc"]

            .fillna(

                gold_df.get(
                    "scheme"
                )

            )

            .fillna(

                gold_df.get(
                    "fund_description"
                )

            )

        )


    else:


        scheme_name = gold_df["fund_description"]



    final = pd.DataFrame()



    # =====================================================
    # PRIMARY KEY
    # =====================================================


    final["id"] = [

        uuid.uuid4()

        for _ in range(len(gold_df))

    ]



    # =====================================================
    # BASIC DETAILS
    # =====================================================


    final["rta"] = (

        gold_df["source"]

        .astype("string")

        .str.upper()

        .str.strip()

    )



    final["scheme_code"] = (

        gold_df["join_scheme_code"]

        .astype("string")

        .str.upper()

        .str.strip()

    )



    final["scheme_name"] = (

        scheme_name

        .apply(normalize_name)

    )

        # =====================================================
    # CATEGORY
    # =====================================================


    if "scheme_type" in gold_df.columns:


        final["category"] = (

            gold_df["scheme_type"]

            .fillna(

                gold_df.get(
                    "categorydesc"
                )

            )

        )


    else:


        final["category"] = (

            gold_df.get(
                "categorydesc"
            )

        )



    # =====================================================
    # PLAN TYPE
    # =====================================================


    final["plan"] = (

        scheme_name

        .astype("string")

        .str.extract(

            r"(DIRECT|REGULAR)",

            expand=False

        )

    )



    # =====================================================
    # FUTURE MAPPING COLUMNS
    # =====================================================


    final["isin"] = None


    final["amc_code"] = (

        gold_df["amc_code"]

        .astype("string")

        .str.upper()

        .str.strip()

    )


    final["amfi_code"] = None


    final["category_id"] = None


    final["plan_type"] = None


    final["option_type"] = None



    final["rta_scheme_code"] = (

        gold_df["join_scheme_code"]

    )



    final["benchmark_id"] = None


    final["expense_ratio"] = None


    final["exit_load_json"] = None


    final["lock_in_months"] = None


    final["riskometer"] = None


    final["status"] = None



    # =====================================================
    # AUDIT COLUMNS
    # =====================================================


    final["created_at"] = (

        gold_df["created_at"]

    )


    final["updated_at"] = None



    # =====================================================
    # CLEAN INVALID RECORDS
    # =====================================================


    final = final[

        final["scheme_code"]

        .notna()

    ]



    final = final[

        final["scheme_code"]

        != ""

    ]



    # =====================================================
    # BATCH DUPLICATE REMOVAL
    #
    # Only current extraction duplicates
    #
    # Timestamp handles old data
    #
    # =====================================================


    final = (

        final.drop_duplicates(

            [

                "rta",

                "scheme_code"

            ],

            keep="first"

        )

        .reset_index(drop=True)

    )



    # =====================================================
    # STRING LENGTH CONTROL
    # =====================================================


    final["rta"] = (

        final["rta"]

        .astype("string")

        .str[:20]

    )


    final["scheme_code"] = (

        final["scheme_code"]

        .astype("string")

        .str[:50]

    )


    final["scheme_name"] = (

        final["scheme_name"]

        .astype("string")

        .str[:255]

    )


    final["category"] = (

        final["category"]

        .astype("string")

        .str[:100]

    )


    final["plan"] = (

        final["plan"]

        .astype("string")

        .str[:50]

    )


    final["amc_code"] = (

        final["amc_code"]

        .astype("string")

        .str[:50]

    )


    final["rta_scheme_code"] = (

        final["rta_scheme_code"]

        .astype("string")

        .str[:50]

    )



    print("=" * 80)
    print("Gold Scheme Preview")
    print("=" * 80)

    print(final.head())


    print()

    print(
        "Rows Ready :",
        len(final)
    )


    return final

# =====================================================
# LOAD GOLD SCHEME
# =====================================================

def load_scheme(gold_df):


    print("=" * 80)
    print("Loading Gold Scheme")
    print("=" * 80)



    if gold_df.empty:

        print(
            "No new scheme records found."
        )

        return True



    # =====================================================
    # SET LOAD TIMESTAMP
    #
    # Same logic as SIP / Transaction
    #
    # =====================================================


    load_time = pd.Timestamp.now()



    gold_df["created_at"] = load_time

    gold_df["updated_at"] = load_time



    try:


        gold_df.to_sql(

            name="scheme",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print()

        print(
            f"{len(gold_df)} rows inserted into gold.scheme"
        )


        return True



    except Exception as e:


        print(
            "ERROR while loading gold.scheme"
        )


        traceback.print_exc()


        return False





# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":


    print("=" * 80)
    print("STARTING GOLD SCHEME ETL")
    print("=" * 80)



    transaction_df, investor_df = extract_scheme()



    if transaction_df.empty and investor_df.empty:


        print(
            "No new data found."
        )

        exit()



    gold_df = transform_scheme(

        transaction_df,

        investor_df

    )



    print()

    print(
        "Final Gold Scheme Shape :",
        gold_df.shape
    )



    print(
        gold_df.head()
    )



    success = load_scheme(

        gold_df

    )



    if success:


        print("=" * 80)

        print(
            "GOLD SCHEME ETL COMPLETED SUCCESSFULLY"
        )

        print("=" * 80)



    else:


        print("=" * 80)

        print(
            "GOLD SCHEME ETL FAILED"
        )

        print("=" * 80)