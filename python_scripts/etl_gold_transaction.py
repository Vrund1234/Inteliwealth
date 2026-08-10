import pandas as pd
import traceback

from utils.db import engine
from utils.db import master_engine


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
            FROM gold.transactions
            """,
            engine
        )

        last_time = result.iloc[0]["last_time"]

        if pd.isna(last_time):

            return pd.Timestamp("1900-01-01")

        return pd.to_datetime(last_time)


    except Exception:

        return pd.Timestamp("1900-01-01")



# =====================================================
# NORMALIZE DATA FOR DUPLICATE CHECK
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=[
            "created_at"
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
                .dt.strftime("%Y-%m-%d")
            )

        else:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
            )


    return df



# =====================================================
# CREATE NATURAL KEY
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)

    return (

        df[
            [
                "rta",
                "rta_txn_no"
            ]
        ]
        .fillna("")
        .astype(str)
        .agg("|".join, axis=1)

    )



# =====================================================
# EXTRACT SILVER TRANSACTIONS
# =====================================================

def extract_transactions():

    print("=" * 80)
    print("Extracting Silver Transactions")
    print("=" * 80)


    last_time = get_last_processed_time()


    df = safe_read(
        """
        SELECT
        source,
        folio_no,
        prodcode,
        trxntype,
        trxnno,
        trxnstat,
        trxnsubtyp,
        traddate,
        postdate,
        purprice,
        units,
        amount,
        brokcode,
        trxn_nature,
        load,
        pan,
        stt,
        siptrxnno,
        euin,
        igst_amount,
        cgst_amount,
        sgst_amount,
        stamp_duty,
        td_purred,
        created_at
        FROM silver.transaction_master_new
        """
    )


    if df.empty:

        print("No data found.")

        return df



    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )


    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )



    if getattr(last_time, "tzinfo", None) is not None:

        last_time = last_time.tz_localize(None)



    # =====================================================
    # INCREMENTAL LOAD
    # =====================================================

    df = df[
        df["created_at"] > last_time
    ]


    print(
        "Rows fetched :",
        len(df)
    )


    return df




# =====================================================
# TRANSACTION CLASSIFICATION
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


    purred = str(
        row.get(
            "td_purred",
            ""
        )
    ).lower()



    text = (
        desc
        + " "
        + raw
        + " "
        + purred
    )



    if (
        raw in [
            "swi",
            "swin"
        ]
        or "switch in" in text
        or "switchin" in text
    ):

        return "SWITCH_IN"



    if (
        raw in [
            "swo",
            "swout"
        ]
        or "switch out" in text
        or "switchout" in text
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
        "transfer in" in text
        or "transfer-in" in text
    ):

        return "TRANSFER_IN"



    if (
        "transfer out" in text
        or "transfer-out" in text
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

def classify_transaction_fast(df):

    text = (
        df["trxn_nature"].fillna("").astype(str)
        + " "
        + df["trxntype"].fillna("").astype(str)
        + " "
        + df["td_purred"].fillna("").astype(str)
    ).str.lower()


    result = pd.Series(
        "OTHER",
        index=df.index
    )


    result[
        text.str.contains(
            "purchase|fresh purchase|additional purchase"
        )
        |
        df["trxntype"].str.upper().eq("PUR")
    ] = "PURCHASE"


    result[
        text.str.contains(
            "redemption|redeem"
        )
        |
        df["trxntype"].str.upper().eq("RED")
    ] = "REDEMPTION"


    result[
        text.str.contains(
            "sip|systematic"
        )
    ] = "SIP"


    result[
        text.str.contains(
            "switch in|switchin"
        )
    ] = "SWITCH_IN"


    result[
        text.str.contains(
            "switch out|switchout"
        )
    ] = "SWITCH_OUT"


    result[
        text.str.contains(
            "dividend"
        )
    ] = "DIVIDEND"


    result[
        text.str.contains(
            "stp"
        )
    ] = "STP"


    return result


# =====================================================
# TRANSFORM GOLD TRANSACTIONS
# =====================================================

def transform_transactions(df):


    print("=" * 80)
    print("Transforming Gold Transactions")
    print("=" * 80)



    if df.empty:

        return pd.DataFrame()



    gold_df = pd.DataFrame()



    # =====================================================
    # RTA
    # CAMS / KFIN
    # =====================================================

    gold_df["rta"] = (

        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()

    )



    # =====================================================
    # RTA TRANSACTION NUMBER
    # TRXNNO / TD_TRNO
    # =====================================================

    gold_df["rta_txn_no"] = (

        df["trxnno"]
        .fillna("")
        .astype(str)
        .str.replace(
            ".0",
            "",
            regex=False
        )
        .str.strip()

    )


    gold_df.loc[
        gold_df["rta_txn_no"] == "",
        "rta_txn_no"
    ] = None



    # =====================================================
    # PAN
    # =====================================================

    gold_df["pan"] = (

        df["pan"]
        .fillna("")
        .astype(str)
        .str.replace(
            ".0",
            "",
            regex=False
        )
        .str.strip()
        .str.upper()

    )


    gold_df.loc[
        gold_df["pan"] == "",
        "pan"
    ] = None



    # =====================================================
    # FOLIO
    # =====================================================

    gold_df["folio_number"] = (

        df["folio_no"]
        .fillna("")
        .astype(str)
        .str.replace(
            ".0",
            "",
            regex=False
        )
        .str.strip()

    )


    gold_df.loc[
        gold_df["folio_number"] == "",
        "folio_number"
    ] = None



    # =====================================================
    # TRANSACTION TYPE
    # =====================================================

    gold_df["txn_type"] = classify_transaction_fast(df)



    # =====================================================
    # RAW TRANSACTION TYPE
    # =====================================================

    gold_df["txn_type_raw"] = df["trxntype"]



    # =====================================================
    # DESCRIPTION
    # =====================================================

    gold_df["txn_desc"] = df["trxn_nature"]



    # =====================================================
    # DATES
    # =====================================================

    gold_df["txn_date"] = pd.to_datetime(
        df["traddate"],
        errors="coerce",
        dayfirst=True,
    ).dt.date


    gold_df["post_date"] = pd.to_datetime(
        df["postdate"],
        errors="coerce",
        dayfirst=True
    ).dt.date



    # =====================================================
    # NUMERIC FIELDS
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



    # =====================================================
    # GST
    # =====================================================

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
    # DISTRIBUTOR
    # =====================================================

    gold_df["arn"] = (

        df["brokcode"]
        .fillna("")
        .astype(str)
        .str.strip()

    )


    gold_df["euin"] = df["euin"]


    gold_df["sip_ref"] = df["siptrxnno"]


    gold_df["status"] = df["trxnstat"]

    # =====================================================
    # CLEAN SOURCE + PRODUCT CODE
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
        .str.replace(
            ".0",
            "",
            regex=False
        )
        .str.strip()
        .str.upper()

    )


    # =====================================================
    # CLEAN SOURCE + PRODUCT CODE
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
        .str.replace(".0", "", regex=False)
        .str.strip()
        .str.upper()
    )

    gold_df["scheme_code"] = df["prodcode"]

    # =====================================================
    # LOAD MASTER SCHEME
    # =====================================================

    master_scheme = pd.read_sql(
        """
        SELECT
            id,
            scheme_code
        FROM public.scheme_master
        """,
        master_engine
    )

    master_scheme["scheme_code"] = (
        master_scheme["scheme_code"]
        .fillna("")
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.strip()
        .str.upper()
    )

    master_scheme = master_scheme.drop_duplicates(
        subset=["scheme_code"],
        keep="first"
    )

    # =====================================================
    # MERGE
    # =====================================================

    df = df.merge(
        master_scheme,
        left_on="prodcode",
        right_on="scheme_code",
        how="left"
    )

    gold_df["scheme_id"] = df["id"]

    # =====================================================
    # DEBUG
    # =====================================================

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)

    print("Master rows :", len(master_scheme))
    print("Distinct prodcodes :", df["prodcode"].nunique())
    print("Distinct scheme codes :", master_scheme["scheme_code"].nunique())

    print(
        "Matching codes :",
        len(
            set(df["prodcode"]) &
            set(master_scheme["scheme_code"])
        )
    )

    print("Matched :", gold_df["scheme_id"].notna().sum())
    print("Missing :", gold_df["scheme_id"].isna().sum())

    if gold_df["scheme_id"].isna().all():
        print("\nSample prodcodes from silver:")
        print(df["prodcode"].drop_duplicates().head(20).tolist())

        print("\nSample scheme_codes from master:")
        print(master_scheme["scheme_code"].drop_duplicates().head(20).tolist())


    # =====================================================
    # APP MANAGED COLUMNS
    # =====================================================

    gold_df["client_id"] = None

    gold_df["amc_id"] = None


    # silver mapping:
    # trxnsubtyp -> txn_sub_type

    gold_df["txn_sub_type"] = (

        df["trxnsubtyp"]
        .fillna("")
        .astype(str)
        .str.strip()

    )


    gold_df["rta_txn_id"] = None

    gold_df["arn_id"] = None

    gold_df["sip_id"] = None


    gold_df["source"] = df["source"]


    gold_df["source_file_id"] = None



    # =====================================================
    # CREATED AT
    # =====================================================

    gold_df["created_at"] = pd.Timestamp.utcnow()



    # =====================================================
    # KEEP GOLD TABLE COLUMNS
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

            "scheme_code"

        ]

    ]



    # =====================================================
    # REMOVE INVALID TRANSACTIONS
    # =====================================================

    gold_df = gold_df.dropna(

        subset=[

            "rta",

            "rta_txn_no"

        ]

    )



    # =====================================================
    # REMOVE DUPLICATES IN CURRENT BATCH
    # =====================================================

    gold_df = gold_df.drop_duplicates(

        subset=[

            "rta",

            "rta_txn_no"

        ],

        keep="last"

    )



    # =====================================================
    # STRING LENGTH VALIDATION
    # =====================================================

    gold_df["rta"] = (

        gold_df["rta"]
        .astype("string")
        .str[:10]

    )


    gold_df["rta_txn_no"] = (

        gold_df["rta_txn_no"]
        .astype("string")
        .str[:50]

    )


    gold_df["pan"] = (

        gold_df["pan"]
        .astype("string")
        .str[:10]

    )


    gold_df["folio_number"] = (

        gold_df["folio_number"]
        .astype("string")
        .str[:40]

    )


    gold_df["txn_type"] = (

        gold_df["txn_type"]
        .astype("string")
        .str[:30]

    )


    gold_df["txn_type_raw"] = (

        gold_df["txn_type_raw"]
        .astype("string")
        .str[:40]

    )


    gold_df["txn_desc"] = (

        gold_df["txn_desc"]
        .astype("string")
        .str[:120]

    )


    gold_df["arn"] = (

        gold_df["arn"]
        .astype("string")
        .str[:20]

    )


    gold_df["euin"] = (

        gold_df["euin"]
        .astype("string")
        .str[:20]

    )


    gold_df["sip_ref"] = (

        gold_df["sip_ref"]
        .astype("string")
        .str[:50]

    )


    gold_df["status"] = (

        gold_df["status"]
        .astype("string")
        .str[:10]

    )


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
            "No new records found."
        )

        return True



    try:

        existing = safe_read(

            """
            SELECT

                rta,
                rta_txn_no,
                pan,
                folio_number,
                txn_type,
                txn_type_raw,
                txn_desc,
                txn_date,
                post_date,
                amount,
                units,
                nav,
                load_amount,
                stt,
                stamp_duty,
                gst,
                arn,
                euin,
                sip_ref,
                status,
                client_id,
                amc_id,
                scheme_id,
                scheme_code,
                txn_sub_type,
                rta_txn_id,
                arn_id,
                sip_id,
                source,
                source_file_id

            FROM gold.transactions
            """

        )


    except Exception:

        existing = pd.DataFrame()



    # =====================================================
    # REMOVE EXISTING RECORDS
    # =====================================================

    if not existing.empty:


        old_keys = set(
            create_row_key(existing)
        )


        new_keys = create_row_key(gold_df)


        gold_df = gold_df.loc[
            ~new_keys.isin(old_keys)
        ]



    if gold_df.empty:

        print(
            "Duplicate transaction records skipped."
        )

        return True



    # =====================================================
    # INSERT GOLD
    # =====================================================

    try:


        gold_df.to_sql(

            name="transactions",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )


        print(
            f"{len(gold_df)} rows inserted into gold.transactions"
        )


        return True



    except Exception:


        print(
            "FAILED LOADING GOLD TRANSACTIONS"
        )


        traceback.print_exc(
            limit=5
        )


        return False




# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":


    print("=" * 80)

    print(
        "STARTING GOLD TRANSACTION ETL"
    )

    print("=" * 80)



    df = extract_transactions()



    if df.empty:


        print(
            "No transaction records found."
        )


    else:


        gold_df = transform_transactions(
            df
        )


        print(
            gold_df.head()
        )


        status = load_transactions(
            gold_df
        )



        if status:


            print("=" * 80)

            print(
                "GOLD TRANSACTION ETL COMPLETED SUCCESSFULLY"
            )

            print("=" * 80)


        else:


            print("=" * 80)

            print(
                "GOLD TRANSACTION ETL FAILED"
            )

            print("=" * 80)