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
            FROM gold.scheme_nav
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
# NORMALIZE DATA FOR COMPARE
# =====================================================

def normalize_for_compare(df):

    df = df.copy()


    df = df.drop(
        columns=[
            "created_at",
            "updated_at"
        ],
        errors="ignore"
    )


    for col in df.columns:


        if pd.api.types.is_datetime64_any_dtype(
            df[col]
        ):

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce"
                )
                .dt.strftime(
                    "%Y-%m-%d"
                )
            )


        else:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )


    return df



# =====================================================
# CREATE ROW KEY
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)


    return (
        df.fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )



# =====================================================
# EXTRACT GOLD SCHEME NAV
# =====================================================

def extract_scheme_nav():

    print("=" * 80)
    print("Extracting Gold Scheme NAV")
    print("=" * 80)


    last_time = get_last_processed_time()



    df = safe_read(

        """
        SELECT

            source,
            prodcode AS scheme_code,
            traddate,
            purprice,
            created_at

        FROM silver.transaction_master_new

        WHERE purprice IS NOT NULL

        """

    )


    if df.empty:

        print(
            "No data found in Silver."
        )

        return df



    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )



    if last_time.tzinfo is not None:

        last_time = (
            last_time
            .tz_localize(None)
        )


    if getattr(
        df["created_at"].dt,
        "tz",
        None
    ) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )



    df = df[
        df["created_at"] > last_time
    ]



    print()

    print(
        "Rows fetched :",
        len(df)
    )


    print(
        "Columns :",
        len(df.columns)
    )


    if not df.empty:

        print(df.head())


    return df



# =====================================================
# TRANSFORM GOLD SCHEME NAV
# =====================================================

def transform_scheme_nav(df):


    print("=" * 80)
    print("Transforming Gold Scheme NAV")
    print("=" * 80)


    gold_df = pd.DataFrame()



    # =====================================================
    # LOAD GOLD SCHEME
    # =====================================================


    gold_scheme = safe_read(

        """

        SELECT

            id,
            rta,
            scheme_code

        FROM gold.scheme

        """

    )



    df["source"] = (

        df["source"]
        .astype("string")
        .str.strip()
        .str.upper()

    )


    df["scheme_code"] = (

        df["scheme_code"]
        .astype("string")
        .str.strip()
        .str.upper()

    )



    gold_scheme["rta"] = (

        gold_scheme["rta"]
        .astype("string")
        .str.strip()
        .str.upper()

    )


    gold_scheme["scheme_code"] = (

        gold_scheme["scheme_code"]
        .astype("string")
        .str.strip()
        .str.upper()

    )



    # =====================================================
    # MAP SCHEME ID
    # =====================================================


    df = df.merge(

        gold_scheme[

            [
                "id",
                "rta",
                "scheme_code"
            ]

        ],

        left_on=[

            "source",
            "scheme_code"

        ],

        right_on=[

            "rta",
            "scheme_code"

        ],

        how="left"

    )


    df.rename(

        columns={
            "id":"scheme_id"
        },

        inplace=True

    )



    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)


    print(
        "Total Rows :",
        len(df)
    )


    print(
        "Matched :",
        df["scheme_id"].notna().sum()
    )


    print(
        "Missing :",
        df["scheme_id"].isna().sum()
    )



    # =====================================================
    # FINAL GOLD DATA
    # =====================================================


    gold_df["scheme_id"] = df["scheme_id"]


    gold_df["nav_date"] = (

        pd.to_datetime(
            df["traddate"],
            errors="coerce"
        )
        .dt.date

    )


    gold_df["nav"] = pd.to_numeric(

        df["purprice"],

        errors="coerce"

    )


    gold_df["repurchase_nav"] = None


    gold_df["source"] = df["source"]



    gold_df["created_at"] = df["created_at"]


    gold_df["updated_at"] = None



    gold_df = gold_df[

        gold_df["scheme_id"].notna()
        &
        gold_df["nav_date"].notna()
        &
        gold_df["nav"].notna()

    ]



    print()

    print(
        "Rows Ready :",
        len(gold_df)
    )


    print(
        gold_df.head()
    )


    return gold_df



# =====================================================
# LOAD GOLD SCHEME NAV
# =====================================================

def load_scheme_nav(gold_df):


    print("=" * 80)
    print("Loading Gold Scheme NAV")
    print("=" * 80)



    if gold_df.empty:

        print(
            "No new records."
        )

        return True



    try:


        existing = pd.read_sql(

            """

            SELECT *

            FROM gold.scheme_nav

            """,

            engine

        )


    except Exception:


        existing = pd.DataFrame()



    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================


    if not existing.empty:


        old_keys = set(

            create_row_key(
                existing
            )

        )


        new_keys = create_row_key(

            gold_df

        )


        gold_df = gold_df.loc[

            ~new_keys.isin(
                old_keys
            )

        ]



    if gold_df.empty:


        print(
            "Duplicate NAV data skipped."
        )


        return True



    # =====================================================
    # GOLD AUDIT TIME
    # =====================================================


    load_time = pd.Timestamp.now()


    gold_df["created_at"] = load_time


    gold_df["updated_at"] = load_time



    # =====================================================
    # INSERT
    # =====================================================


    try:


        gold_df.to_sql(

            "scheme_nav",

            engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print()

        print(
            f"{len(gold_df)} rows inserted into Gold Scheme NAV."
        )


        return True



    except Exception:


        print(
            "FAILED LOADING GOLD SCHEME NAV"
        )


        traceback.print_exc(
            limit=5
        )


        return False




# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":


    print()

    print("=" * 80)
    print("STARTING GOLD SCHEME NAV ETL")
    print("=" * 80)



    df = extract_scheme_nav()



    gold_df = transform_scheme_nav(
        df
    )



    status = load_scheme_nav(
        gold_df
    )



    if status:


        print()

        print("=" * 80)
        print(
            "GOLD SCHEME NAV ETL COMPLETED SUCCESSFULLY"
        )
        print("=" * 80)


    else:


        print()

        print(
            "GOLD SCHEME NAV ETL FAILED"
        )