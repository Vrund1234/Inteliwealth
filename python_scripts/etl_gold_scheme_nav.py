import pandas as pd
import hashlib
import uuid
from sqlalchemy import create_engine, text


# =====================================================
# DATABASE CONNECTION
# =====================================================

engine = create_engine(
    "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
)


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
            purprice
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


    # -------------------------------
    # Generate scheme_id
    # -------------------------------

    df["scheme_id"] = df.apply(
        lambda row: str(
            uuid.UUID(
                hashlib.md5(
                    f"{row['source']}|{row['scheme_code']}".encode()
                ).hexdigest()
            )
        ),
        axis=1
    )


    # -------------------------------
    # NAV DATE
    # -------------------------------

    df["nav_date"] = pd.to_datetime(
        df["traddate"],
        errors="coerce"
    )


    # -------------------------------
    # NAV VALUE
    # -------------------------------

    df["nav"] = pd.to_numeric(
        df["purprice"],
        errors="coerce"
    )


    # -------------------------------
    # REPURCHASE NAV
    # Not available currently
    # -------------------------------

    # -------------------------------
    # REPURCHASE NAV
    # Using PURPRICE as Repurchase Price
    # -------------------------------

    df["repurchase_nav"] = None
    # -------------------------------
    # Source
    # -------------------------------

    df["source"] = df["source"]


    # -------------------------------
    # Final columns
    # -------------------------------

    gold_df = df[
        [
            "scheme_id",
            "nav_date",
            "nav",
            "repurchase_nav",
            "source"
        ]
    ]


    # remove invalid rows

    gold_df = gold_df[
        gold_df["scheme_id"].notna()
        &
        gold_df["nav_date"].notna()
        &
        gold_df["nav"].notna()
    ]


    # Deduplicate
    # requirement:
    # dedup by scheme_id + nav_date

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