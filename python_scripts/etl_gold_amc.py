import pandas as pd
import traceback

from utils.db import engine


# =====================================================
# SAFE READ
# =====================================================

def safe_read(query):

    try:

        return pd.read_sql(
            query,
            engine
        )

    except Exception as e:

        print("SQL ERROR :", e)

        return pd.DataFrame()


# =====================================================
# GET LAST PROCESSED TIME
# =====================================================

def get_last_processed_time():

    try:

        result = pd.read_sql(
            """
            SELECT
                MAX(created_at) AS last_time
            FROM gold.amc
            """,
            engine
        )

        last_time = result.iloc[0]["last_time"]

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
# EXTRACT GOLD AMC
# =====================================================

def extract_amc():

    print("=" * 80)
    print("Extracting Gold AMC")
    print("=" * 80)

    last_time = get_last_processed_time()

    df = safe_read(
        f"""
        SELECT

            source,
            amc_code,
            td_fund,
            scheme,
            brokcode,
            src_brk_code,
            created_at

        FROM silver.transaction_master_new

        WHERE created_at > '{last_time}'
        """
    )

    if df.empty:

        print("No new records found.")

        return df

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )

    print("Rows fetched :", len(df))

    return df


# =====================================================
# TRANSFORM GOLD AMC
# =====================================================

def transform_amc(df):

    print("=" * 80)
    print("Transforming Gold AMC")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()


    # =====================================================
    # CLEAN SOURCE
    # =====================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =====================================================
    # AMC CODE
    # =====================================================

    df["amc_code"] = (
        df["amc_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =====================================================
    # AMC NAME LOOKUP
    # =====================================================

    amc_lookup = safe_read(
        """
        SELECT
            amc_code,
            amc_name
        FROM bronze.amc_master
        """
    )


    if not amc_lookup.empty:

        amc_lookup["amc_code"] = (
            amc_lookup["amc_code"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )


        df = df.merge(

            amc_lookup,

            on="amc_code",

            how="left"

        )

    else:

        df["amc_name"] = None



    # =====================================================
    # BUILD GOLD DATAFRAME
    # =====================================================

    gold_df = pd.DataFrame({

        "amc_code": df["amc_code"],

        "name": df["amc_name"],

        "short_name": None,

        "rta": df["source"],

        "logo_url": None,

        "status": None

    })


    # =====================================================
    # ARN LOGIC
    # =====================================================

    gold_df["arn"] = (
        df["brokcode"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =====================================================
    # SUB ARN LOGIC
    # =====================================================

    gold_df["sub_arn"] = (
        df["src_brk_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    gold_df["arn"] = (
        gold_df["arn"]
        .replace("", None)
    )


    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .replace("", None)
    )



    # =====================================================
    # REMOVE INVALID ROWS
    # =====================================================

    gold_df = gold_df[

        gold_df["amc_code"] != ""

    ]


    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    gold_df = gold_df.drop_duplicates(

        subset=[
            "amc_code"
        ],

        keep="first"

    )


    # =====================================================
    # COLUMN LENGTH VALIDATION
    # =====================================================

    gold_df["amc_code"] = (
        gold_df["amc_code"]
        .str[:20]
    )


    gold_df["name"] = (
        gold_df["name"]
        .astype("string")
        .str[:255]
    )


    gold_df["short_name"] = (
        gold_df["short_name"]
        .astype("string")
        .str[:50]
    )


    gold_df["rta"] = (
        gold_df["rta"]
        .astype("string")
        .str[:20]
    )


    gold_df["logo_url"] = (
        gold_df["logo_url"]
        .astype("string")
        .str[:512]
    )


    gold_df["status"] = (
        gold_df["status"]
        .astype("string")
        .str[:20]
    )


    # Added from code 1

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


    print("Rows Ready :", len(gold_df))

    return gold_df



# =====================================================
# LOAD GOLD AMC
# =====================================================

def load_amc(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.AMC")
    print("=" * 80)


    if gold_df.empty:

        print("No new AMC records found.")

        return True



    print()

    print("Checking existing gold AMC records")



    existing_amc = pd.read_sql(

        """

        SELECT

            amc_code,

            created_at

        FROM gold.amc

        """,

        engine

    )



    print(

        "Existing AMC records:",

        len(existing_amc)

    )



    if len(existing_amc) > 0:


        existing_amc["amc_code"] = (

            existing_amc["amc_code"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        gold_df["amc_code"] = (

            gold_df["amc_code"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



        gold_df = gold_df.merge(

            existing_amc,

            on="amc_code",

            how="left",

            suffixes=(

                "_new",

                "_old"

            )

        )



        gold_df = gold_df[

            gold_df["created_at_old"].isna()

        ]



        gold_df = gold_df.drop(

            columns=[

                "created_at_old"

            ]

        )



    print(

        "Rows after duplicate check:",

        len(gold_df)

    )



    if len(gold_df) == 0:


        print(

            "No new AMC records to insert"

        )


        return True



    try:


        gold_df.to_sql(

            name="amc",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )


        print()

        print(f"Inserted Rows : {len(gold_df)}")

        return True


    except Exception:

        print("FAILED LOADING GOLD AMC")

        traceback.print_exc(limit=5)

        return False



# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    print("=" * 80)
    print("STARTING GOLD AMC ETL")
    print("=" * 80)


    df = extract_amc()


    if not df.empty:

        gold_df = transform_amc(df)

        status = load_amc(gold_df)


        if status:

            print("=" * 80)
            print("GOLD AMC ETL COMPLETED SUCCESSFULLY")
            print("=" * 80)

        else:

            print("=" * 80)
            print("GOLD AMC ETL FAILED")
            print("=" * 80)


    else:

        print("No new AMC records to process.")