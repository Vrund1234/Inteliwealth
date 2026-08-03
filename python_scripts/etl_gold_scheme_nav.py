import pandas as pd
import hashlib
import uuid
from sqlalchemy import create_engine, text
from utils.db import engine

# =====================================================
# DATABASE CONNECTION
# =====================================================

# engine = create_engine(
#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
# )


# =====================================================
# EXTRACT
# =====================================================

def extract_scheme_nav():

    print("=" * 80)
    print("Extracting Scheme NAV Data")
    print("=" * 80)


    query = """
        SELECT
            source,
            amc_code,
            prodcode AS scheme_code,
            traddate,
            purprice,
            brokcode AS arn,
            src_brk_code AS sub_arn
        FROM silver.transaction_master_new
        WHERE purprice IS NOT NULL
    """


    df = pd.read_sql(
        query,
        engine
    )


    print("\nExtraction Completed")
    print("-" * 80)

    print("Rows fetched :", len(df))

    print("\nPreview")
    print(df.head())


    return df

# =====================================================
# TRANSFORM
# =====================================================

def transform_scheme_nav(df):

    print("=" * 80)
    print("Transforming Scheme NAV")
    print("=" * 80)

    df = df.copy()

    # =====================================================
    # LOAD GOLD SCHEME
    # =====================================================

    gold_scheme = pd.read_sql(
        """
        SELECT
            id,
            rta,
            scheme_code
        FROM gold.scheme
        """,
        engine
    )

    # =====================================================
    # CLEAN KEYS
    # =====================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["scheme_code"] = (
        df["scheme_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    gold_scheme["rta"] = (
        gold_scheme["rta"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    gold_scheme["scheme_code"] = (
        gold_scheme["scheme_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

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

    # =====================================================
    # MAP TO GOLD.SCHEME.ID
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
            "id": "scheme_id"
        },
        inplace=True
    )

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)
    print("Total Rows :", len(df))
    print("Matched    :", df["scheme_id"].notna().sum())
    print("Missing    :", df["scheme_id"].isna().sum())

    print("\nMissing Samples")
    print(
        df.loc[
            df["scheme_id"].isna(),
            ["source", "scheme_code"]
        ]
        .drop_duplicates()
        .head(20)
    )

    # =====================================================
    # NAV DATE
    # =====================================================

    df["nav_date"] = pd.to_datetime(
        df["traddate"],
        errors="coerce"
    )

    # =====================================================
    # NAV
    # =====================================================

    df["nav"] = pd.to_numeric(
        df["purprice"],
        errors="coerce"
    )

    # =====================================================
    # REPURCHASE NAV
    # =====================================================

    df["repurchase_nav"] = None

    # =====================================================
    # FINAL DATAFRAME
    # =====================================================

    gold_df = df[
        [
            "scheme_id",
            "nav_date",
            "nav",
            "repurchase_nav",
            "source",
            "arn",
            "sub_arn"
        ]
    ]

    # Remove invalid rows
    gold_df = gold_df[
        gold_df["scheme_id"].notna()
        &
        gold_df["nav_date"].notna()
        &
        gold_df["nav"].notna()
    ]

    # Deduplicate
    gold_df = gold_df.drop_duplicates(
        subset=[
            "scheme_id",
            "nav_date"
        ],
        keep="last"
    )

    print("\nTransformation Completed")
    print("-" * 80)
    print("Rows ready :", len(gold_df))
    print(gold_df.head())

    return gold_df

# =====================================================
# LOAD
# =====================================================

def load_scheme_nav(df):

    print("=" * 80)
    print("Loading Gold Scheme NAV")
    print("=" * 80)


    with engine.begin() as conn:

        conn.execute(
            text(
                "TRUNCATE TABLE gold.scheme_nav"
            )
        )


        df.to_sql(
            name="scheme_nav",
            schema="gold",
            con=conn,
            if_exists="append",
            index=False,
            chunksize=1000
        )


    print("\nLoad Completed")
    print("-" * 80)

    print(
        "Rows Inserted :",
        len(df)
    )


    # Validation

    preview = pd.read_sql(
        """
        SELECT *
        FROM gold.scheme_nav
        LIMIT 10
        """,
        engine
    )


    print("\nGold Scheme NAV Preview")
    print("-" * 80)

    print(preview)



    count = pd.read_sql(
        """
        SELECT COUNT(*) AS total_rows
        FROM gold.scheme_nav
        """,
        engine
    )


    print("\nRow Count")
    print("-" * 80)

    print(
        count.iloc[0]["total_rows"]
    )



# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD SCHEME NAV ETL")
    print("=" * 80)


    df = extract_scheme_nav()


    gold_df = transform_scheme_nav(
        df
    )


    load_scheme_nav(
        gold_df
    )


    print("\n" + "=" * 80)
    print("GOLD SCHEME NAV ETL COMPLETED")
    print("=" * 80)



if __name__ == "__main__":
    main()