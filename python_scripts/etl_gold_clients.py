import pandas as pd
from utils.db import engine
# from sqlalchemy import create_engine


# # =====================================================
# # DATABASE CONNECTION
# # =====================================================

# engine = create_engine(
#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
# )


# =====================================================
# EXTRACT
# =====================================================

def extract_clients():

    print("=" * 80)
    print("STARTING GOLD CLIENTS ETL")
    print("=" * 80)


    query = """
    SELECT
        i.*,
        t.pan AS txn_pan,
        s.pan AS sip_pan
    FROM silver.investor_master i

    LEFT JOIN (
        SELECT
            folio_no,
            MAX(pan) AS pan
        FROM silver.transaction_master_new
        WHERE pan IS NOT NULL
        AND TRIM(pan) <> ''
        GROUP BY folio_no
    ) t
    ON i.folio_no = t.folio_no

    LEFT JOIN (
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
# TRANSFORM
# =====================================================

def transform_clients(df):

    print("=" * 80)
    print("Transforming Gold Clients")
    print("=" * 80)


    # Normalize column names

    df.columns = df.columns.str.lower()


    # -------------------------------
    # Clean PAN
    # -------------------------------

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

    # Clean PAN from Investor Master
    df["pan_no"] = clean_pan(df["pan_no"])

    # Clean PAN from Transaction Master
    df["txn_pan"] = clean_pan(df["txn_pan"])
    df["sip_pan"] = clean_pan(df["sip_pan"])

    # Use Investor PAN first; if missing, use Transaction PAN
    df["pan"] = (
        df["pan_no"]
        .fillna(df["txn_pan"])
        .fillna(df["sip_pan"])
    )

    df.reset_index(drop=True, inplace=True)



    # -------------------------------
    # Create Gold dataframe
    # -------------------------------

    gold = pd.DataFrame()



    # Required fields

    gold["status"] = None

    gold["full_name"] = df["investor_name"]

    gold["pan"] = df["pan"]



    # -------------------------------
    # App managed fields
    # -------------------------------

    gold["client_label"] = None

    gold["phone"] = None

    gold["mobile_isd"] = None

    gold["mobile"] = None


    gold["whatsapp_same_as_mobile"] = None

    gold["whatsapp_isd"] = None

    gold["whatsapp_no"] = None


    gold["aadhaar"] = None



    # pan_verified has database default FALSE
    # so not adding it here


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


    gold["source"] = None


    print("\nPAN Statistics")
    print("-" * 80)
    print("Investor PAN :", df["pan_no"].notna().sum())
    print("Transaction PAN :", df["txn_pan"].notna().sum())
    print("SIP PAN         :", df["sip_pan"].notna().sum())
    print("Final PAN :", df["pan"].notna().sum())
    print("Missing PAN :", df["pan"].isna().sum())

    print("\nTransformation Completed")
    print("-" * 80)

    print(f"Rows ready for Gold : {len(gold)}")
    # Check varchar(10) fields

    print("\nChecking length issues")
    print("\nRow Count Validation")
    print("-" * 80)

    print(f"Silver rows : {len(df)}")
    print(f"Gold rows   : {len(gold)}")


    if len(df) != len(gold):
        raise Exception(
            "Row count mismatch. Data loss detected between Silver and Gold"
        )

    return gold



# =====================================================
# LOAD
# =====================================================

def load_clients(df):

    print("=" * 80)
    print("Loading Gold Clients")
    print("=" * 80)

    df.to_sql(
        "clients",
        engine,
        schema="gold",
        if_exists="append",
        index=False,
        chunksize=1000
    )

    print("\nLoad Completed")
    print("-" * 80)
    print(f"Rows inserted : {len(df)}")



# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":


    clients_df = extract_clients()


    gold_clients = transform_clients(
        clients_df
    )


    print("\nGold Clients Preview")
    print("-" * 80)

    print(
        gold_clients.head()
    )


    load_clients(
        gold_clients
    )