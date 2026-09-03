import pandas as pd
import traceback

from utils.db import engine


# =====================================================
# SAFE READ
# =====================================================

def safe_read(query, params=None):

    try:

        return pd.read_sql(
            query,
            engine,
            params=params
        )

    except Exception as e:

        print("SQL ERROR:", e)

        traceback.print_exc(limit=5)

        return pd.DataFrame()


# =====================================================
# GET LAST GOLD TIMESTAMP
# =====================================================

def get_last_gold_timestamp():

    print("=" * 80)
    print("CHECKING LAST GOLD TRANSACTION TIMESTAMP")
    print("=" * 80)

    query = """
        SELECT
            MAX(created_at) AS last_created_at
        FROM gold.transactions
    """

    result = safe_read(query)

    if result.empty:

        print("Gold transactions table returned no result.")

        return None

    last_created_at = result.iloc[0]["last_created_at"]

    if pd.isna(last_created_at):

        print("Gold transactions is empty.")

        return None

    print(
        "Last Gold created_at:",
        last_created_at
    )

    return last_created_at


# =====================================================
# EXTRACT SILVER TRANSACTIONS
#
# ONLY TIMESTAMP COMPARISON IS USED.
#
# NO DUPLICATE CHECKING.
# NO ROW SIGNATURE.
# NO DROP DUPLICATES.
# NO NATURAL KEY COMPARISON.
# =====================================================

def extract_transactions():

    print("=" * 80)
    print("EXTRACTING SILVER TRANSACTIONS")
    print("=" * 80)

    last_gold_timestamp = get_last_gold_timestamp()

    # =================================================
    # FIRST RUN
    # =================================================

    if last_gold_timestamp is None:

        print()
        print("No previous Gold timestamp found.")
        print("This is treated as the first Gold load.")

        query = """
            SELECT
                source,
                folio_no,
                prodcode,
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
                isin,
                created_at,
                flag,
                src_of_txn,
                trxnmode,
                trxn_type_flag,
                transmission_flag,
                sub_tran_type,
                to_product_code,
                to_scheme,
                to_plan,
                switch_ref_no

            FROM silver.transaction_master_new

            ORDER BY created_at
        """

        df = safe_read(query)

    # =================================================
    # SUBSEQUENT RUN
    # =================================================

    else:

        print()
        print(
            "Loading only Silver rows newer than:"
        )

        print(last_gold_timestamp)

        query = """
            SELECT
                source,
                folio_no,
                prodcode,
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
                isin,
                created_at,
                flag,
                src_of_txn,
                trxnmode,
                trxn_type_flag,
                transmission_flag,
                sub_tran_type,
                to_product_code,
                to_scheme,
                to_plan,
                switch_ref_no

            FROM silver.transaction_master_new

            WHERE created_at > %s

            ORDER BY created_at
        """

        df = safe_read(
            query,
            params=(last_gold_timestamp,)
        )

    # =================================================
    # RESULT
    # =================================================

    if df.empty:

        print()
        print(
            "No new Silver transactions found "
            "after timestamp comparison."
        )

        return df

    print()
    print(
        "Rows fetched:",
        len(df)
    )

    print(
        "Minimum Silver created_at:",
        df["created_at"].min()
    )

    print(
        "Maximum Silver created_at:",
        df["created_at"].max()
    )

    # =================================================
    # SCHEME ID CHECK
    # =================================================

    print()
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

    print("Sample Silver scheme IDs:")

    print(
        df["scheme_id"]
        .dropna()
        .head(20)
        .tolist()
    )

    return df


# =====================================================
# CONSTANTS
# =====================================================

IN = "IN"
OUT = "OUT"
NONE = "NONE"


# =====================================================
# EXACT TRANSACTION CODE MAP
#
# code -> (
#     txn_type,
#     transaction_sub_type,
#     transaction_direction
# )
# =====================================================

TRANSACTION_CODE_MAP = {

    # -------------------------------------------------
    # PURCHASE
    # -------------------------------------------------

    "NEW": (
        "PURCHASE",
        "NEW_PURCHASE",
        IN
    ),

    "ADD": (
        "PURCHASE",
        "ADDITIONAL_PURCHASE",
        IN
    ),

    "SIN": (
        "PURCHASE",
        "SIP",
        IN
    ),

    "IPO": (
        "PURCHASE",
        "INITIAL_ALLOTMENT",
        IN
    ),

    # -------------------------------------------------
    # REDEMPTION
    # -------------------------------------------------

    "RED": (
        "REDEMPTION",
        "REDEMPTION",
        OUT
    ),

    "FUL": (
        "REDEMPTION",
        "FULL_REDEMPTION",
        OUT
    ),

    "SWD": (
        "REDEMPTION",
        "SWP",
        OUT
    ),

    # -------------------------------------------------
    # SWITCH
    # -------------------------------------------------

    "SWIN": (
        "SWITCH",
        "SWITCH_IN",
        IN
    ),

    "SWOF": (
        "SWITCH",
        "SWITCH_OUT",
        OUT
    ),

    "SWOP": (
        "SWITCH",
        "SWITCH_OUT",
        OUT
    ),

    "LTIN": (
        "SWITCH",
        "SWITCH_IN",
        IN
    ),

    "LTIA": (
        "SWITCH",
        "SWITCH_IN",
        IN
    ),

    "LTOF": (
        "SWITCH",
        "SWITCH_OUT",
        OUT
    ),

    "LTOP": (
        "SWITCH",
        "SWITCH_OUT",
        OUT
    ),

    # -------------------------------------------------
    # STP
    # -------------------------------------------------

    "STPA": (
        "STP",
        "STP_IN",
        IN
    ),

    "STPN": (
        "STP",
        "STP_IN",
        IN
    ),

    "STPO": (
        "STP",
        "STP_OUT",
        OUT
    ),

    # -------------------------------------------------
    # TRANSFER / TRANSMISSION
    # -------------------------------------------------

    "TRMI": (
        "TRANSFER",
        "TRANSFER_IN",
        IN
    ),

    "TMI": (
        "TRANSFER",
        "TRANSFER_IN",
        IN
    ),

    "TRMO": (
        "TRANSFER",
        "TRANSFER_OUT",
        OUT
    ),

    "TMO": (
        "TRANSFER",
        "TRANSFER_OUT",
        OUT
    ),

    # -------------------------------------------------
    # DIVIDEND
    # -------------------------------------------------

    "DIR": (
        "DIVIDEND",
        "DIVIDEND_REINVESTMENT",
        IN
    ),

    "DIV": (
        "DIVIDEND",
        "DIVIDEND_PAYOUT",
        NONE
    ),

    "DSPI": (
        "DIVIDEND",
        "DIVIDEND_SWEEP_IN",
        IN
    ),

    "DSPO": (
        "DIVIDEND",
        "DIVIDEND_SWEEP_OUT",
        OUT
    ),

    "DRO": (
        "DIVIDEND",
        "DIVIDEND_SWEEP_OUT",
        OUT
    ),

    # -------------------------------------------------
    # BONUS
    # -------------------------------------------------

    "BNS": (
        "BONUS",
        "BONUS",
        IN
    ),

    # -------------------------------------------------
    # CONSOLIDATION
    # -------------------------------------------------

    "CNI": (
        "CONSOLIDATION",
        "CONSOLIDATION_IN",
        IN
    ),

    # -------------------------------------------------
    # NON-UNIT EVENTS
    # -------------------------------------------------

    "PLDO": (
        "OTHER",
        "PLEDGE",
        NONE
    ),

    "UPLO": (
        "OTHER",
        "UNPLEDGE",
        NONE
    ),

    "RFD": (
        "OTHER",
        "REFUND",
        NONE
    ),

    # -------------------------------------------------
    # PURCHASE REJECTIONS
    # -------------------------------------------------

    "NEWR": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    "NEWD": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    "ADDR": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    "ADDD": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    "IPOR": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    "IPOD": (
        "OTHER",
        "PURCHASE_REJECTION",
        NONE
    ),

    # -------------------------------------------------
    # SIP REJECTIONS
    # -------------------------------------------------

    "SINR": (
        "OTHER",
        "SIP_REJECTION",
        NONE
    ),

    "SIND": (
        "OTHER",
        "SIP_REJECTION",
        NONE
    ),

    # -------------------------------------------------
    # REDEMPTION REJECTIONS
    # -------------------------------------------------

    "REDR": (
        "OTHER",
        "REDEMPTION_REJECTION",
        NONE
    ),

    "FULR": (
        "OTHER",
        "REDEMPTION_REJECTION",
        NONE
    ),

    # -------------------------------------------------
    # STP REJECTIONS
    # -------------------------------------------------

    "STPAR": (
        "OTHER",
        "STP_IN_REJECTION",
        NONE
    ),

    "STPAD": (
        "OTHER",
        "STP_IN_REJECTION",
        NONE
    ),

    "STPOR": (
        "OTHER",
        "STP_OUT_REJECTION",
        NONE
    ),

    "STPOD": (
        "OTHER",
        "STP_OUT_REJECTION",
        NONE
    ),

    # -------------------------------------------------
    # SWITCH REJECTIONS
    # -------------------------------------------------

    "LTINR": (
        "OTHER",
        "SWITCH_IN_REJECTION",
        NONE
    ),

    "LTIAR": (
        "OTHER",
        "SWITCH_IN_REJECTION",
        NONE
    ),

    "LTOFR": (
        "OTHER",
        "SWITCH_OUT_REJECTION",
        NONE
    ),

    "LTOPR": (
        "OTHER",
        "SWITCH_OUT_REJECTION",
        NONE
    ),

    # -------------------------------------------------
    # DIVIDEND / BONUS REJECTIONS
    # -------------------------------------------------

    "DIRR": (
        "OTHER",
        "REINVESTMENT_REJECTION",
        NONE
    ),

    "DIVR": (
        "OTHER",
        "DIVIDEND_REJECTION",
        NONE
    ),

    "BNSR": (
        "OTHER",
        "BONUS_REJECTION",
        NONE
    ),
}


# =====================================================
# CAMS PREFIX MAP
#
# CAMS codes may contain scheme-specific suffixes:
#
# SI13S
# SO1
# P80ES
# DR1
# etc.
#
# Longest prefix is checked first.
# =====================================================

TRANSACTION_CODE_PREFIX_MAP = {

    "SI": (
        "SWITCH",
        "SWITCH_IN",
        IN
    ),

    "SO": (
        "SWITCH",
        "SWITCH_OUT",
        OUT
    ),

    "TI": (
        "TRANSFER",
        "TRANSFER_IN",
        IN
    ),

    "TO": (
        "TRANSFER",
        "TRANSFER_OUT",
        OUT
    ),

    "DR": (
        "DIVIDEND",
        "DIVIDEND_REINVESTMENT",
        IN
    ),

    "DP": (
        "DIVIDEND",
        "DIVIDEND_PAYOUT",
        NONE
    ),

    "P": (
        "PURCHASE",
        "PURCHASE",
        IN
    ),

    "R": (
        "REDEMPTION",
        "REDEMPTION",
        OUT
    ),
}


# =====================================================
# NORMALIZE RAW TRANSACTION CODE
# =====================================================

def normalize_transaction_code(value):

    if value is None or pd.isna(value):

        return ""

    code = str(value).strip().upper()

    if code.endswith(".0"):

        code = code[:-2]

    return code


# =====================================================
# CLASSIFY SINGLE TRANSACTION ROW
# =====================================================

def classify_transaction_row(row):

    code = normalize_transaction_code(
        row.get("trxntype")
    )

    nature = str(
        row.get("trxn_nature") or ""
    ).strip().lower()

    td_purred = str(
        row.get("td_purred") or ""
    ).strip().lower()

    src_of_txn = str(
        row.get("src_of_txn") or ""
    ).strip().lower()

    trxnmode = str(
        row.get("trxnmode") or ""
    ).strip().lower()

    trxn_type_flag = str(
        row.get("trxn_type_flag") or ""
    ).strip().lower()

    transmission_flag = str(
        row.get("transmission_flag") or ""
    ).strip().lower()

    sub_tran_type = str(
        row.get("sub_tran_type") or ""
    ).strip().lower()

    switch_ref_no = str(
        row.get("switch_ref_no") or ""
    ).strip().lower()

    # =================================================
    # REJECTIONS FIRST
    # =================================================

    if code in TRANSACTION_CODE_MAP:

        return TRANSACTION_CODE_MAP[code]

    # =================================================
    # PLEDGE / UNPLEDGE
    # =================================================

    if code == "PLDO":

        return (
            "OTHER",
            "PLEDGE",
            NONE
        )

    if code == "UPLO":

        return (
            "OTHER",
            "UNPLEDGE",
            NONE
        )

    # =================================================
    # CONSOLIDATION
    # =================================================

    if code == "CNI":

        return (
            "CONSOLIDATION",
            "CONSOLIDATION_IN",
            IN
        )

    # =================================================
    # TRANSMISSION
    # =================================================

    if (
        code in {"TRMI", "TMI"}
        or "transmission in" in nature
    ):

        return (
            "TRANSFER",
            "TRANSFER_IN",
            IN
        )

    if (
        code in {"TRMO", "TMO"}
        or "transmission out" in nature
    ):

        return (
            "TRANSFER",
            "TRANSFER_OUT",
            OUT
        )

    # =================================================
    # SYSTEMATIC TRANSFER / STP
    #
    # Check this BEFORE generic SI/SO switch logic.
    # =================================================

    systematic_text = " ".join(
        [
            nature,
            td_purred,
            src_of_txn,
            trxnmode,
            trxn_type_flag,
            sub_tran_type,
            switch_ref_no,
        ]
    )

    is_systematic = (
        "systematic" in systematic_text
        or "stp" in systematic_text
        or "systematic" in code.lower()
    )

    if is_systematic:

        if (
            "switch in" in systematic_text
            or "switch-in" in systematic_text
            or code.startswith("SI")
        ):

            return (
                "STP",
                "STP_IN",
                IN
            )

        if (
            "switch out" in systematic_text
            or "switch-out" in systematic_text
            or code.startswith("SO")
        ):

            return (
                "STP",
                "STP_OUT",
                OUT
            )

        if code in {"STPA", "STPN"}:

            return (
                "STP",
                "STP_IN",
                IN
            )

        if code == "STPO":

            return (
                "STP",
                "STP_OUT",
                OUT
            )

    # =================================================
    # DRO DESCRIPTION-BASED HANDLING
    # =================================================

    if code == "DRO":

        if (
            "sweep out" in nature
            or "transferout" in nature
            or "transfer out" in nature
            or "paid & transferred" in nature
            or "reinvested in other scheme" in nature
        ):

            return (
                "DIVIDEND",
                "DIVIDEND_TRANSFER",
                OUT
            )

        return (
            "DIVIDEND",
            "DIVIDEND_SWEEP_OUT",
            OUT
        )

    # =================================================
    # CAMS PREFIX
    # =================================================

    prefixes = sorted(
        TRANSACTION_CODE_PREFIX_MAP.keys(),
        key=len,
        reverse=True
    )

    for prefix in prefixes:

        if code.startswith(prefix):

            txn_type, sub_type, direction = (
                TRANSACTION_CODE_PREFIX_MAP[prefix]
            )

            # -----------------------------------------
            # PURCHASE REFINEMENT
            # -----------------------------------------

            if txn_type == "PURCHASE":

                if (
                    "sip" in nature
                    or "systematic" in nature
                ):

                    sub_type = "SIP"

                elif (
                    "fresh" in nature
                    or "new purchase" in nature
                ):

                    sub_type = "NEW_PURCHASE"

                elif "additional" in nature:

                    sub_type = "ADDITIONAL_PURCHASE"

            # -----------------------------------------
            # REDEMPTION REFINEMENT
            # -----------------------------------------

            elif txn_type == "REDEMPTION":

                if (
                    "swp" in nature
                    or "systematic" in nature
                ):

                    sub_type = "SWP"

                elif (
                    "full redemption" in nature
                    or "full redeem" in nature
                ):

                    sub_type = "FULL_REDEMPTION"

                elif (
                    "partial redemption" in nature
                    or "partial redeem" in nature
                ):

                    sub_type = "PARTIAL_REDEMPTION"

            # -----------------------------------------
            # SWITCH REFINEMENT
            # -----------------------------------------

            elif txn_type == "SWITCH":

                if (
                    "systematic" in nature
                    or "stp" in nature
                ):

                    if direction == IN:

                        txn_type = "STP"
                        sub_type = "STP_IN"

                    else:

                        txn_type = "STP"
                        sub_type = "STP_OUT"

            return (
                txn_type,
                sub_type,
                direction
            )

    # =================================================
    # DESCRIPTION-BASED FALLBACK
    # =================================================

    if (
        "swp" in nature
        or "systematic withdrawal" in nature
    ):

        return (
            "REDEMPTION",
            "SWP",
            OUT
        )

    if "sip" in nature:

        return (
            "PURCHASE",
            "SIP",
            IN
        )

    if (
        "switch in" in nature
        or "switch-in" in nature
        or "lateral shift in" in nature
    ):

        return (
            "SWITCH",
            "SWITCH_IN",
            IN
        )

    if (
        "switch out" in nature
        or "switch-out" in nature
        or "lateral shift out" in nature
    ):

        return (
            "SWITCH",
            "SWITCH_OUT",
            OUT
        )

    if "purchase" in nature:

        return (
            "PURCHASE",
            "PURCHASE",
            IN
        )

    if (
        "redemption" in nature
        or "redeem" in nature
    ):

        return (
            "REDEMPTION",
            "REDEMPTION",
            OUT
        )

    if "dividend" in nature:

        if (
            "reinvest" in nature
            or "reinvestment" in nature
        ):

            return (
                "DIVIDEND",
                "DIVIDEND_REINVESTMENT",
                IN
            )

        return (
            "DIVIDEND",
            "DIVIDEND_PAYOUT",
            NONE
        )

    # =================================================
    # UNKNOWN
    # =================================================

    return (
        "OTHER",
        "UNMAPPED",
        NONE
    )


# =====================================================
# CLASSIFY ALL TRANSACTIONS
# =====================================================

def classify_transactions(df):

    results = []

    for _, row in df.iterrows():

        txn_type, transaction_sub_type, direction = (
            classify_transaction_row(row)
        )

        results.append(
            {
                "txn_type": txn_type,
                "transaction_sub_type": transaction_sub_type,
                "transaction_direction": direction,
            }
        )

    if not results:

        return pd.DataFrame(
            columns=[
                "txn_type",
                "transaction_sub_type",
                "transaction_direction",
            ],
            index=df.index,
        )

    return pd.DataFrame(
        results,
        index=df.index
    )


# =====================================================
# UNMAPPED CODE REPORT
# =====================================================

def report_unmapped_codes(df, limit=30):

    if (
        "txn_type" not in df.columns
        or "trxntype" not in df.columns
    ):

        return

    unmapped = df.loc[
        df["transaction_sub_type"].eq("UNMAPPED"),
        "trxntype"
    ]

    if unmapped.empty:

        print(
            "Transaction classification: "
            "all raw codes classified."
        )

        return

    counts = (
        unmapped
        .map(normalize_transaction_code)
        .value_counts()
    )

    print("=" * 80)
    print(
        "UNMAPPED TRANSACTION CODES : "
        f"{len(counts)} distinct, "
        f"{int(counts.sum())} rows"
    )
    print("=" * 80)

    for code, count in counts.head(limit).items():

        print(
            f"  {code or '(blank)'} : {count}"
        )

    print("=" * 80)


# =====================================================
# TRANSFORM GOLD TRANSACTIONS
# =====================================================

def transform_transactions(df):

    print("=" * 80)
    print("TRANSFORMING GOLD TRANSACTIONS")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    original_row_count = len(df)

    gold_df = pd.DataFrame(
        index=df.index
    )

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
    # TRANSACTION CLASSIFICATION
    #
    # txn_type             = normalized transaction type
    # txn_type_raw         = raw trxntype
    # transaction_sub_type = normalized subtype
    # transaction_direction= IN / OUT / NONE
    # =================================================

    classified = classify_transactions(df)

    gold_df["txn_type"] = (
        classified["txn_type"]
        .astype("string")
    )

    gold_df["transaction_sub_type"] = (
        classified["transaction_sub_type"]
        .astype("string")
    )

    gold_df["transaction_direction"] = (
        classified["transaction_direction"]
        .astype("string")
    )

    # =================================================
    # RAW TRANSACTION TYPE
    # =================================================

    gold_df["txn_type_raw"] = (
        df["trxntype"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # =================================================
    # DESCRIPTION
    # =================================================

    gold_df["txn_desc"] = (
        df["trxn_nature"]
        .astype("string")
        .str.strip()
    )

    # =================================================
    # RAW TRANSACTION SUB TYPE
    # =================================================

    gold_df["txn_sub_type"] = (
        df["trxnsubtyp"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df.loc[
        gold_df["txn_sub_type"] == "",
        "txn_sub_type"
    ] = None

    # =================================================
    # UNMAPPED REPORT
    # =================================================

    report_unmapped_codes(
        gold_df.assign(
            trxntype=df["trxntype"]
        )
    )

    # =================================================
    # DATES
    # =================================================

    gold_df["txn_date"] = pd.to_datetime(
        df["traddate"],
        errors="coerce",
        dayfirst=True
    ).dt.date

    gold_df["post_date"] = pd.to_datetime(
        df["postdate"],
        errors="coerce",
        dayfirst=True
    ).dt.date

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
    # ISIN
    # =================================================

    gold_df["isin"] = (
        df["isin"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    gold_df.loc[
        gold_df["isin"] == "",
        "isin"
    ] = None

    # =================================================
    # SOURCE
    # =================================================

    gold_df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # =================================================
    # SCHEME CODE
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

    gold_df["scheme_code"] = prodcode

    # =================================================
    # SCHEME ID
    # =================================================

    print("=" * 80)
    print("MAPPING SCHEME ID")
    print("=" * 80)

    gold_df["scheme_id"] = (
        df["scheme_id"]
        .astype("string")
        .str.strip()
        .replace(
            "",
            pd.NA
        )
    )

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

    print("Sample Gold scheme IDs:")

    print(
        gold_df["scheme_id"]
        .dropna()
        .head(20)
        .tolist()
    )

    # =================================================
    # APP MANAGED COLUMNS
    # =================================================

    gold_df["client_id"] = None

    gold_df["amc_id"] = None

    gold_df["rta_txn_id"] = None

    gold_df["arn_id"] = None

    gold_df["sip_id"] = None

    gold_df["source_file_id"] = None

    # =================================================
    # CREATED AT
    # =================================================

    gold_df["created_at"] = (
        pd.Timestamp.now(tz="UTC")
        .tz_localize(None)
    )

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
            "transaction_sub_type",
            "transaction_direction",
            "txn_sub_type",

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
            "isin",

            "client_id",
            "amc_id",
            "scheme_id",

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
    #
    # Only transaction identity is required.
    # No date filtering.
    # =================================================

    gold_df = gold_df.dropna(
        subset=[
            "rta",
            "rta_txn_no"
        ]
    )

    # =================================================
    # STRING LENGTHS
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

    gold_df["transaction_sub_type"] = (
        gold_df["transaction_sub_type"]
        .astype("string")
        .str[:50]
    )

    gold_df["transaction_direction"] = (
        gold_df["transaction_direction"]
        .astype("string")
        .str[:10]
    )

    gold_df["txn_sub_type"] = (
        gold_df["txn_sub_type"]
        .astype("string")
        .str[:30]
    )

    gold_df["arn"] = (
        gold_df["arn"]
        .astype("string")
        .str[:20]
    )

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .astype("string")
        .str[:50]
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

    gold_df["isin"] = (
        gold_df["isin"]
        .astype("string")
        .str[:20]
    )

    # =================================================
    # DATE CHECK
    # =================================================

    print("=" * 80)
    print("DATE CHECK")
    print("=" * 80)

    print(
        "Transaction dates present:",
        gold_df["txn_date"].notna().sum()
    )

    print(
        "Transaction dates missing:",
        gold_df["txn_date"].isna().sum()
    )

    print(
        "Post dates present:",
        gold_df["post_date"].notna().sum()
    )

    print(
        "Post dates missing:",
        gold_df["post_date"].isna().sum()
    )

    # =================================================
    # CLASSIFICATION CHECK
    # =================================================

    print("=" * 80)
    print("TRANSACTION CLASSIFICATION CHECK")
    print("=" * 80)

    print()
    print("Transaction Types:")

    print(
        gold_df["txn_type"]
        .value_counts(dropna=False)
    )

    print()
    print("Transaction Sub Types:")

    print(
        gold_df["transaction_sub_type"]
        .value_counts(dropna=False)
        .head(50)
    )

    print()
    print("Transaction Directions:")

    print(
        gold_df["transaction_direction"]
        .value_counts(dropna=False)
    )

    # =================================================
    # ROW COUNT
    # =================================================

    print("=" * 80)
    print("TRANSFORM COMPLETE")
    print("=" * 80)

    print(
        "Input rows:",
        original_row_count
    )

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

        print("No new records found.")

        return True

    # =================================================
    # FINAL SCHEME ID CHECK
    # =================================================

    print(
        "Python scheme_id dtype:",
        gold_df["scheme_id"].dtype
    )

    print("Scheme IDs before INSERT:")

    print(
        gold_df["scheme_id"]
        .dropna()
        .head(10)
        .tolist()
    )

    # =================================================
    # NO EXISTING GOLD QUERY
    # =================================================

    print()
    print("Duplicate filtering: DISABLED")

    print(
        "Timestamp-based incremental loading: ENABLED"
    )

    print(
        "Rows to insert:",
        len(gold_df)
    )

    # =================================================
    # INSERT
    # =================================================

    try:

        print(
            f"Inserting {len(gold_df)} rows..."
        )

        from utils.db import upsert_dataframe

        upsert_dataframe(
            gold_df,
            schema="gold",
            table="transactions",
            conflict_columns=[
                "rta",
                "rta_txn_no",
                "folio_number",
                "amount",
                "units"
            ],
            chunksize=500,
            updated_at_column=None,
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

    try:

        # =================================================
        # EXTRACT
        # =================================================

        df = extract_transactions()

        if df.empty:

            print()
            print(
                "No new transaction records found."
            )

            print(
                "GOLD TRANSACTION ETL COMPLETED - "
                "NOTHING NEW TO LOAD"
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
                        "scheme_code",
                        "txn_type",
                        "txn_type_raw",
                        "txn_desc",
                        "transaction_sub_type",
                        "transaction_direction",
                        "txn_sub_type",
                        "txn_date",
                        "post_date",
                        "created_at"
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

    except Exception as e:

        print("=" * 80)
        print("GOLD TRANSACTION ETL FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e).splitlines()[0]
        )

        traceback.print_exc(
            limit=10
        )

        print("=" * 80)