import pandas as pd
from utils.db import engine

# =====================================================
# SAFE READ (RE-EXPORTED)
# =====================================================
# Identical logic to Gold's safe_read, so we re-export it from common.
from common.etl_helpers import safe_read

# =====================================================
# GET LAST PROCESSED TIME FROM SILVER
# =====================================================
# DIVERGED from Gold: Queries `silver.{table_name}` instead of taking a 
# fully-qualified schema.table, and the fallback Timestamp is timezone-aware 
# (tz="UTC") whereas Gold's fallback is timezone-naive.

def get_last_processed_time(table_name):
    try:
        result = pd.read_sql(
            f"""
            SELECT MAX(created_at) AS last_time
            FROM silver.{table_name}
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
# NORMALIZE DATA FOR DUPLICATE CHECK
# =====================================================
# DIVERGED from Gold: Silver version drops "updated_at" and "flag" in addition
# to "created_at", whereas Gold only drops "created_at".

def normalize_for_compare(df):
    df = df.copy()
    df = df.drop(
        columns=["created_at", "updated_at", "flag"],
        errors="ignore"
    )
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = (
                pd.to_datetime(df[col], errors="coerce")
                .dt.strftime("%Y-%m-%d")
            )
        else:
            df[col] = df[col].astype("string").str.strip()
    return df

# =====================================================
# CREATE ROW HASH KEY
# =====================================================
# DIVERGED from Gold: Silver creates a hash key across ALL columns in the DataFrame.
# Gold ETL scripts define this function locally to hash specific key columns per table.

def create_row_key(df):
    df = normalize_for_compare(df)
    return (
        df.fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )

# =====================================================
# LOAD STATE DIMENSION
# =====================================================

def load_state_dimension():

    state_dim = safe_read(
        """
        SELECT
            state_id,
            state_name
        FROM bronze.state_code
        """
    )


    if state_dim.empty:

        return state_dim



    state_dim["state_id"] = pd.to_numeric(
        state_dim["state_id"],
        errors="coerce"
    )


    state_dim["state_name"] = (
        state_dim["state_name"]
        .astype("string")
        .str.strip()
        .str.title()
    )


    return state_dim


# =====================================================
# GET SILVER TABLE COLUMNS
# =====================================================

def get_table_columns(table_name):

    query = f"""

    SELECT column_name

    FROM information_schema.columns

    WHERE table_schema='silver'

    AND table_name='{table_name}'

    ORDER BY ordinal_position

    """


    return pd.read_sql(
        query,
        engine
    )["column_name"].tolist()


# =====================================================
# ROUND DECIMAL COLUMNS
# =====================================================

def round_decimal_columns(df):


    df = df.copy()



    float_cols = df.select_dtypes(
        include=[
            "float16",
            "float32",
            "float64"
        ]
    ).columns



    for col in float_cols:


        df[col] = df[col].round(4)



    return df


# =====================================================
# APPEND ONLY NEW DATA TO SILVER
# USING TIMESTAMP + FLAG LOGIC
# =====================================================

def append_new_rows(
        df,
        table_name
):


    if df.empty:

        print(
            f"{table_name} : No data"
        )

        return



    # -------------------------------------------------
    # GET LAST SILVER LOAD TIME
    # -------------------------------------------------

    last_time = get_last_processed_time(
        table_name
    )



    # -------------------------------------------------
    # FILTER BRONZE DATA BY TIMESTAMP
    # -------------------------------------------------

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    last_time = pd.Timestamp(last_time)

    # Make BOTH timezone-naive
    if getattr(df["created_at"].dt, "tz", None) is not None:
        df["created_at"] = df["created_at"].dt.tz_localize(None)

    if last_time.tzinfo is not None:
        last_time = last_time.tz_localize(None)

    df = df[
        df["created_at"] > last_time
    ]



    if df.empty:

        print(
            f"{table_name} : No new timestamp records"
        )

        return




    # -------------------------------------------------
    # CHECK EXISTING SILVER DATA
    # -------------------------------------------------

    try:

        existing = pd.read_sql(
            f"""
            SELECT *
            FROM silver.{table_name}
            """,
            engine
        )


    except Exception:


        existing = pd.DataFrame()



    # -------------------------------------------------
    # REMOVE DUPLICATES
    # -------------------------------------------------

    if not existing.empty:


        old_keys = set(
            create_row_key(existing)
        )


        new_keys = create_row_key(df)


        df = df.loc[
            ~new_keys.isin(old_keys)
        ]



    if df.empty:


        print(
            f"{table_name} : Duplicate data skipped"
        )

        return




    # -------------------------------------------------
    # SILVER AUDIT TIMESTAMP
    # -------------------------------------------------

    load_time = pd.Timestamp.now()


    df["created_at"] = load_time

    df["updated_at"] = load_time



    df = df.drop(
        columns=[
            "flag"
        ],
        errors="ignore"
    )



    # -------------------------------------------------
    # MATCH DATABASE COLUMNS
    # -------------------------------------------------

    db_cols = get_table_columns(
        table_name
    )


    for col in db_cols:

        if col not in df.columns:

            df[col] = None



    df = df[db_cols]




    # -------------------------------------------------
    # INSERT INTO SILVER
    # -------------------------------------------------

    df.to_sql(
        table_name,
        engine,
        schema="silver",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )



    print(
        f"{table_name} : {len(df)} rows inserted into Silver"
    )
