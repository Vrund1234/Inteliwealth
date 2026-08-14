import pandas as pd
import traceback

from utils.db import engine


# ============================================================
# SAFE READ
# ============================================================

def safe_read(query):

    try:

        return pd.read_sql(
            query,
            engine
        )

    except Exception as e:

        print("SQL ERROR :", e)

        return pd.DataFrame()


# ============================================================
# GET LAST PROCESSED TIME
# ============================================================

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


# ============================================================
# NORMALIZE DATA FOR COMPARISON
# ============================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=[
            "created_at"
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
                .dt.strftime("%Y-%m-%d")
            )

        else:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )

    return df


# ============================================================
# CREATE NATURAL KEY
# ============================================================

def create_row_key(df):

    df = normalize_for_compare(
        df
    )

    return (
        df[
            [
                "scheme_id",
                "nav_date"
            ]
        ]
        .fillna("")
        .astype(str)
        .agg(
            "|".join,
            axis=1
        )
    )


# ============================================================
# EXTRACT GOLD SCHEME NAV
# ============================================================

def extract_scheme_nav():

    print("=" * 80)
    print("EXTRACTING GOLD SCHEME NAV")
    print("=" * 80)

    # ========================================================
    # GET LAST PROCESSED TIME
    # ========================================================

    last_time = get_last_processed_time()

    print(
        "Last processed time:",
        last_time
    )

    # ========================================================
    # EXTRACT FROM SILVER TRANSACTION MASTER
    #
    # ARN:
    # brokcode
    #
    # SUB ARN:
    # src_brk_code
    # ========================================================

    df = safe_read(
        """
        SELECT

            source,

            prodcode AS scheme_code,

            traddate,

            purprice,

            brokcode AS arn,

            src_brk_code AS sub_arn,

            created_at

        FROM silver.transaction_master_new

        WHERE purprice IS NOT NULL
        """
    )

    if df.empty:

        print(
            "No data found."
        )

        return df

    # ========================================================
    # CLEAN CREATED AT
    # ========================================================

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    last_time = pd.Timestamp(
        last_time
    )

    # ========================================================
    # REMOVE TIMEZONE DIFFERENCE
    # ========================================================

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

        last_time = (
            last_time
            .tz_localize(None)
        )

    # ========================================================
    # TIMESTAMP FILTER
    # ========================================================

    df = df[
        df["created_at"] > last_time
    ]

    print(
        "Rows fetched:",
        len(df)
    )

    return df


# ============================================================
# TRANSFORM GOLD SCHEME NAV
# ============================================================

def transform_scheme_nav(df):

    print("=" * 80)
    print("TRANSFORMING GOLD SCHEME NAV")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    # ========================================================
    # LOAD GOLD SCHEME
    # ========================================================

    print(
        "Loading gold.scheme..."
    )

    gold_scheme = safe_read(
        """
        SELECT

            id,

            rta,

            scheme_code

        FROM gold.scheme
        """
    )

    if gold_scheme.empty:

        print(
            "Gold Scheme table is empty."
        )

        return pd.DataFrame()

    print(
        "Gold Scheme rows:",
        len(gold_scheme)
    )

    # ========================================================
    # CLEAN SOURCE
    # ========================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN SCHEME CODE
    # ========================================================

    df["scheme_code"] = (
        df["scheme_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN GOLD SCHEME RTA
    # ========================================================

    gold_scheme["rta"] = (
        gold_scheme["rta"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN GOLD SCHEME CODE
    # ========================================================

    gold_scheme["scheme_code"] = (
        gold_scheme["scheme_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # ========================================================
    # CLEAN ARN
    #
    # SOURCE:
    # silver.transaction_master_new.brokcode
    # ========================================================

    df["arn"] = (
        df["arn"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df.loc[
        df["arn"] == "",
        "arn"
    ] = None

    # ========================================================
    # CLEAN SUB ARN
    #
    # SOURCE:
    # silver.transaction_master_new.src_brk_code
    # ========================================================

    df["sub_arn"] = (
        df["sub_arn"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df.loc[
        df["sub_arn"] == "",
        "sub_arn"
    ] = None

    # ========================================================
    # MAP SCHEME ID
    #
    # source + scheme_code
    #
    # source       -> gold.scheme.rta
    # scheme_code  -> gold.scheme.scheme_code
    # ========================================================

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
            "id": "scheme_id"
        },
        inplace=True
    )

    # ========================================================
    # SCHEME VALIDATION
    # ========================================================

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)

    print(
        "Total rows:",
        len(df)
    )

    print(
        "Matched Scheme IDs:",
        df["scheme_id"].notna().sum()
    )

    print(
        "Missing Scheme IDs:",
        df["scheme_id"].isna().sum()
    )

    # ========================================================
    # MISSING SCHEME SAMPLE
    # ========================================================

    missing_scheme = df[
        df["scheme_id"].isna()
    ]

    if not missing_scheme.empty:

        print()
        print(
            "Missing Scheme Samples:"
        )

        print(
            missing_scheme[
                [
                    "source",
                    "scheme_code"
                ]
            ]
            .drop_duplicates()
            .head(20)
            .to_string(
                index=False
            )
        )

    # ========================================================
    # BUILD GOLD DATAFRAME
    # ========================================================

    gold_df = pd.DataFrame(
        index=df.index
    )

    # ========================================================
    # SCHEME ID
    # ========================================================

    gold_df["scheme_id"] = (
        df["scheme_id"]
    )

    # ========================================================
    # NAV DATE
    # ========================================================

    gold_df["nav_date"] = (
        pd.to_datetime(
            df["traddate"],
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # NAV
    #
    # purprice -> nav
    # ========================================================

    gold_df["nav"] = pd.to_numeric(
        df["purprice"],
        errors="coerce"
    )

    # ========================================================
    # REPURCHASE NAV
    # ========================================================

    gold_df["repurchase_nav"] = None

    # ========================================================
    # SOURCE
    # ========================================================

    gold_df["source"] = (
        df["source"]
    )

    # ========================================================
    # ARN
    #
    # brokcode -> arn
    # ========================================================

    gold_df["arn"] = (
        df["arn"]
    )

    # ========================================================
    # SUB ARN
    #
    # src_brk_code -> sub_arn
    # ========================================================

    gold_df["sub_arn"] = (
        df["sub_arn"]
    )

    # ========================================================
    # REMOVE INVALID ROWS
    # ========================================================

    before = len(
        gold_df
    )

    gold_df = gold_df[
        gold_df["scheme_id"].notna()
        &
        gold_df["nav_date"].notna()
        &
        gold_df["nav"].notna()
    ].copy()

    removed = (
        before
        -
        len(gold_df)
    )

    print(
        "Invalid rows removed:",
        removed
    )

    # ========================================================
    # REMOVE DUPLICATES IN CURRENT BATCH
    #
    # Natural key:
    # scheme_id + nav_date
    # ========================================================

    before_dedup = len(
        gold_df
    )

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=[
                "scheme_id",
                "nav_date"
            ],
            keep="last"
        )
        .reset_index(
            drop=True
        )
    )

    print(
        "Current batch duplicates removed:",
        before_dedup - len(gold_df)
    )

    # ========================================================
    # CHECK EXISTING GOLD SCHEME NAV
    #
    # Prevent duplicate scheme_id + nav_date
    # ========================================================

    print()
    print(
        "Checking existing gold.scheme_nav..."
    )

    existing_nav = safe_read(
        """
        SELECT

            scheme_id,

            nav_date

        FROM gold.scheme_nav
        """
    )

    if not existing_nav.empty:

        existing_nav["nav_date"] = (
            pd.to_datetime(
                existing_nav["nav_date"],
                errors="coerce"
            )
            .dt.date
        )

        existing_nav = (
            existing_nav[
                [
                    "scheme_id",
                    "nav_date"
                ]
            ]
            .drop_duplicates()
        )

        # ====================================================
        # REMOVE EXISTING NATURAL KEYS
        # ====================================================

        gold_df = gold_df.merge(

            existing_nav.assign(
                _exists=True
            ),

            on=[
                "scheme_id",
                "nav_date"
            ],

            how="left"

        )

        existing_count = (
            gold_df["_exists"]
            .notna()
            .sum()
        )

        print(
            "Existing rows skipped:",
            existing_count
        )

        gold_df = (
            gold_df[
                gold_df["_exists"].isna()
            ]
            .drop(
                columns=[
                    "_exists"
                ]
            )
        )

    # ========================================================
    # AUDIT TIMESTAMP
    # ========================================================

    gold_df["created_at"] = (
        pd.Timestamp.now()
    )

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================

    gold_df = gold_df[
        [
            "scheme_id",
            "nav_date",
            "nav",
            "repurchase_nav",
            "source",
            "arn",
            "sub_arn",
            "created_at"
        ]
    ].copy()

    # ========================================================
    # FINAL VALIDATION
    # ========================================================

    print("=" * 80)
    print("GOLD SCHEME NAV VALIDATION")
    print("=" * 80)

    print(
        "Rows ready:",
        len(gold_df)
    )

    print(
        "Missing scheme_id:",
        gold_df["scheme_id"].isna().sum()
    )

    print(
        "Missing nav_date:",
        gold_df["nav_date"].isna().sum()
    )

    print(
        "Missing nav:",
        gold_df["nav"].isna().sum()
    )

    print(
        "Missing ARN:",
        gold_df["arn"].isna().sum()
    )

    print(
        "Missing Sub ARN:",
        gold_df["sub_arn"].isna().sum()
    )

    if not gold_df.empty:

        print()
        print(
            "Gold Scheme NAV Preview:"
        )

        print(
            gold_df.head(
                10
            ).to_string(
                index=False
            )
        )

    return gold_df


# ============================================================
# LOAD GOLD SCHEME NAV
# ============================================================

def load_scheme_nav(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.SCHEME_NAV")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No new records found."
        )

        return True

    try:

        gold_df.to_sql(

            name="scheme_nav",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )

        print(
            f"{len(gold_df):,} rows inserted "
            "into gold.scheme_nav."
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


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 80)
    print("STARTING GOLD SCHEME NAV ETL")
    print("=" * 80)

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        df = extract_scheme_nav()

        # ====================================================
        # TRANSFORM
        # ====================================================

        if not df.empty:

            gold_df = transform_scheme_nav(
                df
            )

        else:

            gold_df = pd.DataFrame()

        # ====================================================
        # LOAD
        # ====================================================

        status = load_scheme_nav(
            gold_df
        )

        # ====================================================
        # FINAL STATUS
        # ====================================================

        print()
        print("=" * 80)

        if status:

            print(
                "GOLD SCHEME NAV ETL "
                "COMPLETED SUCCESSFULLY"
            )

        else:

            print(
                "GOLD SCHEME NAV ETL FAILED"
            )

        print("=" * 80)

    except Exception as e:

        print()
        print("=" * 80)
        print(
            "GOLD SCHEME NAV ETL ERROR"
        )
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            e
        )

        traceback.print_exc()