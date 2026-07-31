import pandas as pd
import uuid
import re

from sqlalchemy import text
from utils.db import engine
from utils.db import master_engine


# =====================================================
# LOAD SCHEME MASTER (MASTER DATABASE)
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
# SAFE SQL READ
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
# GET LAST GOLD LOAD TIME
# =====================================================

def get_last_processed_time():

    try:

        result = pd.read_sql(
            """
            SELECT
                MAX(created_at) AS last_time
            FROM gold.scheme
            """,
            engine
        )

        last_time = result.iloc[0]["last_time"]

        if pd.isna(last_time):

            return pd.Timestamp("1900-01-01", tz="UTC")

        return pd.to_datetime(last_time)

    except Exception:

        return pd.Timestamp("1900-01-01", tz="UTC")


# =====================================================
# EXTRACT GOLD SCHEME SOURCE DATA
# =====================================================

def extract_scheme():

    print("=" * 80)
    print("Extracting Gold Scheme")
    print("=" * 80)


    last_time = get_last_processed_time()

    print("Last Processed Time :", last_time)


    # =================================================
    # TRANSACTION MASTER
    # =================================================

    transaction_query = f"""

        SELECT

            source,
            amc_code,
            prodcode,
            scheme,
            funddesc,
            scheme_type,
            created_at

        FROM silver.transaction_master_new

        WHERE created_at > '{last_time}'

    """


    # =================================================
    # INVESTOR MASTER
    # =================================================

    investor_query = f"""

        SELECT

            source,
            amc_code,
            product_code,
            scheme_name,
            fund_description,
            categorydesc,
            created_at

        FROM silver.investor_master

        WHERE created_at > '{last_time}'

    """


    transaction_df = safe_read(
        transaction_query
    )

    investor_df = safe_read(
        investor_query
    )


    # =================================================
    # TIMEZONE FIX
    # =================================================

    for df in [transaction_df, investor_df]:

        if not df.empty and "created_at" in df.columns:

            df["created_at"] = pd.to_datetime(
                df["created_at"],
                errors="coerce"
            )


            if getattr(df["created_at"].dt, "tz", None) is not None:

                df["created_at"] = (
                    df["created_at"]
                    .dt.tz_localize(None)
                )


    print()

    print("Extraction Completed")
    print("-" * 80)

    print("Transaction Rows :", len(transaction_df))
    print("Investor Rows    :", len(investor_df))

    print()

    print("Transaction Preview")
    print("-" * 80)
    print(transaction_df.head())

    print()

    print("Investor Preview")
    print("-" * 80)
    print(investor_df.head())


    return (
        transaction_df,
        investor_df
    )

# =====================================================
# TRANSFORM GOLD SCHEME
# =====================================================

def transform_scheme(transaction_df, investor_df):

    print("=" * 80)
    print("Transforming Gold Scheme")
    print("=" * 80)

    transaction_df = transaction_df.copy()
    investor_df = investor_df.copy()

    # =================================================
    # CLEAN SCHEME CODE
    # =================================================

    transaction_df["scheme_code"] = (
        transaction_df["prodcode"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    investor_df["scheme_code"] = (
        investor_df["product_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # =================================================
    # REMOVE DUPLICATES
    # =================================================

    transaction_df = transaction_df.drop_duplicates(
        subset=[
            "source",
            "amc_code",
            "scheme_code"
        ]
    )

    investor_df = investor_df.drop_duplicates(
        subset=[
            "source",
            "amc_code",
            "scheme_code"
        ]
    )

    print("Transaction schemes :", len(transaction_df))
    print("Investor schemes    :", len(investor_df))

    # =================================================
    # MERGE
    # =================================================

    gold_df = transaction_df.merge(

        investor_df[
            [
                "source",
                "amc_code",
                "scheme_code",
                "fund_description",
                "categorydesc"
            ]
        ],

        on=[
            "source",
            "amc_code",
            "scheme_code"
        ],

        how="left"

    )

    print("\nAfter Merge :", len(gold_df))

    # =================================================
    # SCHEME NAME
    # =================================================

    scheme_name = (

        gold_df["funddesc"]
        .fillna(gold_df["scheme"])
        .fillna(gold_df["fund_description"])

    )

    # =================================================
    # PLAN
    # =================================================

    plan = (
        scheme_name
        .astype("string")
        .str.extract(
            r"(Direct|Regular)",
            expand=False
        )
    )

    # =================================================
    # CREATE GOLD DATAFRAME
    # =================================================

    gold_df = pd.DataFrame({

        "id": [
            uuid.uuid4()
            for _ in range(len(gold_df))
        ],

        "rta": gold_df["source"],

        "scheme_code": gold_df["scheme_code"],

        "scheme_name": scheme_name,

        "category": (
            gold_df["scheme_type"]
            .fillna(gold_df["categorydesc"])
        ),

        "plan": plan,

        "isin": None,

        "amc_code": gold_df["amc_code"],

        "amfi_code": None,

        "category_id": None,

        "plan_type": None,

        "option_type": None,

        "rta_scheme_code": gold_df["scheme_code"],

        "benchmark_id": None,

        "expense_ratio": None,

        "exit_load_json": None,

        "lock_in_months": None,

        "riskometer": None,

        "status": None

    })

    # =================================================
    # NORMALIZE NAME
    # =================================================

    def normalize_name(x):

        if pd.isna(x):
            return None

        x = str(x).upper()
        x = re.sub(r"[^A-Z0-9 ]", " ", x)
        x = re.sub(r"\s+", " ", x)

        return x.strip()

    gold_df["name_norm"] = (
        gold_df["scheme_name"]
        .apply(normalize_name)
    )

    # =================================================
    # MATCH SCHEME MASTER
    # =================================================

    gold_df = gold_df.merge(

        scheme_master,

        on="name_norm",

        how="left",

        suffixes=("", "_master")

    )

    gold_df["amfi_code"] = gold_df["scheme_code_master"]

    print("\nScheme Master Match")
    print("-" * 80)
    print("Matched   :", gold_df["id_master"].notna().sum())
    print("Unmatched :", gold_df["id_master"].isna().sum())

    # =================================================
    # AMC LOOKUP
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

    # =================================================
    # DROP EXTRA COLUMNS
    # =================================================

    gold_df.drop(

        columns=[

            "amc_code",
            "name_norm",
            "id_master",
            "scheme_code_master",
            "name",
            "name_norm_loose"

        ],

        inplace=True,

        errors="ignore"

    )

    # =================================================
    # REMOVE NULL SCHEME CODE
    # =================================================

    gold_df = gold_df[
        gold_df["scheme_code"].notna()
    ]

    # =================================================
    # REMOVE DUPLICATES
    # =================================================

    gold_df = gold_df.drop_duplicates(

        subset=[
            "rta",
            "scheme_code"
        ],

        keep="first"

    )

    print("\nFinal Gold Scheme :", len(gold_df))

    #print(gold_df.head())

    return gold_df

# =====================================================
# LOAD GOLD SCHEME
# =====================================================

def load_scheme(df):

    print("=" * 80)
    print("Loading Gold Scheme")
    print("=" * 80)

    if df.empty:

        print("No Scheme data found")

        return

    # =================================================
    # GET LAST LOAD TIME
    # =================================================

    try:

        last_time = pd.read_sql(

            """
            SELECT
                MAX(created_at) AS last_time
            FROM gold.scheme
            """,

            engine

        ).iloc[0]["last_time"]

    except Exception:

        last_time = None


    if pd.isna(last_time):

        last_time = pd.Timestamp("1900-01-01")

    else:

        last_time = pd.to_datetime(last_time)


    print("Last Processed Time :", last_time)

    # =================================================
    # FILTER USING CREATED_AT
    # =================================================

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    if getattr(df["created_at"].dt, "tz", None) is not None:
        df["created_at"] = df["created_at"].dt.tz_localize(None)

    if getattr(last_time, "tzinfo", None) is not None:
        last_time = last_time.tz_localize(None)

    df = df[
        df["created_at"] > last_time
    ]


    if df.empty:

        print("No new Scheme records found.")

        return

    # =================================================
    # READ EXISTING GOLD
    # =================================================

    try:

        existing = pd.read_sql(

            """
            SELECT *
            FROM gold.scheme
            """,

            engine

        )

    except Exception:

        existing = pd.DataFrame()

    # =================================================
    # REMOVE DUPLICATES
    # =================================================

    if not existing.empty:

        old_keys = set(

            existing[
                ["rta", "scheme_code"]
            ]
            .fillna("")
            .astype(str)
            .agg("|".join, axis=1)

        )

        new_keys = (

            df[
                ["rta", "scheme_code"]
            ]
            .fillna("")
            .astype(str)
            .agg("|".join, axis=1)

        )

        df = df.loc[
            ~new_keys.isin(old_keys)
        ]

    if df.empty:

        print("Duplicate Scheme data skipped")

        return

    # =================================================
    # MATCH DATABASE COLUMN ORDER
    # =================================================

    db_cols = pd.read_sql(

        """
        SELECT column_name

        FROM information_schema.columns

        WHERE table_schema='gold'

        AND table_name='scheme'

        ORDER BY ordinal_position
        """,

        engine

    )["column_name"].tolist()

    for col in db_cols:

        if col not in df.columns:

            df[col] = None

    df = df[db_cols]

    # =================================================
    # INSERT
    # =================================================

    df.to_sql(

        "scheme",

        engine,

        schema="gold",

        if_exists="append",

        index=False,

        method="multi",

        chunksize=10000

    )

    print("\nLoading Completed")
    print("-" * 80)
    print("Rows Inserted :", len(df))

    # =================================================
    # VALIDATION
    # =================================================

    preview = pd.read_sql(

        """
        SELECT *
        FROM gold.scheme
        ORDER BY created_at DESC
        LIMIT 10
        """,

        engine

    )

    #print("\nGold Scheme Preview")
    #print("-" * 80)
    #print(preview)

    count = pd.read_sql(

        """
        SELECT COUNT(*) total_rows
        FROM gold.scheme
        """,

        engine

    )

    print("\nGold Row Count")
    print("-" * 80)
    print(count)

# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD SCHEME ETL")
    print("=" * 80)

    try:

        # =================================================
        # EXTRACT
        # =================================================

        transaction_df, investor_df = extract_scheme()

        if transaction_df.empty and investor_df.empty:

            print("No Scheme data found")

            return

        # =================================================
        # TRANSFORM
        # =================================================

        gold_df = transform_scheme(

            transaction_df,
            investor_df

        )

        if gold_df.empty:

            print("No Scheme records after transformation")

            return

        # =================================================
        # LOAD
        # =================================================

        load_scheme(gold_df)

        print("\n" + "=" * 80)
        print("GOLD SCHEME LOADED SUCCESSFULLY")
        print("=" * 80)

    except Exception as e:

        print("=" * 80)
        print("SCHEME GOLD FAILED")
        print("=" * 80)
        print(e)

        import traceback
        traceback.print_exc()


# =====================================================
# EXECUTE
# =====================================================

if __name__ == "__main__":

    main()