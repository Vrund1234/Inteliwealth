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

        print("SQL ERROR:", e)

        return pd.DataFrame()


# =====================================================
# NORMALIZE DATA FOR DUPLICATE CHECK
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=["created_at"],
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
    print("EXTRACTING SILVER TRANSACTIONS")
    print("=" * 80)

    query = """
        SELECT
            source,
            folio_no,
            prodcode,

            -- IMPORTANT:
            -- Take scheme_id directly from Silver
            scheme_id,

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
            src_brk_code,
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
            created_at,
            flag

        FROM silver.transaction_master_new

        WHERE flag = 0
    """

    df = safe_read(query)

    if df.empty:

        print(
            "No unprocessed Silver transactions found."
        )

        return df

    print(
        "Rows fetched:",
        len(df)
    )

    # =================================================
    # CHECK SCHEME ID
    # =================================================

    print("=" * 80)
    print("SILVER SCHEME ID CHECK")
    print("=" * 80)

    print(
        "Scheme ID datatype:",
        df["scheme_id"].dtype
    )

    print(
        "Scheme IDs present:",
        df["scheme_id"].notna().sum()
    )

    print(
        "Scheme IDs missing:",
        df["scheme_id"].isna().sum()
    )

    print(
        "Sample Silver scheme IDs:"
    )

    print(
        df["scheme_id"]
        .dropna()
        .drop_duplicates()
        .head(20)
        .tolist()
    )

    return df


# =====================================================
# TRANSACTION CLASSIFICATION
# =====================================================

def classify_transaction_fast(df):

    text = (

        df["trxn_nature"]
        .fillna("")
        .astype(str)

        + " "

        + df["trxntype"]
        .fillna("")
        .astype(str)

        + " "

        + df["td_purred"]
        .fillna("")
        .astype(str)

    ).str.lower()

    result = pd.Series(
        "OTHER",
        index=df.index
    )

    # -------------------------------------------------
    # PURCHASE
    # -------------------------------------------------

    result[
        text.str.contains(
            "purchase|fresh purchase|additional purchase",
            regex=True,
            na=False
        )
        |
        df["trxntype"]
        .fillna("")
        .astype(str)
        .str.upper()
        .eq("PUR")
    ] = "PURCHASE"

    # -------------------------------------------------
    # REDEMPTION
    # -------------------------------------------------

    result[
        text.str.contains(
            "redemption|redeem",
            regex=True,
            na=False
        )
        |
        df["trxntype"]
        .fillna("")
        .astype(str)
        .str.upper()
        .eq("RED")
    ] = "REDEMPTION"

    # -------------------------------------------------
    # SIP
    # -------------------------------------------------

    result[
        text.str.contains(
            "sip|systematic",
            regex=True,
            na=False
        )
    ] = "SIP"

    # -------------------------------------------------
    # SWITCH IN
    # -------------------------------------------------

    result[
        text.str.contains(
            "switch in|switchin",
            regex=True,
            na=False
        )
    ] = "SWITCH_IN"

    # -------------------------------------------------
    # SWITCH OUT
    # -------------------------------------------------

    result[
        text.str.contains(
            "switch out|switchout",
            regex=True,
            na=False
        )
    ] = "SWITCH_OUT"

    # -------------------------------------------------
    # DIVIDEND
    # -------------------------------------------------

    result[
        text.str.contains(
            "dividend",
            regex=True,
            na=False
        )
    ] = "DIVIDEND"

    # -------------------------------------------------
    # STP
    # -------------------------------------------------

    result[
        text.str.contains(
            "stp",
            regex=True,
            na=False
        )
    ] = "STP"

    # -------------------------------------------------
    # TRANSFER IN
    # -------------------------------------------------

    result[
        text.str.contains(
            "transfer in|transfer-in",
            regex=True,
            na=False
        )
    ] = "TRANSFER_IN"

    # -------------------------------------------------
    # TRANSFER OUT
    # -------------------------------------------------

    result[
        text.str.contains(
            "transfer out|transfer-out",
            regex=True,
            na=False
        )
    ] = "TRANSFER_OUT"

    return result


# =====================================================
# TRANSFORM GOLD TRANSACTIONS
# =====================================================

def transform_transactions(df):

    print("=" * 80)
    print("TRANSFORMING GOLD TRANSACTIONS")
    print("=" * 80)

    if df.empty:
        return pd.DataFrame()

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
    # RTA TRANSACTION NUMBER
    # =================================================

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

    # =================================================
    # PAN
    # =================================================

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

    # =================================================
    # FOLIO
    # =================================================

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

    # =================================================
    # TRANSACTION TYPE
    # =================================================

    gold_df["txn_type"] = (
        classify_transaction_fast(df)
    )

    # =================================================
    # RAW TRANSACTION TYPE
    # =================================================

    gold_df["txn_type_raw"] = df["trxntype"]

    # =================================================
    # DESCRIPTION
    # =================================================

    gold_df["txn_desc"] = df["trxn_nature"]

    # =================================================
    # DATES
    # =================================================

    gold_df["txn_date"] = (
        pd.to_datetime(
            df["traddate"],
            errors="coerce",
            dayfirst=True
        )
        .dt.date
    )

    gold_df["post_date"] = (
        pd.to_datetime(
            df["postdate"],
            errors="coerce",
            dayfirst=True
        )
        .dt.date
    )

    # =================================================
    # NUMERIC FIELDS
    # =================================================

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

    # =================================================
    # GST
    # =================================================

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

    # =================================================
    # ARN
    # =================================================

    gold_df["arn"] = (
        df["brokcode"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
    ] = None

    # =================================================
    # SUB ARN
    # =================================================

    gold_df["sub_arn"] = (
        df["src_brk_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df.loc[
        gold_df["sub_arn"] == "",
        "sub_arn"
    ] = None

    # =================================================
    # EUIN / SIP / STATUS
    # =================================================

    gold_df["euin"] = df["euin"]

    gold_df["sip_ref"] = df["siptrxnno"]

    gold_df["status"] = df["trxnstat"]

    # =================================================
    # CLEAN SOURCE
    # =================================================

    source = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =================================================
    # CLEAN PRODUCT CODE
    # =================================================

    prodcode = (
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

    # =================================================
    # SCHEME CODE
    # =================================================

    gold_df["scheme_code"] = prodcode

    # =================================================
    # SCHEME ID
    #
    # IMPORTANT:
    #
    # DO NOT GENERATE UUID
    # DO NOT LOOK UP AMFI UUID
    # DO NOT CREATE scheme_code -> UUID mapping
    #
    # Directly copy the numeric scheme_id from:
    #
    # silver.transaction_master_new.scheme_id
    #
    # Example:
    #
    # Silver scheme_id = 128114564
    #
    # Gold scheme_id = 128114564
    #
    # =================================================

    print("=" * 80)
    print("MAPPING SCHEME ID")
    print("=" * 80)

    gold_df["scheme_id"] = (
        df["scheme_id"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    gold_df.loc[
        gold_df["scheme_id"] == "",
        "scheme_id"
    ] = None

    print(
        "Silver scheme_id rows:",
        df["scheme_id"].notna().sum()
    )

    print(
        "Gold scheme_id rows:",
        gold_df["scheme_id"].notna().sum()
    )

    print(
        "Missing Gold scheme_id:",
        gold_df["scheme_id"].isna().sum()
    )

    print(
        "Sample Gold scheme IDs:"
    )

    print(
        gold_df["scheme_id"]
        .dropna()
        .drop_duplicates()
        .head(20)
        .tolist()
    )

    # =================================================
    # APP MANAGED COLUMNS
    # =================================================

    gold_df["client_id"] = None

    gold_df["amc_id"] = None

    gold_df["txn_sub_type"] = (
        df["trxnsubtyp"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df["rta_txn_id"] = None

    gold_df["arn_id"] = None

    gold_df["sip_id"] = None

    gold_df["source"] = source

    gold_df["source_file_id"] = None

    # =================================================
    # CREATED AT
    # =================================================

    gold_df["created_at"] = pd.Timestamp.utcnow()

    # =================================================
    # GOLD COLUMN ORDER
    # =================================================

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
            "sub_arn",
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

    # =================================================
    # REMOVE INVALID
    # =================================================

    gold_df = gold_df.dropna(
        subset=[
            "rta",
            "rta_txn_no"
        ]
    )

    # =================================================
    # REMOVE BATCH DUPLICATES
    # =================================================

    gold_df = gold_df.drop_duplicates(
        subset=[
            "rta",
            "rta_txn_no"
        ],
        keep="last"
    )

    # =================================================
    # STRING LENGTH VALIDATION
    # =================================================

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

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
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

    print("=" * 80)
    print("TRANSFORM COMPLETE")
    print("=" * 80)

    print(
        "Rows ready:",
        len(gold_df)
    )

    print(
        "Rows with scheme_id:",
        gold_df["scheme_id"].notna().sum()
    )

    print(
        "Rows without scheme_id:",
        gold_df["scheme_id"].isna().sum()
    )

    return gold_df


# =====================================================
# LOAD GOLD TRANSACTIONS
# =====================================================

def load_transactions(gold_df):

    print("=" * 80)
    print("LOADING GOLD TRANSACTIONS")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No new records found."
        )

        return True

    # =================================================
    # FINAL SCHEME ID CHECK
    # =================================================

    print(
        "Python scheme_id dtype:",
        gold_df["scheme_id"].dtype
    )

    print(
        "Sample scheme_id before INSERT:"
    )

    print(
        gold_df["scheme_id"]
        .dropna()
        .head(10)
        .tolist()
    )

    # =================================================
    # EXISTING GOLD RECORDS
    # =================================================

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
            sub_arn,
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

    # =================================================
    # REMOVE EXISTING RECORDS
    # =================================================

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

    # =================================================
    # INSERT
    # =================================================

    try:

        print(
            f"Inserting {len(gold_df)} rows..."
        )

        gold_df.to_sql(
            name="transactions",
            con=engine,
            schema="gold",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500
        )

        print(
            f"{len(gold_df)} rows successfully inserted "
            f"into gold.transactions"
        )

        return True

    except Exception:

        print("=" * 80)
        print("FAILED LOADING GOLD TRANSACTIONS")
        print("=" * 80)

        traceback.print_exc(
            limit=10
        )

        return False


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    print("=" * 80)
    print("STARTING GOLD TRANSACTION ETL")
    print("=" * 80)

    # =================================================
    # EXTRACT
    # =================================================

    df = extract_transactions()

    if df.empty:

        print(
            "No transaction records found."
        )

    else:

        # =================================================
        # TRANSFORM
        # =================================================

        gold_df = transform_transactions(
            df
        )

        print("=" * 80)
        print("GOLD DATA SAMPLE")
        print("=" * 80)

        print(
            gold_df[
                [
                    "rta",
                    "rta_txn_no",
                    "scheme_id",
                    "scheme_code"
                ]
            ].head(20)
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

        print("=" * 80)

        if status:

            print(
                "GOLD TRANSACTION ETL COMPLETED SUCCESSFULLY"
            )

        else:

            print(
                "GOLD TRANSACTION ETL FAILED"
            )

        print("=" * 80)