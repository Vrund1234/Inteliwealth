import pandas as pd
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
# SAME LOGIC AS GOLD SIP
# =====================================================

def get_last_processed_time():

    try:

        df = pd.read_sql(

            """
            SELECT

                MAX(created_at) AS last_time

            FROM gold.clients

            """,

            engine

        )


        last_time = df.iloc[0]["last_time"]


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
# EXTRACT GOLD CLIENT DATA
# =====================================================

def extract_clients():


    print("=" * 80)
    print("Extracting Silver Data For Gold Clients")
    print("=" * 80)



    last_time = get_last_processed_time()



    print(
        "Last Processed Time :",
        last_time
    )



    query = """

    SELECT

        i.*,

        t.pan AS txn_pan,

        s.pan AS sip_pan


    FROM silver.investor_master i



    LEFT JOIN

    (

        SELECT

            folio_no,

            MAX(pan) AS pan


        FROM silver.transaction_master_new


        WHERE pan IS NOT NULL

        AND TRIM(pan) <> ''


        GROUP BY folio_no


    ) t


    ON i.folio_no = t.folio_no





    LEFT JOIN

    (

        SELECT

            folio_no,

            MAX(pan) AS pan


        FROM silver.sip_master_new


        WHERE pan IS NOT NULL

        AND TRIM(pan) <> ''


        GROUP BY folio_no


    ) s


    ON i.folio_no = s.folio_no



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

        print(
            df.head()
        )


    return df

# =====================================================
# TRANSFORM GOLD CLIENTS
# =====================================================

def transform_clients(df):


    print("=" * 80)
    print("Transforming Gold Clients")
    print("=" * 80)



    if df.empty:

        print(
            "No data available for transformation"
        )

        return pd.DataFrame()



    df = df.copy()



    # =====================================================
    # CLEAN COLUMN NAMES
    # =====================================================

    df.columns = (

        df.columns

        .str.lower()

        .str.strip()

    )



    # =====================================================
    # PAN CLEANING FUNCTION
    # =====================================================


    def clean_pan(series):

        return (

            series

            .fillna("")

            .astype(str)

            .str.upper()

            .str.strip()

            .str.replace(
                ".0",
                "",
                regex=False
            )

            .replace(

                [

                    "",

                    "NAN",

                    "NONE",

                    "NULL",

                    "NON RESIDENT"

                ],

                pd.NA

            )

            .str[:10]

        )



    # =====================================================
    # CLEAN ALL PAN SOURCES
    # =====================================================


    df["pan_no"] = clean_pan(

        df["pan_no"]

    )


    df["txn_pan"] = clean_pan(

        df["txn_pan"]

    )


    df["sip_pan"] = clean_pan(

        df["sip_pan"]

    )



    # =====================================================
    # FINAL PAN PRIORITY
    #
    # Investor Master
    #        |
    # Transaction
    #        |
    # SIP
    #
    # =====================================================


    df["pan"] = (

        df["pan_no"]

        .fillna(

            df["txn_pan"]

        )

        .fillna(

            df["sip_pan"]

        )

    )



    # =====================================================
    # CREATE GOLD DATAFRAME
    # =====================================================


    gold = pd.DataFrame()



    # =====================================================
    # BASIC CLIENT DETAILS
    # =====================================================


    gold["status"] = None



    gold["full_name"] = (

        df["investor_name"]

        .astype("string")

        .str.strip()

    )



    gold["pan"] = df["pan"]



    # =====================================================
    # APPLICATION MANAGED FIELDS
    # =====================================================


    gold["client_label"] = None


    gold["phone"] = None


    gold["mobile_isd"] = None


    gold["mobile"] = None


    gold["whatsapp_same_as_mobile"] = None


    gold["whatsapp_isd"] = None


    gold["whatsapp_no"] = None


    gold["aadhaar"] = None


    gold["pan_verified_at"] = None


    gold["email"] = None


    gold["date_of_birth"] = None


    gold["marital_status"] = None


    gold["anniversary_date"] = None


    gold["blood_group"] = None


    gold["equity_ucc"] = None


    gold["can"] = None


    gold["occupation"] = None


    gold["user_id"] = None


    gold["family_id"] = None


    gold["family_relation"] = None


    gold["gender"] = None


    gold["investor_type"] = None


    gold["tax_status"] = None


    gold["kyc_status"] = None


    gold["risk_profile"] = None


    gold["rm_id"] = None


    gold["branch_id"] = None


    gold["arn_id"] = None


    gold["onboarded_at"] = None



    # =====================================================
    # SOURCE
    # =====================================================


    gold["source"] = (

        df["source"]

        .astype("string")

        .str.upper()

        .str.strip()

    )



    # =====================================================
    # TIMESTAMP
    #
    # SAME AS GOLD SIP
    #
    # Preserve silver created_at
    #
    # =====================================================


    gold["created_at"] = (

        df["created_at"]

    )


    gold["updated_at"] = None



    # =====================================================
    # VALIDATION
    # =====================================================


    print()

    print(
        "PAN Statistics"
    )

    print("-" * 80)



    print(

        "Investor PAN :",

        df["pan_no"].notna().sum()

    )


    print(

        "Transaction PAN :",

        df["txn_pan"].notna().sum()

    )


    print(

        "SIP PAN :",

        df["sip_pan"].notna().sum()

    )


    print(

        "Final PAN :",

        df["pan"].notna().sum()

    )


    print(

        "Missing PAN :",

        df["pan"].isna().sum()

    )

    # =====================================================
    # CLEAN EMPTY VALUES
    # =====================================================

    gold = gold.replace(
        "",
        None
    )

    # =====================================================
    # ROW COUNT CHECK
    # =====================================================

    print()

    print("Row Count Validation")
    print("-" * 80)

    print(
        "Silver Rows :",
        len(df)
    )

    print("Gold Rows :",len(gold))

    if len(df) != len(gold):
        raise Exception(
            "Row count mismatch between Silver and Gold"
        )

    print()
    print("Transformation Completed")

    print("Rows Ready For Gold :",len(gold))

    return gold

# =====================================================
# LOAD GOLD CLIENTS
# =====================================================

def load_clients(df):


    print("=" * 80)
    print("Loading Data Into Gold Clients")
    print("=" * 80)



    if df.empty:


        print(
            "No new records to load"
        )

        return True




    try:


        df.to_sql(

            name="clients",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=1000

        )



        print()

        print(
            "Inserted Rows :",
            len(df)
        )



        return True



    except Exception:


        print()

        print(
            "ERROR WHILE LOADING GOLD CLIENTS"
        )


        traceback.print_exc()


        return False





# =====================================================
# MAIN ETL
# =====================================================

def main():


    print("=" * 80)
    print("STARTING GOLD CLIENTS ETL")
    print("=" * 80)



    silver_df = extract_clients()



    if silver_df.empty:


        print()

        print(
            "No new records found"
        )
        return

    gold_df = transform_clients(
        silver_df
    )

    status = load_clients(
        gold_df
    )

    if status:

        print()
        print("=" * 80)
        print(
            "GOLD CLIENTS ETL COMPLETED SUCCESSFULLY"
        )
        print("=" * 80)

    else:

        print()
        print("=" * 80)
        print(
            "GOLD CLIENTS ETL FAILED"
        )

        print("=" * 80)

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    main()