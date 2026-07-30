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
# GET LAST PROCESSED TIME FROM GOLD
# =====================================================

def get_last_processed_time():

    try:

        result = pd.read_sql(

            """
            SELECT
                MAX(created_at) AS last_time

            FROM gold.transactions

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
# NORMALIZE DATA FOR DUPLICATE CHECK
# =====================================================

def normalize_for_compare(df):


    df = df.copy()


    # remove audit columns

    df = df.drop(

        columns=[

            "created_at",

            "updated_at"

        ],

        errors="ignore"

    )


    for col in df.columns:


        if pd.api.types.is_datetime64_any_dtype(df[col]):


            df[col] = (

                pd.to_datetime(

                    df[col],

                    errors="coerce"

                )

                .dt.strftime(
                    "%Y-%m-%d"
                )

            )


        else:


            df[col] = (

                df[col]

                .astype("string")

                .str.strip()

            )


    return df



# =====================================================
# CREATE ROW KEY
# =====================================================

def create_row_key(df):


    df = normalize_for_compare(df)


    return (

        df.fillna("")

        .astype(str)

        .agg(

            "|".join,

            axis=1

        )

    )



# =====================================================
# GET GOLD TABLE COLUMNS
# =====================================================

def get_table_columns():


    query = """

    SELECT

        column_name

    FROM information_schema.columns

    WHERE table_schema='gold'

    AND table_name='transactions'

    ORDER BY ordinal_position

    """


    return pd.read_sql(

        query,

        engine

    )["column_name"].tolist()



# =====================================================
# EXTRACT GOLD TRANSACTIONS SOURCE
# =====================================================

def extract_transactions():


    print("=" * 80)
    print("Extracting Gold Transactions")
    print("=" * 80)



    last_time = get_last_processed_time()



    print(
        "Last Processed Time :",
        last_time
    )



    df = safe_read(

        """

        SELECT *

        FROM silver.transaction_master_new

        """

    )



    if df.empty:


        print(
            "No data found in Silver."
        )


        return df




    # =================================================
    # TIMESTAMP FILTER
    # =================================================


    df["created_at"] = pd.to_datetime(

        df["created_at"],

        errors="coerce"

    )



    last_time = pd.Timestamp(
        last_time
    )



    # remove timezone mismatch

    if getattr(df["created_at"].dt, "tz", None) is not None:


        df["created_at"] = (

            df["created_at"]

            .dt.tz_localize(None)

        )



    if last_time.tzinfo is not None:


        last_time = last_time.tz_localize(None)



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


    return df

# =====================================================
# CLASSIFY TRANSACTION TYPE
# =====================================================

def classify_transaction(row):


    desc = str(
        row.get(
            "trxn_nature",
            ""
        )
    ).lower()



    raw = str(
        row.get(
            "trxntype",
            ""
        )
    ).lower()



    text = desc + " " + raw



    if (
        raw in ["swi", "switch in"]
        or "switch in" in text
        or "switchin" in text
        or "swin" in text
    ):

        return "SWITCH_IN"



    if (
        raw in ["swo", "switch out"]
        or "switch out" in text
        or "switchout" in text
        or "swout" in text
    ):

        return "SWITCH_OUT"



    if (
        "redemption" in text
        or "redeem" in text
        or raw == "red"
    ):

        return "REDEMPTION"



    if (
        "sip" in text
        or "systematic" in text
    ):

        return "SIP"



    if "stp" in text:

        return "STP"



    if "dividend" in text:

        return "DIVIDEND"



    if (
        "transfer-in" in text
        or "transfer in" in text
    ):

        return "TRANSFER_IN"



    if (
        "transfer-out" in text
        or "transfer out" in text
    ):

        return "TRANSFER_OUT"



    if (
        "purchase" in text
        or "fresh purchase" in text
        or "additional purchase" in text
        or raw == "pur"
    ):

        return "PURCHASE"



    return "OTHER"





# =====================================================
# TRANSFORM GOLD TRANSACTIONS
# =====================================================

def transform_transactions(df):


    print("=" * 80)
    print("Transforming Gold Transactions")
    print("=" * 80)



    gold_df = pd.DataFrame()



    # =====================================================
    # BASIC DETAILS
    # =====================================================


    gold_df["rta"] = (

        df["source"]

        .astype("string")

        .str.strip()

        .str.upper()

        .str[:10]

    )



    gold_df["rta_txn_no"] = (

        df["trxnno"]

        .astype("string")

        .str.strip()

        .str.replace(
            ".0",
            "",
            regex=False
        )

        .replace(
            "",
            None
        )

        .str[:50]

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

        .replace(
            "",
            None
        )

        .str[:10]

    )



    gold_df["folio_number"] = (

        df["folio_no"]

        .astype("string")

        .str.strip()

        .str.replace(
            ".0",
            "",
            regex=False
        )

        .replace(
            "",
            None
        )

        .str[:40]

    )



    # =====================================================
    # TRANSACTION DETAILS
    # =====================================================


    gold_df["txn_type"] = (

        df.apply(

            classify_transaction,

            axis=1

        )

    )



    gold_df["txn_type_raw"] = (

        df["trxntype"]

        .astype("string")

        .str.strip()

        .str[:40]

    )



    gold_df["txn_desc"] = (

        df["trxn_nature"]

        .astype("string")

        .str.strip()

        .str[:120]

    )



    gold_df["txn_date"] = (

        pd.to_datetime(

            df["traddate"],

            errors="coerce"

        )

        .dt.date

    )



    gold_df["post_date"] = (

        pd.to_datetime(

            df["postdate"],

            errors="coerce"

        )

        .dt.date

    )



    # =====================================================
    # AMOUNT DETAILS
    # =====================================================


    gold_df["amount"] = pd.to_numeric(

        df["amount"],

        errors="coerce"

    )


    gold_df["units"] = pd.to_numeric(

        df["units"],

        errors="coerce"

    )


    gold_df["nav"] = pd.to_numeric(

        df["purprice"],

        errors="coerce"

    )


    gold_df["load_amount"] = pd.to_numeric(

        df["load"],

        errors="coerce"

    )


    gold_df["stt"] = pd.to_numeric(

        df["stt"],

        errors="coerce"

    )


    gold_df["stamp_duty"] = pd.to_numeric(

        df["stamp_duty"],

        errors="coerce"

    )



    gold_df["gst"] = (

        pd.to_numeric(
            df["igst_amount"],
            errors="coerce"
        ).fillna(0)

        +

        pd.to_numeric(
            df["cgst_amount"],
            errors="coerce"
        ).fillna(0)

        +

        pd.to_numeric(
            df["sgst_amount"],
            errors="coerce"
        ).fillna(0)

    )



    # =====================================================
    # OTHER DETAILS
    # =====================================================


    gold_df["arn"] = (

        df["brokcode"]

        .astype("string")

        .str.strip()

        .str[:20]

    )



    gold_df["euin"] = (

        df["euin"]

        .astype("string")

        .str.strip()

        .str[:20]

    )



    gold_df["sip_ref"] = (

        df["siptrxnno"]

        .astype("string")

        .str.strip()

        .str[:50]

    )



    gold_df["status"] = (

        df["trxnstat"]

        .astype("string")

        .str.strip()

        .str.upper()

        .str[:20]

    )

    # =====================================================
# SCHEME ID LOOKUP
# =====================================================

    print("=" * 80)
    print("Mapping Scheme ID")
    print("=" * 80)



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



    # =====================================================
    # CLEAN JOIN KEYS
    # =====================================================


    df["source"] = (

        df["source"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    df["prodcode"] = (

        df["prodcode"]

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



    # =====================================================
    # MERGE SCHEME ID
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



    gold_df["scheme_id"] = df["id"]



    # =====================================================
    # SCHEME VALIDATION
    # =====================================================


    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)



    print(

        "Total Transactions :",

        len(gold_df)

    )



    print(

        "Matched Scheme ID :",

        gold_df["scheme_id"].notna().sum()

    )



    print(

        "Missing Scheme ID :",

        gold_df["scheme_id"].isna().sum()

    )



    if gold_df["scheme_id"].isna().sum() > 0:


        print("\nMissing Scheme Samples")


        print(

            df.loc[

                gold_df["scheme_id"].isna(),

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
    # APPLICATION MANAGED COLUMNS
    # =====================================================


    gold_df["client_id"] = None

    gold_df["amc_id"] = None


    gold_df["txn_sub_type"] = None

    gold_df["rta_txn_id"] = None

    gold_df["arn_id"] = None

    gold_df["sip_id"] = None



    gold_df["source"] = df["source"]



    gold_df["source_file_id"] = None



    # =====================================================
    # TIMESTAMP LOGIC
    # SAME AS GOLD SIP
    # =====================================================


    gold_df["created_at"] = (

        df["created_at"]

    )


    gold_df["updated_at"] = None



    # =====================================================
    # FINAL COLUMN ORDER
    # =====================================================


    gold_df = gold_df[

        [

            "rta",

            "rta_txn_no",

            "pan",

            "folio_number",

            "txn_type",

            "txn_type_raw",

            "txn_desc",

            "txn_date",

            "post_date",

            "amount",

            "units",

            "nav",

            "load_amount",

            "stt",

            "stamp_duty",

            "gst",

            "arn",

            "euin",

            "sip_ref",

            "status",

            "client_id",

            "amc_id",

            "scheme_id",

            "txn_sub_type",

            "rta_txn_id",

            "arn_id",

            "sip_id",

            "source",

            "source_file_id",

            "created_at",

            "updated_at"

        ]

    ]



    print("=" * 80)
    print("Gold Transaction Preview")
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
# LOAD GOLD TRANSACTIONS
# =====================================================

def load_transactions(gold_df):


    print("=" * 80)
    print("Loading Gold Transactions")
    print("=" * 80)



    if gold_df.empty:


        print(
            "No new records."
        )


        return True



    # =====================================================
    # LOAD EXISTING GOLD DATA
    # =====================================================


    try:


        existing = pd.read_sql(

            """

            SELECT *

            FROM gold.transactions

            """,

            engine

        )


    except Exception:


        existing = pd.DataFrame()



    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================


    if not existing.empty:


        print(

            "Existing Gold Rows :",

            len(existing)

        )


        old_keys = set(

            create_row_key(

                existing

            )

        )



        new_keys = create_row_key(

            gold_df

        )



        gold_df = gold_df.loc[

            ~new_keys.isin(old_keys)

        ]



    if gold_df.empty:


        print(
            "Duplicate data skipped."
        )


        return True



    # =====================================================
    # GOLD AUDIT TIMESTAMP
    # SAME AS SIP
    # =====================================================


    load_time = pd.Timestamp.now()



    gold_df["created_at"] = load_time


    gold_df["updated_at"] = load_time



    # =====================================================
    # MATCH DATABASE COLUMNS
    # =====================================================


    db_cols = get_table_columns()



    for col in db_cols:


        if col not in gold_df.columns:


            gold_df[col] = None



    gold_df = gold_df[db_cols]



    # =====================================================
    # VARCHAR VALIDATION
    # =====================================================


    varchar_limits = {


        "rta": 10,

        "rta_txn_no": 50,

        "pan": 10,

        "folio_number": 40,

        "txn_type": 30,

        "txn_type_raw": 40,

        "txn_desc": 120,

        "arn": 20,

        "euin": 20,

        "sip_ref": 50,

        "status": 20,

        "txn_sub_type": 50


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



    # =====================================================
    # INSERT INTO GOLD
    # =====================================================


    try:


        gold_df.to_sql(

            "transactions",

            engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )


        print()

        print(

            f"{len(gold_df)} rows inserted into Gold."

        )


        return True



    except Exception as e:


        print(

            "\nFAILED LOADING GOLD TRANSACTIONS\n"

        )


        traceback.print_exc(limit=5)



        if hasattr(e, "orig"):


            print(e.orig)



        return False

# =====================================================
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":


    print()

    print("=" * 80)
    print("STARTING GOLD TRANSACTION ETL")
    print("=" * 80)



    # =================================================
    # EXTRACT
    # =================================================


    df = extract_transactions()



    if df.empty:


        print()

        print(
            "No new silver transaction data found."
        )


    else:



        # =================================================
        # TRANSFORM
        # =================================================


        gold_df = transform_transactions(

            df

        )



        # =================================================
        # LOAD
        # =================================================


        status = load_transactions(

            gold_df

        )



        # =================================================
        # FINAL STATUS
        # =================================================


        if status:


            print()

            print("=" * 80)

            print(
                "GOLD TRANSACTION ETL COMPLETED SUCCESSFULLY"
            )

            print("=" * 80)



        else:


            print()

            print("=" * 80)

            print(
                "GOLD TRANSACTION ETL FAILED"
            )

            print("=" * 80)