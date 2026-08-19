import pandas as pd
from datetime import datetime

from utils.db import engine



# =====================================================
# EXTRACT GOLD CLIENT DATA
# =====================================================

def extract_clients():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENTS")
    print("=" * 80)


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


    df = pd.read_sql(query, engine)


    print("\nExtraction Completed")
    print("-" * 80)

    print(f"Rows fetched    : {len(df)}")
    print(f"Columns fetched : {len(df.columns)}")


    return df

# =====================================================
# TRANSFORM GOLD CLIENT DATA
# =====================================================

def transform_clients(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENTS")
    print("=" * 80)


    df.columns = df.columns.str.lower()



    # =====================================================
    # CLEAN PAN
    # =====================================================


    def clean_pan(series):

        return (

            series.fillna("")

            .astype(str)

            .str.upper()

            .str.strip()

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



    df["pan_no"] = clean_pan(df["pan_no"])

    df["txn_pan"] = clean_pan(df["txn_pan"])

    df["sip_pan"] = clean_pan(df["sip_pan"])



    # =====================================================
    # PAN PRIORITY
    # Investor Master > Transaction > SIP
    # =====================================================


    df["pan"] = (

        df["pan_no"]

        .fillna(df["txn_pan"])

        .fillna(df["sip_pan"])

    )



    df.reset_index(drop=True, inplace=True)



    # =====================================================
    # CREATE GOLD DATAFRAME
    # =====================================================


    gold = pd.DataFrame()



    # =====================================================
    # REQUIRED FIELDS
    # =====================================================


    gold["status"] = "ACTIVE"


    gold["full_name"] = df["investor_name"]


    gold["pan"] = df["pan"]


    gold["pan_verified"] = False


    gold["pan_verified_at"] = None



    print("\nPAN Statistics")
    print("-" * 80)

    print("Investor PAN :", df["pan_no"].notna().sum())

    print("Transaction PAN :", df["txn_pan"].notna().sum())

    print("SIP PAN :", df["sip_pan"].notna().sum())

    print("Final PAN :", df["pan"].notna().sum())

    print("Missing PAN :", df["pan"].isna().sum())

        # =====================================================
    # APP MANAGED FIELDS
    # =====================================================


    gold["client_label"] = None

    gold["phone"] = None

    gold["mobile_isd"] = None

    gold["mobile"] = None


    gold["whatsapp_same_as_mobile"] = None

    gold["whatsapp_isd"] = None

    gold["whatsapp_no"] = None


    gold["aadhaar"] = None


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


    # source is app managed
    gold["source"] = None



    # =====================================================
    # TIMESTAMP
    # =====================================================


    gold["created_at"] = datetime.now()



    # =====================================================
    # ROW COUNT VALIDATION
    # =====================================================


    print("\nTransformation Completed")
    print("-" * 80)

    print(f"Silver rows : {len(df)}")
    print(f"Gold rows   : {len(gold)}")


    if len(df) != len(gold):

        raise Exception(
            "Row count mismatch. Data loss detected between Silver and Gold"
        )



    # =====================================================
    # FINAL GOLD CLIENT COLUMN ORDER
    # =====================================================


    gold = gold[

        [

            "status",

            "full_name",

            "client_label",

            "phone",

            "mobile_isd",

            "mobile",

            "whatsapp_same_as_mobile",

            "whatsapp_isd",

            "whatsapp_no",

            "aadhaar",

            "pan",

            "pan_verified",

            "pan_verified_at",

            "email",

            "date_of_birth",

            "marital_status",

            "anniversary_date",

            "blood_group",

            "equity_ucc",

            "can",

            "occupation",

            "user_id",

            "family_id",

            "family_relation",

            "gender",

            "investor_type",

            "tax_status",

            "kyc_status",

            "risk_profile",

            "rm_id",

            "branch_id",

            "arn_id",

            "onboarded_at",

            "source",

            "created_at"

        ]

    ]



    print("\nGold Clients Preview")
    print("-" * 80)

    print(gold.head())


    return gold

# =====================================================
# LOAD GOLD CLIENT DATA
# =====================================================

def load_clients(gold_df):

    print("=" * 80)
    print("LOADING GOLD CLIENTS")
    print("=" * 80)



    # =====================================================
    # DUPLICATE CHECK
    # =====================================================


    print("\nChecking existing gold clients")


    existing_clients = pd.read_sql(

        """

        SELECT

            pan

        FROM gold.clients

        """,

        engine

    )



    print(
        "Existing clients:",
        len(existing_clients)
    )



    if len(existing_clients) > 0:


        existing_clients["pan"] = (

            existing_clients["pan"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )


        gold_df["pan"] = (

            gold_df["pan"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



        # pan is the natural key, so a client already in gold is skipped rather
        # than inserted again.
        #
        # created_at is deliberately not part of this test. The merge this
        # replaces renamed the frame's own created_at to created_at_new, so the
        # reprojection onto gold_df.columns then asked for a created_at that no
        # longer existed and raised "['created_at'] not in index". Because the
        # block only runs when gold.clients already holds rows, the stage
        # succeeded once and has failed on every run since.
        #
        # The comparison it made was wrong in its own right: created_at_new is
        # stamped with datetime.now() in transform_clients, so it is always
        # greater than the stored created_at, every existing client passed the
        # filter, and the append-only to_sql below would have inserted the whole
        # table again on each run.

        existing_keys = set(

            existing_clients["pan"]

        )


        gold_df = gold_df.loc[

            ~gold_df["pan"].isin(existing_keys)

        ]



    print(

        "Rows after duplicate check:",

        len(gold_df)

    )



    if len(gold_df) == 0:


        print(

            "No new clients to insert"

        )


        return True



    # =====================================================
    # LOAD TO GOLD
    # =====================================================


    try:


        gold_df.to_sql(

            "clients",

            engine,

            schema="gold",

            if_exists="append",

            index=False,

            chunksize=1000

        )



        print("\nLoad Completed")

        print("-" * 80)

        print(

            f"Rows inserted : {len(gold_df)}"

        )



        return True



    except Exception as e:


        print()

        print(

            "ERROR WHILE LOADING GOLD CLIENTS"

        )


        print(

            type(e).__name__

        )


        print(e)

        return False

# =====================================================
# MAIN EXECUTION
# =====================================================


if __name__ == "__main__":


    print("\n")

    print("=" * 80)

    print("STARTING GOLD CLIENTS ETL")

    print("=" * 80)



    try:


        # =================================================
        # EXTRACT
        # =================================================


        clients_df = extract_clients()



        # =================================================
        # TRANSFORM
        # =================================================


        gold_clients = transform_clients(

            clients_df

        )



        # =================================================
        # LOAD
        # =================================================


        status = load_clients(

            gold_clients

        )



        if status:


            print("\n")

            print("=" * 80)

            print("GOLD CLIENTS ETL COMPLETED SUCCESSFULLY")

            print("=" * 80)



        else:


            print("\n")

            print("=" * 80)

            print("GOLD CLIENTS ETL FAILED")

            print("=" * 80)




    except Exception as e:


        print("\n")

        print("=" * 80)

        print("GOLD CLIENTS ETL ERROR")

        print("=" * 80)


        print(

            type(e).__name__

        )


        print(e)