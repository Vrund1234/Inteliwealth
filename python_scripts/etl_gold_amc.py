import pandas as pd
import traceback
from utils.db import engine



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


        return pd.to_datetime(last_time)


    except Exception:

        return pd.Timestamp(
            "1900-01-01"
        )



# =====================================================
# EXTRACT AMC
# =====================================================

def extract_amc():


    print("="*80)
    print("Extracting Silver AMC Data")
    print("="*80)


    last_time = get_last_processed_time()



    df = pd.read_sql(

        """
        SELECT

            source,
            prodcode,
            td_fund,
            scheme,
            created_at

        FROM silver.transaction_master_new

        """,

        engine
    )



    if df.empty:

        return df



    # Timestamp cleaning

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )


    # Remove timezone difference

    if getattr(
        df["created_at"].dt,
        "tz",
        None
    ) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )



    if last_time.tzinfo is not None:

        last_time = last_time.tz_localize(None)



    # Incremental extraction

    df = df[
        df["created_at"] > last_time
    ]



    print()

    print(
        "Rows fetched :",
        len(df)
    )


    return df



# =====================================================
# TRANSFORM AMC
# =====================================================

def transform_amc(df):


    print("="*80)
    print("Transforming Gold AMC")
    print("="*80)



    gold_df = pd.DataFrame()



    # ==========================
    # RTA
    # ==========================

    gold_df["rta"] = (

        df["source"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    # ==========================
    # AMC CODE
    # ==========================


    gold_df["amc_code"] = None



    cams_mask = (
        gold_df["rta"]=="CAMS"
    )


    kfin_mask = (
        gold_df["rta"]=="KFIN"
    )



    gold_df.loc[
        cams_mask,
        "amc_code"
    ] = (
        df.loc[
            cams_mask,
            "prodcode"
        ]
    )



    gold_df.loc[
        kfin_mask,
        "amc_code"
    ] = (
        df.loc[
            kfin_mask,
            "td_fund"
        ]
    )



    gold_df["amc_code"] = (

        gold_df["amc_code"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    # ==========================
    # AMC NAME LOOKUP
    # ==========================


    amc_lookup = pd.read_sql(

        """

        SELECT

            amc_code,
            amc_name

        FROM bronze.amc_master

        """,

        engine

    )



    amc_lookup["amc_code"] = (

        amc_lookup["amc_code"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    lookup_code = (

        gold_df["amc_code"]

        .str.extract(
            r"^([A-Z]+)",
            expand=False
        )

    )



    gold_df["name"] = (

        lookup_code

        .map(
            amc_lookup
            .set_index("amc_code")
            ["amc_name"]
        )

    )



    # ==========================
    # Remaining fields
    # ==========================


    gold_df["short_name"] = None

    gold_df["logo_url"] = None

    gold_df["status"] = None



    # ==========================
    # Timestamp
    # ==========================


    gold_df["created_at"] = (
        df["created_at"]
        .values
    )



    gold_df["updated_at"] = None



    gold_df = gold_df[

        [

            "amc_code",

            "name",

            "short_name",

            "rta",

            "logo_url",

            "status",

            "created_at",

            "updated_at"

        ]

    ]



    # Remove invalid codes

    gold_df = gold_df[
        gold_df["amc_code"] != ""
    ]



    # Length cleanup

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



    print()

    print(
        "Rows Ready :",
        len(gold_df)
    )


    print(gold_df.head())


    return gold_df



# =====================================================
# LOAD
# =====================================================

def load_amc(gold_df):


    print("="*80)
    print("Loading Gold AMC")
    print("="*80)


    try:


        gold_df.to_sql(

            name="amc",

            schema="gold",

            con=engine,

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print(
            "Rows inserted:",
            len(gold_df)
        )


        return True



    except Exception:


        traceback.print_exc()

        return False



# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":


    df = extract_amc()



    if df.empty:

        print(
            "No new AMC records found"
        )

        exit()



    gold_df = transform_amc(df)



    load_amc(
        gold_df
    )


    print(
        "Gold AMC ETL Completed"
    )