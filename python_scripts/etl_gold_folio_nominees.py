import pandas as pd
import hashlib
import uuid
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
            FROM gold.folio_nominees
            """,
            engine
        )

        last_time = result.iloc[0]["last_time"]

        if pd.isna(last_time):

            return pd.Timestamp("1900-01-01")

        return pd.to_datetime(last_time)

    except Exception:

        return pd.Timestamp("1900-01-01")


# =====================================================
# NORMALIZE FOR COMPARISON
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=[
            "created_at"
        ],
        errors="ignore"
    )

    for col in df.columns:

        if pd.api.types.is_datetime64_any_dtype(df[col]):

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


# =====================================================
# CREATE NATURAL KEY
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)

    return (

        df[
            [
                "holding_id",
                "seq"
            ]
        ]
        .fillna("")
        .astype(str)
        .agg("|".join, axis=1)

    )


# =====================================================
# EXTRACT FOLIO NOMINEES
# =====================================================

def extract_folio_nominees():

    print("=" * 80)
    print("Extracting Gold Folio Nominees")
    print("=" * 80)

    last_time = get_last_processed_time()

    df = safe_read(
        """
        SELECT *
        FROM silver.investor_master
        """
    )

    if df.empty:

        print("No data found.")

        return df

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    last_time = pd.Timestamp(last_time)

    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )

    if last_time.tzinfo is not None:

        last_time = last_time.tz_localize(None)

    df = df[
        df["created_at"] > last_time
    ]

    print("Rows fetched :", len(df))

    return df

# =====================================================
# TRANSFORM GOLD FOLIO NOMINEES
# =====================================================

def transform_folio_nominees(df):

    print("=" * 80)
    print("Transforming Gold Folio Nominees")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    # =====================================================
    # LOAD GOLD HOLDINGS
    # =====================================================

    holdings = safe_read(
        """
        SELECT
            id,
            rta,
            folio_number
        FROM gold.holdings
        """
    )

    if holdings.empty:

        print("Gold Holdings table is empty.")

        return pd.DataFrame()

    # =====================================================
    # CLEAN JOIN KEYS
    # =====================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["folio_no"] = (
        df["folio_no"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    holdings["rta"] = (
        holdings["rta"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    holdings["folio_number"] = (
        holdings["folio_number"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    # =====================================================
    # MAP HOLDING ID
    # =====================================================

    df = df.merge(

        holdings,

        left_on=[
            "source",
            "folio_no"
        ],

        right_on=[
            "rta",
            "folio_number"
        ],

        how="left"

    )

    df.rename(

        columns={
            "id": "holding_id"
        },

        inplace=True

    )

    print("Matched Holding IDs :", df["holding_id"].notna().sum())
    print("Missing Holding IDs :", df["holding_id"].isna().sum())

    # =====================================================
    # BUILD NOMINEE ROWS
    # =====================================================

    gold_rows = []

    nominee_configs = [

        (1, "nominee1"),
        (2, "nominee2"),
        (3, "nominee3")

    ]

    for _, row in df.iterrows():

        for seq, prefix in nominee_configs:

            nominee_name = row.get(f"{prefix}_name")

            if pd.isna(nominee_name):

                nominee_name = None

            else:

                nominee_name = str(
                    nominee_name
                ).strip()

                if nominee_name == "":

                    nominee_name = None

            relationship = row.get(
                f"{prefix}_relation"
            )

            if pd.isna(relationship):

                relationship = None

            else:

                relationship = str(
                    relationship
                ).strip()

            percentage = pd.to_numeric(

                row.get(f"{prefix}_percentage"),

                errors="coerce"

            )

                        # ============================================
            # BUILD GOLD ROW
            # ============================================

            gold_rows.append({

                "holding_id": row["holding_id"],

                "seq": seq,

                "name": nominee_name,

                "relationship": relationship,

                "percentage": percentage,

                "dob": None,

                "is_minor": None,

                "guardian_name": None,

                "id_type": None,

                "id_no": None,

                "address": None

            })

    # =====================================================
    # CREATE GOLD DATAFRAME
    # =====================================================

    gold_df = pd.DataFrame(

        gold_rows,

        columns=[

            "holding_id",

            "seq",

            "name",

            "relationship",

            "percentage",

            "dob",

            "is_minor",

            "guardian_name",

            "id_type",

            "id_no",

            "address"

        ]

    )

    # =====================================================
    # REMOVE INVALID ROWS
    # =====================================================

    gold_df = gold_df[

        gold_df["holding_id"].notna()

    ]

    # =====================================================
    # REMOVE DUPLICATES IN CURRENT BATCH
    # =====================================================

    gold_df = gold_df.drop_duplicates(

        subset=[

            "holding_id",

            "seq"

        ],

        keep="last"

    )

    # =====================================================
    # LENGTH VALIDATION
    # =====================================================

    gold_df["name"] = (

        gold_df["name"]

        .astype("string")

        .str[:255]

    )

    gold_df["relationship"] = (

        gold_df["relationship"]

        .astype("string")

        .str[:60]

    )

    gold_df["guardian_name"] = (

        gold_df["guardian_name"]

        .astype("string")

        .str[:255]

    )

    gold_df["id_type"] = (

        gold_df["id_type"]

        .astype("string")

        .str[:20]

    )

    gold_df["id_no"] = (

        gold_df["id_no"]

        .astype("string")

        .str[:50]

    )

    gold_df["address"] = (

        gold_df["address"]

        .astype("string")

        .str[:500]

    )

    # =====================================================
    # AUDIT TIMESTAMP
    # =====================================================

    gold_df["created_at"] = pd.Timestamp.now()

    print("Rows Ready :", len(gold_df))

    return gold_df

# =====================================================
# LOAD GOLD FOLIO NOMINEES
# =====================================================

def load_folio_nominees(gold_df):

    print("=" * 80)
    print("Loading Gold Folio Nominees")
    print("=" * 80)

    if gold_df.empty:

        print("No new records found.")

        return True

    # =====================================================
    # LOAD EXISTING GOLD DATA
    # =====================================================

    try:

        existing = pd.read_sql(

            """
            SELECT

                holding_id,
                seq,
                name,
                relationship,
                percentage,
                dob,
                is_minor,
                guardian_name,
                id_type,
                id_no,
                address,
                created_at

            FROM gold.folio_nominees
            """,

            engine

        )

    except Exception:

        existing = pd.DataFrame()

    # =====================================================
    # REMOVE EXISTING DUPLICATES
    # =====================================================

    if not existing.empty:

        old_keys = set(
            create_row_key(existing)
        )

        new_keys = create_row_key(gold_df)

        gold_df = gold_df.loc[
            ~new_keys.isin(old_keys)
        ]

    if gold_df.empty:

        print("Duplicate nominee records skipped.")

        return True

    # =====================================================
    # INSERT INTO GOLD
    # =====================================================

    try:

        gold_df.to_sql(

            name="folio_nominees",

            schema="gold",

            con=engine,

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )

        print(f"{len(gold_df)} rows inserted into Gold Folio Nominees.")

        return True

    except Exception:

        print("FAILED LOADING GOLD FOLIO NOMINEES")

        traceback.print_exc(limit=5)

        return False

# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD FOLIO NOMINEES ETL")
    print("=" * 80)


    # =====================================================
    # EXTRACT
    # =====================================================

    silver_df = extract_folio_nominees()


    if silver_df.empty:

        print("No nominee data found.")

        return


    # =====================================================
    # TRANSFORM
    # =====================================================

    gold_df = transform_folio_nominees(
        silver_df
    )


    if gold_df.empty:

        print("No valid nominee records generated.")

        return


    # =====================================================
    # VALIDATION BEFORE LOAD
    # =====================================================

    print("=" * 80)
    print("FINAL GOLD FOLIO NOMINEES VALIDATION")
    print("=" * 80)


    print("\nColumns:")
    print(gold_df.columns.tolist())


    print("\nData Types:")
    print(gold_df.dtypes)


    print("\nNull Count:")
    print(
        gold_df.isnull().sum()
    )


    print("\nDuplicate Check:")
    
    duplicate_count = (

        gold_df
        .duplicated(
            subset=[
                "holding_id",
                "seq"
            ]
        )
        .sum()

    )


    print(
        "Duplicate holding_id + seq :",
        duplicate_count
    )


    print("\nSample Data:")
    print(
        gold_df.head()
    )


    # =====================================================
    # LOAD
    # =====================================================

    status = load_folio_nominees(
        gold_df
    )


    if status:

        print("\n")
        print("=" * 80)
        print("GOLD FOLIO NOMINEES ETL COMPLETED SUCCESSFULLY")
        print("=" * 80)


        # =================================================
        # FINAL DATABASE CHECK
        # =================================================

        final_count = safe_read(
            """
            SELECT
                COUNT(*) AS total_rows
            FROM gold.folio_nominees
            """
        )


        print("\nGold Folio Nominees Row Count")

        print(
            final_count
        )


    else:

        print("\n")
        print("=" * 80)
        print("GOLD FOLIO NOMINEES ETL FAILED")
        print("=" * 80)



# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    main()