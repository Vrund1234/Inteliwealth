import pandas as pd
import traceback
from datetime import datetime, timezone
from utils.db import engine
from sqlalchemy import create_engine



# =====================================================
# DATABASE CONNECTION
# =====================================================

# engine = create_engine(
#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
# )



# =====================================================
# EXTRACT SIP DATA
# =====================================================

def extract_sip():

    print("=" * 80)
    print("Extracting data for Gold SIP")
    print("=" * 80)

    # -------------------------------------------------
    # Last loaded timestamp from Gold
    # -------------------------------------------------

    last_time = pd.read_sql(
        """
        SELECT COALESCE(
            MAX(created_at),
            TIMESTAMP '1900-01-01'
        ) AS last_time
        FROM gold.sip
        """,
        engine
    ).iloc[0]["last_time"]

    last_time = pd.Timestamp(last_time)

    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")


    query = """
    SELECT
        *
    FROM silver.sip_master_new
    """

    df = pd.read_sql(
        query,
        engine
    )

    # -------------------------------------------------
    # Load only new records
    # -------------------------------------------------

    if not df.empty:

        df["created_at"] = pd.to_datetime(
            df["created_at"],
            utc=True
        )

        df = df[
            df["created_at"] > last_time
        ]

    print()
    print("Extraction Completed")
    print("-" * 80)

    print(
        f"Rows fetched : {len(df)}"
    )

    print(
        f"Columns fetched : {len(df.columns)}"
    )



    print()
    print("Sample Data")
    print("-" * 80)

    print(
        df.head()
    )
    return df

# =====================================================
# TRANSFORM GOLD SIP
# =====================================================

def transform_sip(df):

    print("=" * 80)
    print("Transforming data for Gold SIP")
    print("=" * 80)



    gold_df = pd.DataFrame()



    # =================================================
    # RTA
    # CAMS / KFIN
    # =================================================

    gold_df["rta"] = (

        df["source"]
        .astype("string")
        .str.strip()
        .str.upper()

    )



    # =================================================
    # SIP REGISTRATION NUMBER
    # Dedup Key
    # =================================================

    gold_df["sip_reg_no"] = (
        df["ft_sip_regno"]
        .combine_first(df["request_ref_no"])
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    gold_df["sip_reg_no"] = (
        gold_df["sip_reg_no"]
        .replace({"": pd.NA, "nan": pd.NA, "<NA>": pd.NA})
    )
    # =================================================
    # FOLIO NUMBER
    # =================================================

    gold_df["folio_number"] = (

        df["folio_no"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)

    )



    gold_df.loc[
        gold_df["folio_number"] == "",
        "folio_number"
    ] = None




    # =================================================
    # SCHEME CODE
    # =================================================

    gold_df["scheme_code"] = (

        df["scheme_code"]
        .astype("string")
        .str.strip()

    )

    # =================================================
    # SCHEME NAME
    # =================================================

    gold_df["scheme_name"] = (

        df["scheme_name"]
        .astype("string")
        .str.strip()

    )




    # =================================================
    # AMC CODE
    # =================================================

    gold_df["amc_code"] = (

        df["amc_code"]
        .astype("string")
        .str.strip()

    )

    # =================================================
    # SIP AMOUNT
    # =================================================

    gold_df["amount"] = pd.to_numeric(

        df["auto_amount"],

        errors="coerce"

    )




    # =================================================
    # FREQUENCY
    # =================================================

    gold_df["frequency"] = (

        df["periodicity"]
        .astype("string")
        .str.strip()
        .str.upper()

    )




    # =================================================
    # DATES
    # =================================================

    gold_df["start_date"] = (

        pd.to_datetime(
            df["from_date"],
            errors="coerce"
        )
        .dt.date

    )


    gold_df["end_date"] = (

        pd.to_datetime(
            df["to_date"],
            errors="coerce"
        )
        .dt.date

    )

    # =================================================
    # SIP DAY
    # =================================================

    gold_df["sip_day"] = pd.to_numeric(

        df["period_day"],

        errors="coerce"

    )




    # =================================================
    # MANDATE ID
    # =================================================

    gold_df["mandate_id"] = (

        df["umrn_code"]
        .astype("string")
        .str.strip()

    )




    # =================================================
    # STATUS
    # =================================================

    gold_df["status"] = (

        df["status"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str[:20]

    )




    # =================================================
    # REGISTERED DATE
    # =================================================

    gold_df["registered_date"] = (

        pd.to_datetime(
            df["reg_date"],
            errors="coerce"
        )
        .dt.date

    )

    gold_df["next_due_date"] = None

    # =================================================
    # CEASED DATE
    # =================================================

    gold_df["ceased_date"] = (

        pd.to_datetime(
            df["cease_date"],
            errors="coerce"
        )
        .dt.date

    )

    print(df.columns.tolist())

    gold_df["isin"] = None

    # =================================================
    # APPLICATION MANAGED COLUMNS
    # =================================================


    gold_df["scheme_id"] = None

    gold_df["amc_id"] = None

    gold_df["client_id"] = None


    gold_df["sip_type"] = None


    gold_df["registered_installments"] = None

    gold_df["completed_installments"] = None

    gold_df["bounced_installments"] = None


    gold_df["ceased_reason"] = None


    gold_df["arn_id"] = None




    # =================================================
    # COLUMN ORDER
    # =================================================

    gold_df = gold_df[
        [

            "rta",

            "sip_reg_no",

            "folio_number",

            "scheme_code",

            "scheme_name",

            "amc_code",

            "isin",

            "amount",

            "frequency",

            "start_date",

            "end_date",

            "next_due_date",

            "sip_day",

            "mandate_id",

            "status",

            "registered_date",

            "ceased_date",

            "scheme_id",

            "amc_id",

            "client_id",

            "sip_type",

            "registered_installments",

            "completed_installments",

            "bounced_installments",

            "ceased_reason",

            "arn_id"

        ]
    ]



    print("=" * 80)
    print("Gold SIP Preview")
    print("=" * 80)


    print(
        gold_df.head()
    )


    print(
        "\nTotal Gold SIP Rows:",
        len(gold_df)
    )

    gold_df["rta"] = (
        gold_df["rta"]
        .astype("string")
        .str[:10]
    )

    gold_df["sip_reg_no"] = gold_df["sip_reg_no"].where(
        gold_df["sip_reg_no"].isna(),
        gold_df["sip_reg_no"].astype(str).str[:50]
    )

    gold_df["folio_number"] = gold_df["folio_number"].where(
        gold_df["folio_number"].isna(),
        gold_df["folio_number"].astype(str).str[:40]
    )

    gold_df["scheme_code"] = (
        gold_df["scheme_code"]
        .astype("string")
        .str[:30]
    )

    gold_df["scheme_name"] = (
        gold_df["scheme_name"]
        .astype("string")
        .str[:255]
    )

    gold_df["amc_code"] = (
        gold_df["amc_code"]
        .astype("string")
        .str[:20]
    )

    gold_df["isin"] = (
        gold_df["isin"]
        .astype("string")
        .str[:20]
    )

    gold_df["frequency"] = (
        gold_df["frequency"]
        .astype("string")
        .str[:20]
    )

    gold_df["mandate_id"] = (
        gold_df["mandate_id"]
        .astype("string")
        .str[:50]
    )

    gold_df["status"] = (
        gold_df["status"]
        .astype("string")
        .str[:20]
    )

    gold_df["sip_type"] = (
        gold_df["sip_type"]
        .astype("string")
        .str[:20]
    )

    gold_df["ceased_reason"] = (
        gold_df["ceased_reason"]
        .astype("string")
        .str[:100]
    )

    # Preserve timestamp till load
    if "created_at" in df.columns:
        gold_df["created_at"] = df["created_at"].values

    return gold_df

# =====================================================
# DUPLICATE CHECK
# Gold SIP Dedup Key:
# rta + sip_reg_no
# =====================================================

def check_duplicates(gold_df):

    print("=" * 80)
    print("Checking duplicate SIP records")
    print("=" * 80)


    duplicates = (
        gold_df[gold_df["sip_reg_no"].notna()]
        .groupby(["rta","sip_reg_no"])
        .size()
        .reset_index(name="count")
        .query("count > 1")
    )


    print(
        "Duplicate RTA + SIP Registration records:",
        len(duplicates)
    )


    if len(duplicates) > 0:

        print()

        print(
            duplicates.head(20)
        )


    return duplicates




# =====================================================
# LOAD GOLD SIP
# =====================================================

def load_sip(gold_df):


    print("=" * 80)
    print("Loading data into gold.sip")
    print("=" * 80)



    # ---------------------------------------------
    # Validate varchar lengths
    # ---------------------------------------------

    varchar_limits = {


        "rta":10,

        "sip_reg_no":50,

        "folio_number":40,

        "scheme_code":30,

        "scheme_name":255,

        "amc_code":20,

        "isin":20,

        "frequency":20,

        "mandate_id":50,

        "status":20,

        "sip_type":20,

        "ceased_reason":100

    }



    for col, limit in varchar_limits.items():


        if col in gold_df.columns:


            max_length = (

                gold_df[col]
                .fillna("")
                .astype(str)
                .str.len()
                .max()

            )


            print(
                f"{col:<25} Max Length : {max_length}"
            )



            if max_length > limit:


                raise Exception(

                    f"{col} length {max_length} exceeds limit {limit}"

                )

    # ---------------------------------------------
    # Insert into gold.sip
    # ---------------------------------------------


    try:
        # Remove helper column before insert
        gold_df = gold_df.drop(
            columns=["created_at"],
            errors="ignore"
)

        gold_df.to_sql(

            name="sip",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print()

        print(
            f"Inserted Rows : {len(gold_df)}"
        )


        return True




    except Exception as e:

        print("\nFAILED LOADING GOLD SIP\n")

        traceback.print_exc(limit=5)

        if hasattr(e, "orig"):
            print("\n========== POSTGRES ERROR ==========")
            print(e.orig)
            print("====================================")

        return False

# =====================================================
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":


    print()
    print("=" * 80)
    print("STARTING GOLD SIP ETL")
    print("=" * 80)



    # -------------------------------------------------
    # EXTRACT
    # -------------------------------------------------

    df = extract_sip()



    # -------------------------------------------------
    # TRANSFORM
    # -------------------------------------------------

    gold_df = transform_sip(df)



    # -------------------------------------------------
    # DUPLICATE CHECK
    # -------------------------------------------------

    duplicates = check_duplicates(
        gold_df
    )



    # -------------------------------------------------
    # LOAD
    # -------------------------------------------------

    status = load_sip(
        gold_df
    )



    # -------------------------------------------------
    # FINAL STATUS
    # -------------------------------------------------

    if status:


        print()

        print("=" * 80)

        print(
            "GOLD SIP ETL COMPLETED SUCCESSFULLY"
        )

        print("=" * 80)



    else:


        print()

        print(
            "GOLD SIP ETL FAILED"
        )