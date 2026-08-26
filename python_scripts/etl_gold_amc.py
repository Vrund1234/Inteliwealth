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

            return pd.Timestamp("1900-01-01")

        return pd.to_datetime(last_time)

    except Exception:

        return pd.Timestamp("1900-01-01")

def extract_amc():

    print("=" * 80)
    print("EXTRACTING GOLD AMC")
    print("=" * 80)

    df = safe_read(
        """
        SELECT
            source,
            amc_code,
            td_fund,
            scheme,
            folio_no,
            brokcode,
            src_brk_code,
            created_at
        FROM silver.transaction_master_new
        WHERE flag = 0
        """
    )

    if df.empty:

        print("No Silver records found with flag = 0")
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

    print("Silver flag = 0 rows fetched :", len(df))

    return df


# =====================================================
# TRANSFORM GOLD AMC
# =====================================================

def transform_amc(df):

    print("=" * 80)
    print("TRANSFORMING GOLD AMC")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()


    # =================================================
    # CLEAN SOURCE
    # =================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =================================================
    # AMC CODE
    # =================================================

    df["amc_code"] = (
        df["amc_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =================================================
    # ARN
    #
    # Gold AMC ARN comes from:
    #
    # silver.transaction_master_new.brokcode
    # =================================================

    df["arn"] = (
        df["brokcode"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # =================================================
    # SUB ARN
    #
    # Gold AMC sub_arn comes from:
    #
    # silver.transaction_master_new.src_brk_code
    # =================================================

    df["sub_arn"] = (
        df["src_brk_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )


    # Empty values → NULL

    df["arn"] = (
        df["arn"]
        .replace("", None)
    )

    df["sub_arn"] = (
        df["sub_arn"]
        .replace("", None)
    )


    # =================================================
    # AMC NAME LOOKUP
    # =================================================

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

        # Prevent duplicate lookup rows

        amc_lookup = (
            amc_lookup
            .drop_duplicates(
                subset=["amc_code"],
                keep="first"
            )
        )

        df = df.merge(
            amc_lookup,
            on="amc_code",
            how="left"
        )

    else:

        df["amc_name"] = None


    # =================================================
    # BUILD GOLD DATAFRAME
    #
    # IMPORTANT:
    #
    # flag is NOT included.
    #
    # flag is used only in Silver extraction:
    #
    #     WHERE flag = 0
    #
    # Gold AMC has NO flag column.
    # =================================================

    gold_df = pd.DataFrame({

        "amc_code": df["amc_code"],

        "name": df["amc_name"],

        "short_name": None,

        "rta": df["source"],

        "logo_url": None,

        "status": None,

        "arn": df["arn"],

        "sub_arn": df["sub_arn"],

        "created_at": df["created_at"]

    })


    # =================================================
    # REMOVE INVALID AMC CODE
    # =================================================

    gold_df = gold_df[
        gold_df["amc_code"] != ""
    ]


    # =================================================
    # REMOVE DUPLICATES IN CURRENT BATCH
    #
    # AMC natural key = amc_code
    # =================================================

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=["amc_code"],
            keep="first"
        )
    )


    # =================================================
    # COLUMN LENGTH VALIDATION
    # =================================================

    gold_df["amc_code"] = (
        gold_df["amc_code"]
        .astype("string")
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


    # =================================================
    # CREATED AT
    # =================================================

    gold_df["created_at"] = pd.to_datetime(
        gold_df["created_at"],
        errors="coerce"
    )


    # =================================================
    # FINAL SAFETY CHECK
    #
    # flag must NEVER exist in Gold.
    # =================================================

    if "flag" in gold_df.columns:

        gold_df = gold_df.drop(
            columns=["flag"]
        )


    print(
        "Rows Ready :",
        len(gold_df)
    )

    print()
    print("Gold AMC Columns:")
    print(
        list(gold_df.columns)
    )

    return gold_df


# =====================================================
# LOAD GOLD AMC
# =====================================================

def load_amc(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.AMC")
    print("=" * 80)


    if gold_df.empty:

        print(
            "No new AMC records found."
        )

        return True


    # =================================================
    # CHECK EXISTING GOLD AMC
    #
    # Natural key = amc_code
    # =================================================

    print()
    print(
        "Checking existing gold AMC records"
    )


    existing_amc = safe_read(
        """
        SELECT
            amc_code
        FROM gold.amc
        """
    )


    print(
        "Existing AMC records :",
        len(existing_amc)
    )


    if not existing_amc.empty:

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


        # =================================================
        # REMOVE AMC CODES ALREADY IN GOLD
        # =================================================

        gold_df = gold_df[
            ~gold_df["amc_code"].isin(
                existing_amc["amc_code"]
            )
        ]


    print(
        "Rows after duplicate check :",
        len(gold_df)
    )


    if gold_df.empty:

        print(
            "No new AMC records to insert."
        )

        return True


    # =================================================
    # FINAL GOLD COLUMNS
    #
    # flag intentionally NOT included.
    # =================================================

    gold_columns = [

        "amc_code",
        "name",
        "short_name",
        "rta",
        "logo_url",
        "status",
        "arn",
        "sub_arn",
        "created_at"

    ]


    gold_df = gold_df[
        gold_columns
    ]


    print()
    print(
        "Rows to insert :",
        len(gold_df)
    )

    print()
    print(
        "Columns being inserted:"
    )

    print(
        list(gold_df.columns)
    )


    # =================================================
    # FINAL FLAG SAFETY CHECK
    # =================================================

    if "flag" in gold_df.columns:

        print(
            "ERROR: flag found in Gold dataframe."
        )

        gold_df = gold_df.drop(
            columns=["flag"]
        )


    # =================================================
    # INSERT
    # =================================================

    try:

        from utils.db import upsert_dataframe

        upsert_dataframe(
            gold_df,
            schema="gold",
            table="amc",
            conflict_columns=["rta", "amc_code"],
            chunksize=200,
            # gold.amc has no updated_at (or equivalent) column.
            updated_at_column=None,
        )


        print()
        print(
            f"Inserted Rows : {len(gold_df)}"
        )

        print(
            "Silver flag = 0 filter : YES"
        )

        print(
            "Flag inserted into Gold : NO"
        )

        return True


    except Exception:

        print(
            "FAILED LOADING GOLD AMC"
        )

        traceback.print_exc(
            limit=5
        )

        return False


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    print("=" * 80)
    print("STARTING GOLD AMC ETL")
    print("=" * 80)


    # =================================================
    # EXTRACT
    # =================================================

    df = extract_amc()


    if not df.empty:


        # =================================================
        # TRANSFORM
        # =================================================

        gold_df = transform_amc(df)


        # =================================================
        # LOAD
        # =================================================

        status = load_amc(
            gold_df
        )


        if status:

            print("=" * 80)
            print(
                "GOLD AMC ETL COMPLETED SUCCESSFULLY"
            )
            print("=" * 80)

        else:

            print("=" * 80)
            print(
                "GOLD AMC ETL FAILED"
            )
            print("=" * 80)


    else:

        print(
            "No new AMC records to process."
        )