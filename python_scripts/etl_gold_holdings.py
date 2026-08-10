import pandas as pd
import uuid

from datetime import datetime, timezone

from utils.db import engine
from utils.db import master_engine



# =====================================================
# EXTRACT GOLD HOLDINGS DATA
# =====================================================


def extract_holdings():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD HOLDINGS")
    print("=" * 80)



    query = """

    WITH investor_base AS
    (

        SELECT DISTINCT ON
        (

            source,
            folio_no

        )

            source,

            folio_no,

            holding_nature,

            nominee1_name,

            nominee1_relation,

            nominee1_percentage,

            bank_name,

            bank_account_no,

            demat_flag,

            ckyc_no,

            broker_code


        FROM silver.investor_master


        ORDER BY

            source,
            folio_no

    )


    SELECT


        t.*,


        i.holding_nature AS investor_holding_nature,

        i.nominee1_name AS investor_nominee_name,

        i.nominee1_relation AS investor_nominee_relation,

        i.nominee1_percentage AS investor_nominee_percentage,


        t.bank_name AS investor_bank_name,

        t.ac_no AS investor_bank_account_no,


        i.demat_flag AS investor_demat_flag,

        i.ckyc_no AS investor_ckyc_no,

       t.brokcode AS investor_broker_code



    FROM silver.transaction_master_new t



    LEFT JOIN investor_base i


    ON t.source = i.source

    AND t.folio_no = i.folio_no


    """



    df = pd.read_sql(

        query,

        engine

    )



    print()

    print("Extraction Completed")

    print("-" * 80)


    print(
        "Rows fetched:",
        len(df)
    )


    print(
        "Columns fetched:",
        len(df.columns)
    )


    print(df.head())


    return df

# =====================================================
# TRANSFORM GOLD HOLDINGS DATA
# =====================================================


def transform_holdings(df):


    print("=" * 80)
    print("TRANSFORMING GOLD HOLDINGS")
    print("=" * 80)



    gold_df = pd.DataFrame()



    # =====================================================
    # GENERATE HOLDING ID
    # =====================================================


    gold_df["id"] = [

        uuid.uuid4()

        for _ in range(len(df))

    ]



    # =====================================================
    # CLEAN PRODUCT CODE
    # =====================================================


    df["prodcode"] = (
        df["prodcode"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()

    )

    # =====================================================
    # LOAD GOLD SCHEME
    # prodcode -> scheme_id
    # =====================================================

    gold_scheme = pd.read_sql(
        """SELECT
            id,
            rta,
            scheme_code
        FROM gold.scheme""",
        engine
    )


    print(
        "Gold Scheme Rows:",
        len(gold_scheme)
    )

    # =====================================================
    # CLEAN SCHEME KEYS
    # =====================================================

    gold_scheme["scheme_code"] = (
        gold_scheme["scheme_code"]
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

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
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
            "prodcode"
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

    print("Total Holdings:",len(df))

    print("Matched scheme_id:",df["scheme_id"].notna().sum())

    print("Missing scheme_id:",df["scheme_id"].isna().sum())

    print("\nMissing Scheme Samples")

    print(df.loc[df["scheme_id"].isna(),
            [
                "source",
                "prodcode",
                "scheme",
                "funddesc"
            ]
        ]
        .drop_duplicates()
        .head(20)
    )

    return create_holdings_columns(df, gold_df)

# =====================================================
# CREATE GOLD HOLDINGS COLUMNS
# =====================================================

def create_holdings_columns(df, gold_df):

    # =====================================================
    # RTA
    # =====================================================


    gold_df["rta"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =====================================================
    # PAN
    # =====================================================

    gold_df["pan"] = (
        df["pan"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(".0", "", regex=False)
    )


    gold_df.loc[
        gold_df["pan"].isna()
        |
        (gold_df["pan"].str.len() != 10),
        "pan"
    ] = None

    # =====================================================
    # FOLIO NUMBER
    # =====================================================

    folio = (
        df["folio_no"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    scheme_folio = (
        df["scheme_folio_number"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    gold_df["folio_number"] = (
        folio
        .fillna(scheme_folio)
    )

    gold_df.loc[
        gold_df["folio_number"] == "",
        "folio_number"
    ] = None

    # =====================================================
    # HOLDING VALUES
    # =====================================================

    gold_df["units"] = pd.to_numeric(
        df["units"],
        errors="coerce"
    )

    gold_df["market_value"] = pd.to_numeric(
        df["amount"],
        errors="coerce"
    )

    # =====================================================
    # DATES
    # =====================================================


    gold_df["as_on_date"] = (
        pd.to_datetime(
            df["rep_date"],
            errors="coerce"
        )
        .dt.date
    )

    gold_df["folio_date"] = (
        pd.to_datetime(
            df["traddate"],
            errors="coerce"
        )
        .dt.date
    )

    # =====================================================
    # ARN
    # =====================================================

    gold_df["arn"] = (
        df["investor_broker_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )


    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
    ] = None

    # =====================================================
    # HOLDING DETAILS
    # =====================================================


    gold_df["holding_nature"] = (
        df["investor_holding_nature"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_name"] = (
        df["investor_nominee_name"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_relation"] = (
        df["investor_nominee_relation"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["nominee_pct"] = (
        df["investor_nominee_percentage"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # =====================================================
    # KYC STATUS
    # =====================================================

    gold_df["kyc_status"] = None

    kyc_available = (
        df["investor_ckyc_no"]
        .fillna("")
        .astype(str)
        .str.strip()
        != ""
    )

    gold_df.loc[
        kyc_available,
        "kyc_status"
    ] = "Verified"

    # =====================================================
    # BANK DETAILS
    # =====================================================


    gold_df["bank_name"] = (

        df["investor_bank_name"]

        .fillna("")

        .astype(str)

        .str.strip()

    )


    gold_df.loc[

        gold_df["bank_name"] == "",

        "bank_name"

    ] = None



    gold_df["bank_ac_last4"] = (

        df["investor_bank_account_no"]

        .fillna("")

        .astype(str)

        .str.replace(".0", "", regex=False)

        .str.strip()

        .str[-4:]

    )


    gold_df.loc[

        gold_df["bank_ac_last4"] == "",

        "bank_ac_last4"

    ] = None



    # =====================================================
    # DEMAT FLAG
    # =====================================================


    gold_df["demat_flag"] = (

        df["investor_demat_flag"]

        .fillna("")

        .astype(str)

        .str.strip()

    )


    gold_df.loc[

        gold_df["demat_flag"] == "",

        "demat_flag"

    ] = None

        # =====================================================
    # APPLICATION MANAGED FIELDS
    # =====================================================

    gold_df["client_id"] = None
    gold_df["amc_id"] = None

    # mapped from gold.scheme bridge

    gold_df["scheme_id"] = df["scheme_id"]

    gold_df["purchase_date"] = None


    gold_df["arn_id"] = None


    gold_df["avg_cost_nav"] = None


    gold_df["invested_amount"] = None


    gold_df["current_nav"] = None


    gold_df["current_value"] = None


    gold_df["nav_date"] = None


    gold_df["unrealised_gain"] = None


    gold_df["xirr"] = None


    gold_df["first_purchase_date"] = None


    gold_df["source_file_id"] = None



    # =====================================================
    # TIMESTAMP FIELDS
    # =====================================================


    current_time = datetime.now(
        timezone.utc
    )



    gold_df["last_synced_at"] = current_time



    gold_df["created_at"] = datetime.now()



    # =====================================================
    # FINAL GOLD HOLDINGS COLUMN ORDER
    # =====================================================


    gold_df = gold_df[

        [

            "id",

            "rta",

            "pan",

            "folio_number",

            "units",

            "market_value",

            "as_on_date",

            "folio_date",

            "arn",

            "holding_nature",

            "nominee_name",

            "nominee_relation",

            "nominee_pct",

            "kyc_status",

            "bank_name",

            "bank_ac_last4",

            "demat_flag",

            "client_id",

            "amc_id",

            "scheme_id",

            "purchase_date",

            "arn_id",

            "avg_cost_nav",

            "invested_amount",

            "current_nav",

            "current_value",

            "nav_date",

            "unrealised_gain",

            "xirr",

            "first_purchase_date",

            "source_file_id",

            "last_synced_at",

            "created_at"

        ]

    ]



    # =====================================================
    # FINAL VALIDATION
    # =====================================================


    print("=" * 80)

    print("GOLD HOLDINGS PREVIEW")

    print("=" * 80)



    print(gold_df.head())



    print(

        "Total Gold Holdings:",

        len(gold_df)

    )



    print(

        "Missing Scheme IDs:",

        gold_df["scheme_id"].isna().sum()

    )



    return gold_df

# =====================================================
# LOAD GOLD HOLDINGS DATA
# =====================================================


def load_holdings(gold_df):


    print("=" * 80)
    print("LOADING DATA INTO GOLD.HOLDINGS")
    print("=" * 80)



    # =====================================================
    # VARCHAR VALIDATION
    # =====================================================


    varchar_limits = {


        "rta": 10,

        "pan": 10,

        "folio_number": 40,

        "arn": 20,

        "holding_nature": 40,

        "nominee_name": 255,

        "nominee_relation": 40,

        "nominee_pct": 20,

        "kyc_status": 20,

        "bank_name": 120,

        "bank_ac_last4": 8,

        "demat_flag": 4

    }



    for col, limit in varchar_limits.items():


        if col in gold_df.columns:


            max_len = (

                gold_df[col]

                .fillna("")

                .astype(str)

                .str.len()

                .max()

            )


            print(

                f"{col:<25} Max Length : {max_len}"

            )


            if max_len > limit:

                raise ValueError(

                    f"{col} length {max_len} exceeds limit {limit}"

                )



    # =====================================================
    # TIMESTAMP DUPLICATION CHECK
    # =====================================================


    print()

    print("Checking existing gold holdings")



    existing_holdings = pd.read_sql(

        """

        SELECT


            rta,

            folio_number,

            scheme_id,

            last_synced_at


        FROM gold.holdings


        """,

        engine

    )



    print(

        "Existing holdings:",

        len(existing_holdings)

    )



    if len(existing_holdings) > 0:



        # normalize keys


        for col in [

            "rta",

            "folio_number"

        ]:


            existing_holdings[col] = (

                existing_holdings[col]

                .fillna("")

                .astype(str)

                .str.strip()

                .str.upper()

            )



            gold_df[col] = (

                gold_df[col]

                .fillna("")

                .astype(str)

                .str.strip()

                .str.upper()

            )



        # merge with existing


        compare_df = gold_df.merge(

            existing_holdings,

            on=[

                "rta",

                "folio_number",

                "scheme_id"

            ],

            how="left",

            suffixes=(

                "_new",

                "_old"

            )

        )



        # keep only new or latest synced rows


        compare_df = compare_df[

            compare_df["last_synced_at_old"].isna()

            |

            (

                compare_df["last_synced_at_new"]

                >

                compare_df["last_synced_at_old"]

            )

        ]



        gold_df = compare_df[

            gold_df.columns

        ]



    print(

        "Rows after duplicate check:",

        len(gold_df)

    )



    if len(gold_df) == 0:


        print(

            "No new holdings to insert"

        )


        return True



    # =====================================================
    # INSERT INTO GOLD
    # =====================================================


    try:


        gold_df.to_sql(

            name="holdings",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print()

        print(

            "Inserted Rows:",

            len(gold_df)

        )

        return True
    
    except Exception as e:
        print()
        print("ERROR WHILE LOADING GOLD HOLDINGS")
        print(type(e).__name__)
        print(e)

        return False

# =====================================================
# MAIN EXECUTION
# =====================================================


if __name__ == "__main__":


    print("\n")

    print("=" * 80)

    print("STARTING GOLD HOLDINGS ETL")

    print("=" * 80)



    try:


        # =================================================
        # EXTRACT
        # =================================================


        df = extract_holdings()



        # =================================================
        # TRANSFORM
        # =================================================


        gold_df = transform_holdings(

            df

        )



        # =================================================
        # LOAD
        # =================================================


        status = load_holdings(

            gold_df

        )



        if status:


            print("\n")

            print("=" * 80)

            print("GOLD HOLDINGS ETL COMPLETED SUCCESSFULLY")

            print("=" * 80)



        else:


            print("\n")

            print("=" * 80)

            print("GOLD HOLDINGS ETL FAILED")

            print("=" * 80)

    except Exception as e:

        print("\n")
        print("=" * 80)
        print("GOLD HOLDINGS ETL ERROR")
        print("=" * 80)
        print(type(e).__name__)
        print(e)
    