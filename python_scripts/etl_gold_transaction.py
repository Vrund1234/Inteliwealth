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
# NORMALIZE DATA FOR COMPARISON
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
# (rta + rta_txn_no)
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
# EXTRACT GOLD TRANSACTIONS
# =====================================================

def extract_transactions():

    print("=" * 80)
    print("Extracting Gold Transactions")
    print("=" * 80)

    last_time = get_last_processed_time()

    df = safe_read(
        """
        SELECT *
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
    # INCREMENTAL FILTER
    # =====================================================

    df = df[
        df["created_at"] > last_time
    ]

    print("Rows fetched :", len(df))

    return df

# =====================================================
# TRANSACTION CLASSIFICATION
# =====================================================

def classify_transaction(row):

    source = str(
        row.get("source", "")
    ).upper()

    # =====================================================
    # CAMS
    # =====================================================

    if source == "CAMS":

        desc = str(
            row.get("trxn_nature", "")
        ).lower()

        raw = str(
            row.get("trxntype", "")
        ).lower()

        text = desc + " " + raw

        if (
            raw in ["swi", "switch in"]
            or "switch in" in text
            or "switchin" in text
        ):
            return "SWITCH_IN"

        if (
            raw in ["swo", "switch out"]
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

    # =====================================================
    # KFIN
    # =====================================================

    desc = str(
        row.get("trdesc", "")
    ).lower()

    raw = str(
        row.get("td_trtype", "")
    ).lower()

    purred = str(
        row.get("td_purred", "")
    ).lower()

    text = desc + " " + raw + " " + purred

    if "switch in" in text:
        return "SWITCH_IN"

    if "switch out" in text:
        return "SWITCH_OUT"

    if (
        "redemption" in text
        or "redeem" in text
    ):
        return "REDEMPTION"

    if "sip" in text:
        return "SIP"

    if "stp" in text:
        return "STP"

    if "dividend" in text:
        return "DIVIDEND"

    if "purchase" in text:
        return "PURCHASE"

    return "OTHER"


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
    # =====================================================

    gold_df["rta_txn_no"] = None

    cams_mask = gold_df["rta"] == "CAMS"

    kfin_mask = gold_df["rta"] == "KFIN"

    gold_df.loc[cams_mask, "rta_txn_no"] = (
        df.loc[cams_mask, "trxnno"]
    )

    gold_df.loc[kfin_mask, "rta_txn_no"] = (
        df.loc[kfin_mask, "td_trno"]
    )

    gold_df["rta_txn_no"] = (
        gold_df["rta_txn_no"]
        .astype("string")
        .str.strip()
    )

    gold_df.loc[
        gold_df["rta_txn_no"] == "",
        "rta_txn_no"
    ] = None

    # =====================================================
    # PAN
    # =====================================================

    gold_df["pan"] = None

    gold_df.loc[cams_mask, "pan"] = (
        df.loc[cams_mask, "pan"]
    )

    gold_df.loc[kfin_mask, "pan"] = (
        df.loc[kfin_mask, "pangno"]
    )

    gold_df["pan"] = (

        gold_df["pan"]
        .astype("string")
        .str.strip()
        .str.upper()
        .str.replace(".0", "", regex=False)

    )

    gold_df.loc[
        gold_df["pan"] == "",
        "pan"
    ] = None

    # =====================================================
    # FOLIO NUMBER
    # =====================================================

    gold_df["folio_number"] = None

    gold_df.loc[cams_mask, "folio_number"] = (
        df.loc[cams_mask, "folio_no"]
    )

    gold_df.loc[kfin_mask, "folio_number"] = (
        df.loc[kfin_mask, "td_acno"]
    )

    gold_df["folio_number"] = (

        gold_df["folio_number"]
        .astype("string")
        .str.strip()
        .str.replace(".0", "", regex=False)

    )

    gold_df.loc[
        gold_df["folio_number"] == "",
        "folio_number"
    ] = None

    # =====================================================
    # TRANSACTION TYPE
    # =====================================================

    gold_df["txn_type"] = df.apply(
        classify_transaction,
        axis=1
    )

    # =====================================================
    # RAW TRANSACTION TYPE
    # =====================================================

    gold_df["txn_type_raw"] = None

    gold_df.loc[cams_mask, "txn_type_raw"] = (
        df.loc[cams_mask, "trxntype"]
    )

    gold_df.loc[kfin_mask, "txn_type_raw"] = (
        df.loc[kfin_mask, "td_trtype"]
    )

    # =====================================================
    # TRANSACTION DESCRIPTION
    # =====================================================

    gold_df["txn_desc"] = None

    gold_df.loc[cams_mask, "txn_desc"] = (
        df.loc[cams_mask, "trxn_nature"]
    )

    gold_df.loc[kfin_mask, "txn_desc"] = (
        df.loc[kfin_mask, "trdesc"]
    )

        # =====================================================
    # TRANSACTION DATE
    # =====================================================

    gold_df["txn_date"] = None

    gold_df.loc[cams_mask, "txn_date"] = pd.to_datetime(
        df.loc[cams_mask, "traddate"],
        errors="coerce"
    ).dt.date

    gold_df.loc[kfin_mask, "txn_date"] = pd.to_datetime(
        df.loc[kfin_mask, "td_trdt"],
        errors="coerce"
    ).dt.date

    # =====================================================
    # POST DATE
    # =====================================================

    gold_df["post_date"] = None

    gold_df.loc[cams_mask, "post_date"] = pd.to_datetime(
        df.loc[cams_mask, "postdate"],
        errors="coerce"
    ).dt.date

    gold_df.loc[kfin_mask, "post_date"] = pd.to_datetime(
        df.loc[kfin_mask, "td_prdt"],
        errors="coerce"
    ).dt.date

    # =====================================================
    # AMOUNT
    # =====================================================

    gold_df["amount"] = None

    gold_df.loc[cams_mask, "amount"] = pd.to_numeric(
        df.loc[cams_mask, "amount"],
        errors="coerce"
    )

    gold_df.loc[kfin_mask, "amount"] = pd.to_numeric(
        df.loc[kfin_mask, "td_amt"],
        errors="coerce"
    )

    # =====================================================
    # UNITS
    # =====================================================

    gold_df["units"] = None

    gold_df.loc[cams_mask, "units"] = pd.to_numeric(
        df.loc[cams_mask, "units"],
        errors="coerce"
    )

    gold_df.loc[kfin_mask, "units"] = pd.to_numeric(
        df.loc[kfin_mask, "td_units"],
        errors="coerce"
    )

    # =====================================================
    # NAV
    # =====================================================

    gold_df["nav"] = None

    gold_df.loc[cams_mask, "nav"] = pd.to_numeric(
        df.loc[cams_mask, "purprice"],
        errors="coerce"
    )

    gold_df.loc[kfin_mask, "nav"] = pd.to_numeric(
        df.loc[kfin_mask, "td_nav"],
        errors="coerce"
    )

    # =====================================================
    # LOAD AMOUNT
    # =====================================================

    gold_df["load_amount"] = None

    gold_df.loc[cams_mask, "load_amount"] = pd.to_numeric(
        df.loc[cams_mask, "load"],
        errors="coerce"
    )

    gold_df.loc[kfin_mask, "load_amount"] = pd.to_numeric(
        df.loc[kfin_mask, "load1"],
        errors="coerce"
    )

    # =====================================================
    # STT
    # =====================================================

    gold_df["stt"] = pd.to_numeric(
        df["stt"],
        errors="coerce"
    )

    # =====================================================
    # STAMP DUTY
    # =====================================================

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
    # ARN
    # =====================================================

    gold_df["arn"] = None

    gold_df.loc[cams_mask, "arn"] = (
        df.loc[cams_mask, "brokcode"]
    )

    gold_df.loc[kfin_mask, "arn"] = (
        df.loc[kfin_mask, "td_agent"]
    )

    # =====================================================
    # EUIN
    # =====================================================

    gold_df["euin"] = df["euin"]

    # =====================================================
    # SIP REFERENCE
    # =====================================================

    gold_df["sip_ref"] = df["siptrxnno"]

    # =====================================================
    # STATUS
    # =====================================================

    gold_df["status"] = None

    gold_df.loc[cams_mask, "status"] = (
        df.loc[cams_mask, "trxnstat"]
    )

    gold_df.loc[kfin_mask, "status"] = (
        df.loc[kfin_mask, "trnstat"]
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
    # CLEAN TRANSACTION KEYS
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

    gold_df["scheme_id"] = df["id"]

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)

    print("Total Transactions :", len(gold_df))
    print("Matched Scheme IDs :", gold_df["scheme_id"].notna().sum())
    print("Missing Scheme IDs :", gold_df["scheme_id"].isna().sum())

    # =====================================================
    # APP MANAGED COLUMNS
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
    # CREATED AT
    # =====================================================

    gold_df["created_at"] = pd.Timestamp.now()

    # =====================================================
    # KEEP TARGET COLUMNS
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
            "created_at"
        ]

    ]

    # =====================================================
    # REMOVE INVALID RECORDS
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
    # MAP SCHEME ID
    # =====================================================

    gold_df["scheme_id"] = df["id"]

    print("=" * 80)
    print("SCHEME ID VALIDATION")
    print("=" * 80)

    print("Total Transactions :", len(gold_df))
    print("Matched Scheme IDs :", gold_df["scheme_id"].notna().sum())
    print("Missing Scheme IDs :", gold_df["scheme_id"].isna().sum())

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
    # APP MANAGED COLUMNS
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
    # COLUMN ORDER
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
            "source_file_id"
        ]
    ]

    # =====================================================
    # REMOVE INVALID ROWS
    # =====================================================

    gold_df = gold_df.dropna(
        subset=[
            "rta_txn_no"
        ]
    )

    gold_df = gold_df.drop_duplicates(

        subset=[
            "rta",
            "rta_txn_no"
        ],

        keep="last"

    )

    # =====================================================
    # LENGTH VALIDATION
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

    gold_df["created_at"] = pd.Timestamp.utcnow()

    print("Rows Ready :", len(gold_df))

    return gold_df

# =====================================================
# LOAD GOLD TRANSACTIONS
# =====================================================

def load_transactions(gold_df):

    print("=" * 80)
    print("Loading Gold Transactions")
    print("=" * 80)

    if gold_df.empty:

        print("No new records found.")

        return True

    # =====================================================
    # READ EXISTING GOLD DATA
    # =====================================================

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
                txn_sub_type,
                rta_txn_id,
                arn_id,
                sip_id,
                source,
                source_file_id,
                created_at

            FROM gold.transactions
            """
        )

    except Exception:

        existing = pd.DataFrame()

    # =====================================================
    # REMOVE EXISTING DUPLICATES
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

        print("Duplicate transaction records skipped.")

        return True

    # =====================================================
    # INSERT INTO GOLD
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

        print(f"{len(gold_df)} rows inserted into Gold Transactions.")

        return True

    except Exception:

        print("FAILED LOADING GOLD TRANSACTIONS")

        traceback.print_exc(limit=5)

        return False

# =====================================================
# CLASSIFY TRANSACTION
# =====================================================

def classify_transaction(row):

    desc = str(
        row.get("trxn_nature", "")
    ).lower()

    raw = str(
        row.get("trxntype", "")
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
# MAIN
# =====================================================

if __name__ == "__main__":

    print("=" * 80)
    print("STARTING GOLD TRANSACTION ETL")
    print("=" * 80)

    df = extract_transactions()

    if df.empty:

        print("No transaction records found.")

    else:

        gold_df = transform_transactions(df)

        status = load_transactions(gold_df)

        if status:

            print("=" * 80)
            print("GOLD TRANSACTION ETL COMPLETED SUCCESSFULLY")
            print("=" * 80)

        else:

            print("=" * 80)
            print("GOLD TRANSACTION ETL FAILED")
            print("=" * 80)