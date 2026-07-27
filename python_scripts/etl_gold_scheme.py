import pandas as pd
import json
from sqlalchemy import create_engine, text
import uuid

# =====================================================
# DATABASE CONNECTION
# =====================================================

engine = create_engine(
    "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
)

master_engine = create_engine(
    "postgresql+psycopg2://postgres:postgres123@localhost:5432/inteliwealth_sh"
)

scheme_master = pd.read_sql("""
    SELECT
        id,
        scheme_code,
        name,
        name_norm,
        name_norm_loose
    FROM public.scheme_master
    """, master_engine)
# =====================================================
# EXTRACT GOLD SCHEME SOURCE DATA
# =====================================================

def extract_scheme():

    print("=" * 80)
    print("Extracting data for Gold Scheme")
    print("=" * 80)


    # =================================================
    # TRANSACTION MASTER
    # Primary source for:
    # scheme_code
    # scheme_name
    # category
    # =================================================

    transaction_query = """
        SELECT
            source,
            amc_code,
            prodcode,
            scheme,
            funddesc,
            scheme_type
        FROM silver.transaction_master_new
    """


    # =================================================
    # SIP MASTER
    # Additional source for:
    # scheme_code
    # scheme_name
    # plan
    # =================================================

    # sip_query = """
    #     SELECT
    #         source,
    #         amc_code,
    #         scheme_code,
    #         product_code,
    #         scheme_name,
    #         plan
    #     FROM silver.sip_master_new
    # """


    # =================================================
    # INVESTOR MASTER
    # Additional source for:
    # category
    # fund description
    # =================================================

    investor_query = """
        SELECT
            source,
            amc_code,
            product_code,
            scheme_name,
            fund_description,
            categorydesc
        FROM silver.investor_master
    """


    # Execute extraction

    transaction_df = pd.read_sql(
        transaction_query,
        engine
    )

    investor_df = pd.read_sql(
        investor_query,
        engine
    )


    print("\nExtraction Completed")
    print("-" * 80)

    print(f"Transaction Rows : {len(transaction_df)}")
    print(f"Investor Rows    : {len(investor_df)}")


    print("\nTransaction Preview")
    print("-" * 80)
    print(transaction_df.head())

    print("\nInvestor Preview")
    print("-" * 80)
    print(investor_df.head())


    return (
        transaction_df,
        investor_df
    )

# =====================================================
# TRANSFORM GOLD SCHEME DATA
# =====================================================

def transform_scheme(transaction_df, investor_df):

    print("=" * 80)
    print("Transforming Gold Scheme")
    print("=" * 80)


    # =================================================
    # CREATE CLEAN DATASETS
    # =================================================

    transaction_df = transaction_df.copy()
    investor_df = investor_df.copy()


    # =================================================
    # REMOVE DUPLICATE SCHEME MASTER RECORDS
    # =================================================

    transaction_df["join_scheme_code"] = (
        transaction_df["prodcode"]
        .astype("string")
        .str.strip()
    )
    investor_df["join_scheme_code"] = (
        investor_df["product_code"]
        .astype("string")
        .str.strip()
    )


    # Keep only one scheme record
    transaction_scheme = transaction_df.drop_duplicates(
        subset=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        keep="first"
    )

    investor_scheme = investor_df.drop_duplicates(
        subset=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        keep="first"
    )


    print("Unique Transaction Schemes :", len(transaction_scheme))
    print("Unique Investor Schemes    :", len(investor_scheme))


    # =================================================
    # BASE = TRANSACTION SCHEME
    # =================================================

    gold_df = transaction_scheme.copy()

    # =================================================
    # JOIN INVESTOR
    # =================================================

    gold_df = gold_df.merge(
        investor_scheme[
            [
                "source",
                "amc_code",
                "join_scheme_code",
                "fund_description",
                "categorydesc"
            ]
        ],
        on=[
            "source",
            "amc_code",
            "join_scheme_code"
        ],
        how="left"
    )

    # gold_df["amfi_code"] = gold_df["scheme_code_master"]


    print("\nAfter Merge")
    print("-"*80)
    print("Rows :", len(gold_df))

    scheme_name = (
        gold_df["funddesc"]
        .fillna(gold_df["scheme"])
        .fillna(gold_df["fund_description"])
    )
    # =================================================
    # GOLD MAPPING
    # =================================================

    gold_df = pd.DataFrame({

        "id": [uuid.uuid4() for _ in range(len(gold_df))],

        "rta": gold_df["source"],


        "scheme_code": (
            gold_df["join_scheme_code"]
            .astype("string")
            .str.strip()
            .str.upper()
        ),


        "scheme_name": scheme_name,

        "category": (
            gold_df["scheme_type"]
            .fillna(gold_df["categorydesc"])
        ),


        "plan": (
            scheme_name
            .str.extract(r"(Direct|Regular)", expand=False)
        ),


        "isin": None,
        "amc_code": gold_df["amc_code"],
        # "amc_id": None,
        "amfi_code": None,
        "category_id": None,
        "plan_type": None,
        "option_type": None,
        "rta_scheme_code": gold_df["join_scheme_code"],
        "benchmark_id": None,
        "expense_ratio": None,


        "exit_load_json": None,


        "lock_in_months": None,
        "riskometer": None,
        "status": None

    })

    # =================================================
    # CHECK MATCH WITH SCHEME MASTER
    # =================================================

    import re

    def normalize_name(name):
        if pd.isna(name):
            return None

        name = str(name).upper()
        name = re.sub(r'[^A-Z0-9 ]', ' ', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    gold_df["name_norm"] = gold_df["scheme_name"].apply(normalize_name)

    gold_df = gold_df.merge(
        scheme_master,
        on="name_norm",
        how="left",
        suffixes=("", "_master")
    )
    # Populate AMFI code from scheme_master
    gold_df["amfi_code"] = gold_df["scheme_code_master"]
    print("\nScheme Master Match")
    print("-" * 80)
    print("Total     :", len(gold_df))
    print("Matched   :", gold_df["id_master"].notna().sum())
    print("Unmatched :", gold_df["id_master"].isna().sum())
    # =================================================
    # CLEAN
    # =================================================

    gold_df["scheme_code"] = (
        gold_df["scheme_code"]
        .astype("string")
        .str.strip()
    )

    # Remove records where scheme_code is missing

    gold_df = gold_df[
        gold_df["scheme_code"].notna()
    ]
    print("\nAMFI Code Populated")
    print("-" * 80)
    print(gold_df["amfi_code"].notna().sum())
    # =================================================
    # AMC ID LOOKUP
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
    # FINAL DEDUP
    # =================================================

    gold_df = gold_df.drop_duplicates(
        subset=[
            "rta",
            "scheme_code"
        ],
        keep="first"
    )


    print("\nFinal Gold Scheme")
    print("-"*80)

    print("Rows :", len(gold_df))

    print(gold_df.head())


    return gold_df
# =====================================================
# LOAD GOLD SCHEME
# =====================================================

def load_scheme(df):

    print("=" * 80)
    print("Loading Gold Scheme")
    print("=" * 80)


    with engine.begin() as conn:

        # Clear existing gold data
        conn.execute(
            text(
                "TRUNCATE TABLE gold.scheme"
            )
        )


        print("\nColumns Loaded")
        print("-" * 80)
        print(df.columns.tolist())


        print("\nData Preview")
        print("-" * 80)
        print(df.head())


        print("\nData Types")
        print("-" * 80)
        print(df.dtypes)


        # Insert into gold table

        df.to_sql(
            name="scheme",
            schema="gold",
            con=conn,
            if_exists="append",
            index=False,
            chunksize=500
        )


    print("\nLoading Completed")
    print("-" * 80)

    print(
        "Rows Inserted :",
        len(df)
    )


    # =================================================
    # DATABASE VALIDATION
    # =================================================

    preview = pd.read_sql(
        """
        SELECT *
        FROM gold.scheme
        LIMIT 10
        """,
        engine
    )


    print("\nGold Scheme Preview")
    print("-" * 80)

    print(preview)



    # Row count

    count_df = pd.read_sql(
        """
        SELECT COUNT(*) AS total_rows
        FROM gold.scheme
        """,
        engine
    )


    print("\nGold Row Count")
    print("-" * 80)

    print(
        count_df.iloc[0]["total_rows"]
    )


    # NULL validation

    null_check = pd.read_sql(
        """
        SELECT
            COUNT(*) FILTER(
                WHERE scheme_code IS NULL
            ) AS null_scheme_code,

            COUNT(*) FILTER(
                WHERE rta IS NULL
            ) AS null_rta

        FROM gold.scheme
        """,
        engine
    )


    print("\nNULL Validation")
    print("-" * 80)

    print(null_check)

def main():

    print("=" * 80)
    print("STARTING GOLD SCHEME ETL")
    print("=" * 80)


    # Extract
    transaction_df, investor_df = extract_scheme()


    # Transform
    gold_df = transform_scheme(
        transaction_df,
        investor_df
    )


    # Load
    load_scheme(gold_df)


    print("\n" + "=" * 80)
    print("GOLD SCHEME ETL COMPLETED SUCCESSFULLY")
    print("=" * 80)

if __name__ == "__main__":
    main()