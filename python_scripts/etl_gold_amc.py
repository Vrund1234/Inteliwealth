import pandas as pd
# from sqlalchemy import create_engine
import traceback
from utils.db import engine


# # =====================================================
# # DATABASE CONNECTION
# # =====================================================

# engine = create_engine(
#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
# )



# =====================================================
# EXTRACT
# =====================================================

def extract_amc():

    print("=" * 80)
    print("STARTING GOLD AMC ETL")
    print("=" * 80)


    query = """
        SELECT
        source,
        prodcode,
        td_fund,
        scheme,
        brokcode,
        src_brk_code
    FROM silver.transaction_master_new
    """


    df = pd.read_sql(query, engine)


    print("\nExtraction Completed")
    print("-" * 80)
    print("Rows fetched :", len(df))
    print("Columns fetched :", len(df.columns))


    return df

# =====================================================
# TRANSFORM
# =====================================================

def transform_amc(df):


    print("=" * 80)
    print("Transforming Gold AMC")
    print("=" * 80)



    gold_df = pd.DataFrame()

    # =================================================
    # RTA
    # =================================================

    gold_df["rta"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =================================================
    # AMC CODE
    #
    # CAMS -> prodcode
    # KFIN -> td_fund
    # =================================================


    gold_df["amc_code"] = None

    cams_mask = gold_df["rta"] == "CAMS"

    kfin_mask = gold_df["rta"] == "KFIN"

    gold_df.loc[cams_mask, "amc_code"] = (
        df.loc[cams_mask, "prodcode"]
    )

    gold_df.loc[kfin_mask, "amc_code"] = (
        df.loc[kfin_mask, "td_fund"]
    )

    gold_df["amc_code"] = (
        gold_df["amc_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =================================================
    # NAME
    # AMC NAME LOOKUP
    # =================================================

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

    # Create lookup key matching the existing amc_code logic
    lookup_code = (
        gold_df["amc_code"]
        .astype(str)
        .str.extract(r"^([A-Z]+)", expand=False)
        .str.upper()
    )

    gold_df["name"] = lookup_code.map(
        amc_lookup.set_index("amc_code")["amc_name"]
    )

    # =================================================
    # Remaining fields
    # =================================================

    gold_df["short_name"] = None

    gold_df["logo_url"] = None

    gold_df["status"] = None

    # =================================================
    # ARN
    # =================================================

    gold_df["arn"] = (
        df["brokcode"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =================================================
    # SUB ARN
    # =================================================

    gold_df["sub_arn"] = (
        df["src_brk_code"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    gold_df["arn"] = gold_df["arn"].replace("", None)
    gold_df["sub_arn"] = gold_df["sub_arn"].replace("", None)

    # =================================================
    # Select required columns only
    # =================================================

    gold_df = gold_df[
        [
            "amc_code",
            "name",
            "short_name",
            "rta",
            "logo_url",
            "status",
            "arn",
            "sub_arn"
        ]
    ]

    # =================================================
    # Remove only invalid AMC codes
    # =================================================

    gold_df = gold_df[
        gold_df["amc_code"] != ""
    ]

    # =================================================
    # AMC MASTER CREATION
    #
    # One AMC code should have one row
    #
    # Keep first available scheme name
    # =================================================

    # gold_df = (
    #     gold_df
    #     .drop_duplicates(
    #         subset=[
    #             "amc_code",
    #             "rta"
    #         ],
    #         keep="first"
    #     )
    #     .reset_index(drop=True)
    # )

    # =================================================
    # Length validation
    # =================================================


    gold_df["amc_code"] = (
        gold_df["amc_code"]
        .str[:20]
    )

    gold_df["name"] = (
        gold_df["name"]
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

    print("=" * 80)
    print("Gold AMC Preview")
    print("=" * 80)

    print(gold_df.head())


    print("\nRows ready for Gold :", len(gold_df))


    return gold_df

# =====================================================
# LOAD
# =====================================================

def load_amc(gold_df):


    print("=" * 80)
    print("Loading into gold.amc")
    print("=" * 80)



    try:

        gold_df.to_sql(
            name="amc",
            con=engine,
            schema="gold",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000
        )


        print(
            f"{len(gold_df)} rows inserted successfully."
        )


        return True

    except Exception:

        print("\nERROR while loading gold.amc")

        traceback.print_exc()

        return False

# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    df = extract_amc()

    if df.empty:

        print(
            "No data found"
        )

        exit()

    gold_df = transform_amc(df)

    print("\nAMC Length Validation")
    print("=" * 80)


    limits = {

        "amc_code":20,
        "name":255,
        "short_name":50,
        "rta":20,
        "logo_url":512,
        "status":20,
         "arn":50,
        "sub_arn":50

    }


    for col, limit in limits.items():

        max_len = (
            gold_df[col]
            .fillna("")
            .astype(str)
            .str.len()
            .max()
        )


        print(
            f"{col:<15} Max={max_len:<5} Limit={limit}"
        )



    success = load_amc(gold_df)



    if success:

        print(
            "\nGold AMC ETL Completed Successfully."
        )

    else:

        print(
            "\nGold AMC ETL Failed."
        )