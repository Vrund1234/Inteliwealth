import pandas as pd
import traceback
import uuid

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

            FROM gold.holdings

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
# EXTRACT GOLD HOLDINGS DATA
# =====================================================

def extract_holdings():


    print("=" * 80)
    print("Extracting Silver Data For Gold Holdings")
    print("=" * 80)



    last_time = get_last_processed_time()



    print(
        "Last Processed Time :",
        last_time
    )



    query = """

    SELECT

        t.*,

        i.holding_nature AS investor_holding_nature,

        i.nominee1_name AS investor_nominee_name,

        i.nominee1_relation AS investor_nominee_relation,

        i.nominee1_percentage AS investor_nominee_percentage,

        i.bank_name AS investor_bank_name,

        i.bank_account_no AS investor_bank_account_no,

        i.demat_flag AS investor_demat_flag,

        i.ckyc_no AS investor_ckyc_no,

        i.broker_code AS investor_broker_code


    FROM silver.transaction_master_new t



    LEFT JOIN

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

            folio_no,

            created_at DESC


    ) i


    ON t.source = i.source

    AND t.folio_no = i.folio_no



    """



    df = safe_read(query)



    if df.empty:


        print(
            "No data found in Silver"
        )


        return df




    # =====================================================
    # TIMESTAMP FILTER
    # =====================================================


    df["created_at"] = pd.to_datetime(

        df["created_at"],

        errors="coerce"

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
        "Columns fetched :",
        len(df.columns)
    )



    if not df.empty:

        print(df.head())



    return df

# =====================================================
# TRANSFORM GOLD HOLDINGS
# =====================================================

def transform_holdings(df):


    print("=" * 80)
    print("Transforming Gold Holdings")
    print("=" * 80)



    if df.empty:

        print(
            "No data available for transformation"
        )

        return pd.DataFrame()



    df = df.copy()



    gold_df = pd.DataFrame()



    # =====================================================
    # GENERATE ID
    # =====================================================


    gold_df["id"] = [

        uuid.uuid4()

        for _ in range(len(df))

    ]



    # =====================================================
    # CLEAN PROD CODE
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


    print(

        "Gold Scheme Rows :",

        len(gold_scheme)

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



    print(

        "Total Rows :",

        len(df)

    )



    print(

        "Matched scheme_id :",

        df["scheme_id"].notna().sum()

    )



    print(

        "Missing scheme_id :",

        df["scheme_id"].isna().sum()

    )



    if df["scheme_id"].isna().sum() > 0:


        print("\nMissing Scheme Samples")


        print(

            df.loc[

                df["scheme_id"].isna(),

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



    # =====================================================
    # BASIC DETAILS
    # =====================================================


    gold_df["rta"] = (

        df["source"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    gold_df["pan"] = (

        df["pan"]

        .astype("string")

        .str.strip()

        .str.upper()

        .str.replace(

            ".0",

            "",

            regex=False

        )

    )



    gold_df.loc[

        gold_df["pan"].str.len() != 10,

        "pan"

    ] = None



    # =====================================================
    # FOLIO NUMBER
    # =====================================================


    gold_df["folio_number"] = (

        df["folio_no"]

        .astype("string")

        .str.strip()

        .str.replace(

            ".0",

            "",

            regex=False

        )

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
    # DATE FIELDS
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
    # ARN DETAILS
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



    kyc_check = (

        df["investor_ckyc_no"]

        .fillna("")

        .astype(str)

        .str.strip()

        != ""

    )



    gold_df.loc[

        kyc_check,

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

        .str.replace(

            ".0",

            "",

            regex=False

        )

        .str.strip()

        .str[-4:]

    )



    gold_df.loc[

        gold_df["bank_ac_last4"] == "",

        "bank_ac_last4"

    ] = None



    # =====================================================
    # DEMAT DETAILS
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

    # mapped scheme id from gold.scheme

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
    # AUDIT COLUMNS
    # Same timestamp logic as SIP / Scheme
    # =====================================================

    gold_df["created_at"] = df["created_at"]

    gold_df["updated_at"] = None

    # =====================================================
    # CLEAN EMPTY VALUES
    # =====================================================


    text_columns = [
        "rta",
        "pan",
        "folio_number",
        "arn",
        "holding_nature",
        "nominee_name",
        "nominee_relation",
        "nominee_pct",
        "bank_name",
        "bank_ac_last4",
        "demat_flag"
    ]

    for col in text_columns:

        gold_df[col] = (

            gold_df[col]

            .replace(
                "",
                None
            )
        )

    # =====================================================
    # FINAL COLUMN ORDER
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
            "created_at",
            "updated_at"
        ]
    ]

    # =====================================================
    # PREVIEW
    # =====================================================

    print("=" * 80)
    print("GOLD HOLDINGS PREVIEW")
    print("=" * 80)

    print(
        gold_df.head()
    )

    print()

    print(
        "Rows Ready :",
        len(gold_df)
    )

    return gold_df

# =====================================================
# LOAD GOLD HOLDINGS
# =====================================================

def load_holdings(gold_df):


    print("=" * 80)
    print("Loading data into gold.holdings")
    print("=" * 80)


    if gold_df.empty:

        print(
            "No new records to load"
        )

        return True



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
            f"{len(gold_df)} rows inserted into gold.holdings"
        )


        return True



    except Exception:


        print(
            "ERROR WHILE LOADING GOLD HOLDINGS"
        )

        traceback.print_exc()

        return False

# =====================================================
# MAIN
# =====================================================

def main():


    print("=" * 80)
    print("STARTING GOLD HOLDINGS ETL")
    print("=" * 80)



    silver_df = extract_holdings()



    if silver_df.empty:

        print(
            "No new records found"
        )

        return



    gold_df = transform_holdings(
        silver_df
    )



    load_holdings(
        gold_df
    )



    print("=" * 80)
    print("GOLD HOLDINGS ETL COMPLETED")
    print("=" * 80)



if __name__ == "__main__":

    main()