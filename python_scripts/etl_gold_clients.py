import pandas as pd
import traceback

from datetime import datetime
from utils.db import engine, master_engine


# ============================================================
# SAFE READ
# ============================================================

def safe_read(query, connection=engine):

    try:

        return pd.read_sql(
            query,
            connection
        )

    except Exception as e:

        print("SQL ERROR :", e)

        traceback.print_exc(limit=5)

        return pd.DataFrame()


# ============================================================
# CLEAN STRING
# ============================================================

def clean_string(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .replace(
            [
                "",
                "NAN",
                "NONE",
                "NULL",
                "NAT"
            ],
            pd.NA
        )
    )


# ============================================================
# CLEAN PAN
# ============================================================

def clean_pan(series):

    return (
        clean_string(series)
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


# ============================================================
# CLEAN PHONE
# ============================================================

def clean_phone(series):

    raw = clean_string(series)

    raw = (
        raw
        .str.split(",")
        .str[0]
        .str.strip()
    )

    invalid_alpha = raw.str.contains(
        r"[A-Za-z]",
        regex=True,
        na=False
    )

    raw.loc[invalid_alpha] = pd.NA

    raw = (
        raw
        .str.replace(
            r"\D",
            "",
            regex=True
        )
    )

    raw = raw.str[:20]

    return raw.replace("", pd.NA)


# ============================================================
# NORMALIZE MOBILE
# ============================================================

def normalize_mobile(series):

    raw = clean_string(series)

    digits = (
        raw
        .fillna("")
        .astype(str)
        .str.replace(
            r"\D",
            "",
            regex=True
        )
    )

    mobile = pd.Series(
        pd.NA,
        index=series.index,
        dtype="object"
    )

    mobile_isd = pd.Series(
        pd.NA,
        index=series.index,
        dtype="object"
    )

    # 91 + 10 digits

    mask_91 = (
        (digits.str.len() == 12)
        &
        digits.str.startswith("91")
    )

    mobile.loc[mask_91] = (
        digits.loc[mask_91].str[-10:]
    )

    mobile_isd.loc[mask_91] = "91"

    # 10 digits

    mask_10 = (
        digits.str.len() == 10
    )

    mobile.loc[mask_10] = (
        digits.loc[mask_10]
    )

    mobile_isd.loc[mask_10] = "91"

    # International

    mask_other = (
        (digits.str.len() > 10)
        &
        (~mask_91)
    )

    mobile.loc[mask_other] = (
        digits.loc[mask_other].str[-10:]
    )

    mobile_isd.loc[mask_other] = (
        digits.loc[mask_other]
        .str[:-10]
        .str[-5:]
    )

    return mobile, mobile_isd


# ============================================================
# EXTRACT CLIENTS
# ============================================================

def extract_clients():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENTS")
    print("=" * 80)

    query = """

    WITH transaction_ranked AS
    (
        SELECT

            folio_no,
            source,
            pan,
            traddate,
            common_account_number,
            brokcode,
            src_brk_code,
            created_at,

            ROW_NUMBER() OVER
            (
                PARTITION BY
                    UPPER(TRIM(source)),
                    REGEXP_REPLACE(
                        TRIM(CAST(folio_no AS TEXT)),
                        '\\.0$',
                        ''
                    )

                ORDER BY
                    created_at DESC NULLS LAST,
                    traddate DESC NULLS LAST

            ) AS rn

        FROM silver.transaction_master_new

        WHERE pan IS NOT NULL
          AND TRIM(pan) <> ''
    ),

    transaction_data AS
    (
        SELECT

            folio_no,
            source,
            pan,
            traddate,
            common_account_number,
            brokcode,
            src_brk_code,
            created_at

        FROM transaction_ranked

        WHERE rn = 1
    ),

    sip_ranked AS
    (
        SELECT

            folio_no,
            source,
            pan,
            created_at,

            ROW_NUMBER() OVER
            (
                PARTITION BY
                    UPPER(TRIM(source)),
                    REGEXP_REPLACE(
                        TRIM(CAST(folio_no AS TEXT)),
                        '\\.0$',
                        ''
                    )

                ORDER BY
                    created_at DESC NULLS LAST

            ) AS rn

        FROM silver.sip_master_new

        WHERE pan IS NOT NULL
          AND TRIM(pan) <> ''
    ),

    sip_data AS
    (
        SELECT

            folio_no,
            source,
            pan,
            created_at

        FROM sip_ranked

        WHERE rn = 1
    )

    SELECT

        i.*,

        t.pan AS txn_pan,

        t.traddate AS txn_traddate,

        t.common_account_number
            AS txn_common_account_number,

        t.brokcode AS txn_brokcode,

        t.src_brk_code AS txn_src_brk_code,

        t.created_at AS txn_created_at,

        s.pan AS sip_pan,

        s.created_at AS sip_created_at

    FROM silver.investor_master i

    LEFT JOIN transaction_data t

        ON REGEXP_REPLACE(
            TRIM(CAST(i.folio_no AS TEXT)),
            '\\.0$',
            ''
        )
        =
        REGEXP_REPLACE(
            TRIM(CAST(t.folio_no AS TEXT)),
            '\\.0$',
            ''
        )

        AND UPPER(TRIM(i.source))
            =
            UPPER(TRIM(t.source))

    LEFT JOIN sip_data s

        ON REGEXP_REPLACE(
            TRIM(CAST(i.folio_no AS TEXT)),
            '\\.0$',
            ''
        )
        =
        REGEXP_REPLACE(
            TRIM(CAST(s.folio_no AS TEXT)),
            '\\.0$',
            ''
        )

        AND UPPER(TRIM(i.source))
            =
            UPPER(TRIM(s.source))

    """

    df = safe_read(query)

    if df.empty:

        print("No client data extracted.")

        return df

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    print("\nExtraction Completed")
    print("-" * 80)

    print(
        "Rows fetched :",
        len(df)
    )

    print(
        "Transaction PAN mapped :",
        df["txn_pan"].notna().sum()
    )

    print(
        "SIP PAN mapped :",
        df["sip_pan"].notna().sum()
    )

    print(
        "Investor PAN available :",
        df["pan_no"].notna().sum()
    )

    return df


# ============================================================
# TRANSFORM CLIENTS
# ============================================================

def transform_clients(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENTS")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    df = df.copy()

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    # ========================================================
    # SOURCE
    # ========================================================

    df["source"] = (
        clean_string(df["source"])
        .str.upper()
        .str.strip()
    )

    cams_mask = df["source"] == "CAMS"

    kfin_mask = df["source"].isin(
        [
            "KFIN",
            "KFINTECH"
        ]
    )

    # ========================================================
    # PAN
    # ========================================================

    df["pan_no"] = clean_pan(
        df["pan_no"]
    )

    df["txn_pan"] = clean_pan(
        df["txn_pan"]
    )

    df["sip_pan"] = clean_pan(
        df["sip_pan"]
    )

    df["pan"] = (
        df["pan_no"]
        .fillna(df["txn_pan"])
        .fillna(df["sip_pan"])
    )

    print("\nPAN Statistics")
    print("-" * 80)

    print(
        "Investor PAN    :",
        df["pan_no"].notna().sum()
    )

    print(
        "Transaction PAN :",
        df["txn_pan"].notna().sum()
    )

    print(
        "SIP PAN         :",
        df["sip_pan"].notna().sum()
    )

    print(
        "Final PAN       :",
        df["pan"].notna().sum()
    )

    print(
        "Missing PAN     :",
        df["pan"].isna().sum()
    )

    # ========================================================
    # CREATE GOLD DATAFRAME
    # ========================================================

    gold = pd.DataFrame(
        index=df.index
    )

    # ========================================================
    # BASIC
    # ========================================================

    gold["status"] = None

    gold["full_name"] = clean_string(
        df["investor_name"]
    )

    gold["client_label"] = None

    # ========================================================
    # PHONE
    # ========================================================

    if "phone_res" in df.columns:

        phone_res = clean_phone(
            df["phone_res"]
        )

    else:

        phone_res = pd.Series(
            pd.NA,
            index=df.index
        )

    if "phone_off" in df.columns:

        phone_off = clean_phone(
            df["phone_off"]
        )

    else:

        phone_off = pd.Series(
            pd.NA,
            index=df.index
        )

    gold["phone"] = (
        phone_res
        .fillna(phone_off)
    )

    # ========================================================
    # MOBILE
    # ========================================================

    if "mobile_no" in df.columns:

        (
            gold["mobile"],
            gold["mobile_isd"]
        ) = normalize_mobile(
            df["mobile_no"]
        )

    else:

        gold["mobile"] = pd.NA
        gold["mobile_isd"] = pd.NA

    # ========================================================
    # WHATSAPP
    # ========================================================

    gold["whatsapp_same_as_mobile"] = None
    gold["whatsapp_isd"] = None
    gold["whatsapp_no"] = None

    # ========================================================
    # AADHAAR
    # ========================================================

    gold["aadhaar"] = pd.NA

    if "holder_1_aadhaar_info" in df.columns:

        aadhaar_info = clean_string(
            df["holder_1_aadhaar_info"]
        )

        gold.loc[
            aadhaar_info.notna(),
            "aadhaar"
        ] = "Y"

    # ========================================================
    # PAN
    # ========================================================

    gold["pan"] = df["pan"]

    gold["pan_verified"] = False

    gold["pan_verified_at"] = None

    # ========================================================
    # EMAIL
    # ========================================================

    if "email" in df.columns:

        gold["email"] = clean_string(
            df["email"]
        )

    else:

        gold["email"] = None

    # ========================================================
    # DOB
    # ========================================================

    if "dob" in df.columns:

        gold["date_of_birth"] = (
            pd.to_datetime(
                df["dob"],
                errors="coerce"
            )
            .dt.date
        )

    else:

        gold["date_of_birth"] = pd.NaT

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["marital_status"] = None
    gold["anniversary_date"] = None
    gold["blood_group"] = None
    gold["equity_ucc"] = None

    # ========================================================
    # CAN
    # ========================================================

    gold["can"] = None

    if "commonaccno" in df.columns:

        gold.loc[
            kfin_mask,
            "can"
        ] = clean_string(
            df.loc[
                kfin_mask,
                "commonaccno"
            ]
        )

    if "txn_common_account_number" in df.columns:

        missing_can = (
            kfin_mask
            &
            gold["can"].isna()
        )

        gold.loc[
            missing_can,
            "can"
        ] = clean_string(
            df.loc[
                missing_can,
                "txn_common_account_number"
            ]
        )

    # ========================================================
    # OCCUPATION
    # ========================================================

    gold["occupation"] = None

    if "occupation" in df.columns:

        gold.loc[
            cams_mask,
            "occupation"
        ] = clean_string(
            df.loc[
                cams_mask,
                "occupation"
            ]
        )

    if "occupation_description" in df.columns:

        gold.loc[
            kfin_mask,
            "occupation"
        ] = clean_string(
            df.loc[
                kfin_mask,
                "occupation_description"
            ]
        )

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["user_id"] = None
    gold["family_id"] = None
    gold["family_relation"] = None
    gold["gender"] = None

    # ========================================================
    # INVESTOR TYPE
    # ========================================================

    investor_type_source = pd.Series(
        pd.NA,
        index=df.index,
        dtype="object"
    )

    if "tax_status" in df.columns:

        investor_type_source.loc[
            cams_mask
        ] = clean_string(
            df.loc[
                cams_mask,
                "tax_status"
            ]
        )

    if "statusdesc" in df.columns:

        investor_type_source.loc[
            kfin_mask
        ] = clean_string(
            df.loc[
                kfin_mask,
                "statusdesc"
            ]
        )

    if "categorydesc" in df.columns:

        missing_type = (
            kfin_mask
            &
            investor_type_source.isna()
        )

        investor_type_source.loc[
            missing_type
        ] = clean_string(
            df.loc[
                missing_type,
                "categorydesc"
            ]
        )

    def derive_investor_type(value):

        if pd.isna(value):

            return pd.NA

        value = (
            str(value)
            .upper()
            .strip()
        )

        if "HUF" in value:
            return "HUF"

        if "NRI" in value:
            return "NRI"

        if "TRUST" in value:
            return "TRUST"

        if "INDIVID" in value:
            return "INDIVIDUAL"

        return pd.NA

    gold["investor_type"] = (
        investor_type_source
        .apply(derive_investor_type)
    )

    # ========================================================
    # TAX STATUS
    # ========================================================

    if "tax_status" in df.columns:

        gold["tax_status"] = clean_string(
            df["tax_status"]
        )

    else:

        gold["tax_status"] = None

    # ========================================================
    # KYC
    # ========================================================

    gold["kyc_status"] = pd.NA

    if "ckyc_no" in df.columns:

        cams_ckyc = clean_string(
            df["ckyc_no"]
        )

        gold.loc[
            cams_mask,
            "kyc_status"
        ] = "Not Verified"

        gold.loc[
            cams_mask & cams_ckyc.notna(),
            "kyc_status"
        ] = "Verified"

    if "kyc1flag" in df.columns:

        kfin_kyc = (
            clean_string(
                df["kyc1flag"]
            )
            .fillna("")
            .astype(str)
            .str.upper()
            .str.strip()
        )

        kfin_verified = kfin_kyc.isin(
            [
                "Y",
                "YES",
                "1",
                "TRUE",
                "VERIFIED"
            ]
        )

        gold.loc[
            kfin_mask,
            "kyc_status"
        ] = "Not Verified"

        gold.loc[
            kfin_mask & kfin_verified,
            "kyc_status"
        ] = "Verified"

    # ========================================================
    # APP MANAGED
    # ========================================================

    gold["risk_profile"] = None
    gold["rm_id"] = None
    gold["branch_id"] = None

    # ========================================================
    # ARN
    # ========================================================

    gold["arn"] = (
        clean_string(
            df["txn_brokcode"]
        )
        .str.upper()
        .str.strip()
        .replace("", pd.NA)
        .str[:50]
    )

    # ========================================================
    # SUB ARN
    # ========================================================

    gold["sub_arn"] = (
        clean_string(
            df["txn_src_brk_code"]
        )
        .str.upper()
        .str.strip()
        .replace("", pd.NA)
        .str[:50]
    )

    print("\nARN Mapping")
    print("-" * 80)

    print(
        "ARN values found      :",
        gold["arn"].notna().sum()
    )

    print(
        "ARN values missing    :",
        gold["arn"].isna().sum()
    )

    print(
        "Sub ARN values found  :",
        gold["sub_arn"].notna().sum()
    )

    print(
        "Sub ARN values missing:",
        gold["sub_arn"].isna().sum()
    )

    # ========================================================
    # ARN ID
    # ========================================================

    gold["arn_id"] = None

    broker_code = clean_string(
        df["txn_brokcode"]
    )

    if broker_code.notna().any():

        arn_lookup = safe_read(
            """
            SELECT
                arn_code,
                id AS arn_id
            FROM arn
            WHERE arn_code IS NOT NULL
              AND TRIM(arn_code) <> ''
              AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            master_engine
        )

        if not arn_lookup.empty:

            arn_lookup["arn_code"] = (
                arn_lookup["arn_code"]
                .astype(str)
                .str.strip()
                .str.upper()
            )

            arn_lookup = (
                arn_lookup
                .drop_duplicates(
                    subset=["arn_code"]
                )
                .set_index("arn_code")["arn_id"]
            )

            gold["arn_id"] = (
                broker_code
                .astype("string")
                .str.strip()
                .str.upper()
                .map(arn_lookup)
            )

    print("\nARN ID Mapping")
    print("-" * 80)

    print(
        "Broker codes found :",
        broker_code.notna().sum()
    )

    print(
        "ARN IDs mapped     :",
        gold["arn_id"].notna().sum()
    )

    print(
        "ARN IDs missing    :",
        gold["arn_id"].isna().sum()
    )

    # ========================================================
    # ONBOARDED AT
    # ========================================================

    gold["onboarded_at"] = pd.NaT

    if "folio_date" in df.columns:

        folio_date = pd.to_datetime(
            df["folio_date"],
            errors="coerce"
        )

        gold.loc[
            cams_mask,
            "onboarded_at"
        ] = folio_date.loc[
            cams_mask
        ]

    if "txn_traddate" in df.columns:

        txn_date = pd.to_datetime(
            df["txn_traddate"],
            errors="coerce"
        )

        gold.loc[
            kfin_mask,
            "onboarded_at"
        ] = txn_date.loc[
            kfin_mask
        ]

    gold["onboarded_at"] = (
        pd.to_datetime(
            gold["onboarded_at"],
            errors="coerce"
        )
        .dt.date
    )

    # ========================================================
    # SOURCE
    # ========================================================

    gold["source"] = clean_string(
        df["source"]
    )

    # ========================================================
    # CREATED AT
    # ========================================================

    gold["created_at"] = datetime.now()

    # ========================================================
    # FINAL COLUMN ORDER
    # ========================================================

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
            "arn",
            "sub_arn",
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

    print("\nTransformation Completed")
    print("-" * 80)

    print(
        "Gold rows generated :",
        len(gold)
    )

    return gold

# ============================================================
# LOAD CLIENTS INTO DATABASE
# ============================================================

def load_clients(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.CLIENTS")
    print("=" * 80)

    if gold_df.empty:
        print("No client rows generated.")
        return False

    gold_df = gold_df.copy()

    # ========================================================
    # CLEAN PAN
    # ========================================================

    gold_df["pan"] = clean_pan(
        gold_df["pan"]
    )

    # ========================================================
    # REMOVE NULL PAN
    # ========================================================

    before = len(gold_df)

    gold_df = gold_df[
        gold_df["pan"].notna()
    ].copy()

    print(
        "Rows without PAN removed:",
        before - len(gold_df)
    )

    # ========================================================
    # REMOVE DUPLICATES INSIDE CURRENT BATCH
    # ========================================================

    before = len(gold_df)

    gold_df = (
        gold_df
        .drop_duplicates(
            subset=["pan"],
            keep="last"
        )
        .copy()
    )

    print(
        "Duplicate PAN rows removed:",
        before - len(gold_df)
    )

    print(
        "Unique client rows:",
        len(gold_df)
    )

    if gold_df.empty:

        print(
            "No valid client records to load."
        )

        return True

    # ========================================================
    # GET EXISTING PANS FROM DATABASE
    # ========================================================

    print()
    print("Checking existing clients in gold.clients...")

    existing = safe_read(
        """
        SELECT
            pan
        FROM gold.clients
        WHERE pan IS NOT NULL
        """
    )

    if not existing.empty:

        existing["pan"] = clean_pan(
            existing["pan"]
        )

        existing_pans = set(
            existing["pan"]
            .dropna()
            .tolist()
        )

        before = len(gold_df)

        gold_df = gold_df[
            ~gold_df["pan"].isin(
                existing_pans
            )
        ].copy()

        print(
            "Existing client rows skipped:",
            before - len(gold_df)
        )

    print(
        "Rows actually going to database:",
        len(gold_df)
    )

    # ========================================================
    # NOTHING NEW
    # ========================================================

    if gold_df.empty:

        print()
        print(
            "All client records already exist in gold.clients."
        )

        count_df = safe_read(
            """
            SELECT COUNT(*) AS total_clients
            FROM gold.clients
            """
        )

        if not count_df.empty:

            print(
                "Current Gold Clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        return True

    # ========================================================
    # CHECK TABLE COLUMNS
    # ========================================================

    print()
    print("Checking gold.clients table columns...")

    table_columns = safe_read(
        """
        SELECT
            column_name
        FROM information_schema.columns
        WHERE table_schema = 'gold'
          AND table_name = 'clients'
        ORDER BY ordinal_position
        """
    )

    if table_columns.empty:

        print(
            "ERROR: gold.clients table was not found."
        )

        return False

    database_columns = set(
        table_columns["column_name"]
    )

    missing_database_columns = [
        col
        for col in gold_df.columns
        if col not in database_columns
    ]

    if missing_database_columns:

        print()
        print(
            "ERROR: These columns exist in the DataFrame "
            "but not in gold.clients:"
        )

        for col in missing_database_columns:
            print(
                " -",
                col
            )

        return False

    # ========================================================
    # KEEP ONLY DATABASE COLUMNS
    # ========================================================

    gold_df = gold_df[
        [
            col
            for col in gold_df.columns
            if col in database_columns
        ]
    ].copy()

    # ========================================================
    # INSERT
    #
    # NOTE: pandas NULL -> database NULL conversion and chunking are both
    # handled inside upsert_dataframe() itself (it takes a chunksize
    # parameter and applies astype(object).where(pd.notnull(...), None)
    # right before building the insert records), so no pre-conversion or
    # manual batching loop is needed here.
    # ========================================================

    print()
    print("Starting database insert...")

    inserted_rows = 0

    try:

        from utils.db import upsert_dataframe

        inserted_rows = upsert_dataframe(
            gold_df,
            schema="gold",
            table="clients",
            conflict_columns=["pan"],
            chunksize=100,
            # gold.clients has no updated_at (or equivalent) column.
            updated_at_column=None,
        )

        print(
            f"Inserted {inserted_rows} / "
            f"{len(gold_df)} rows"
        )

        # ====================================================
        # VERIFY DATABASE
        # ====================================================

        print()
        print("=" * 80)
        print("VERIFYING GOLD.CLIENTS")
        print("=" * 80)

        count_df = safe_read(
            """
            SELECT
                COUNT(*) AS total_clients
            FROM gold.clients
            """
        )

        if not count_df.empty:

            total_clients = int(
                count_df.iloc[0]["total_clients"]
            )

            print(
                "Total rows in gold.clients:",
                total_clients
            )

        # ====================================================
        # SHOW ACTUAL DATABASE DATA
        # ====================================================

        database_preview = safe_read(
            """
            SELECT
                pan,
                full_name,
                phone,
                mobile,
                arn,
                sub_arn,
                email,
                date_of_birth,
                investor_type,
                tax_status,
                kyc_status,
                arn_id,
                onboarded_at,
                source,
                created_at
            FROM gold.clients
            ORDER BY created_at DESC
            LIMIT 10
            """
        )

        print()
        print(
            "Latest 10 rows actually stored "
            "in PostgreSQL gold.clients:"
        )

        if database_preview.empty:

            print(
                "WARNING: Database returned 0 rows."
            )

        else:

            print(
                database_preview.to_string(
                    index=False
                )
            )

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT SUCCESSFUL")
        print("=" * 80)

        return True

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD.CLIENTS DATABASE INSERT FAILED")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        # IMPORTANT:
        # Print only the actual error.
        # Do NOT print the complete SQLAlchemy parameter list.

        error_text = str(e)

        print(
            "Database error:"
        )

        print(
            error_text[:3000]
        )

        print()
        print(
            "Rows successfully inserted before failure:",
            inserted_rows
        )

        print(
            "Rows remaining:",
            len(gold_df) - inserted_rows
        )

        print()
        print(
            "The transaction has been rolled back."
        )

        return False

#Main Function#

def main():

    print("=" * 80)
    print("STARTING GOLD CLIENTS ETL")
    print("=" * 80)

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        clients_df = extract_clients()

        if clients_df.empty:

            print(
                "No client data found."
            )

            return False

        # ====================================================
        # TRANSFORM
        # ====================================================

        gold_clients = transform_clients(
            clients_df
        )

        if gold_clients.empty:

            print(
                "No Gold client records generated."
            )

            return False

        # ====================================================
        # LOAD
        # ====================================================

        status = load_clients(
            gold_clients
        )

        # ====================================================
        # FINAL STATUS
        # ====================================================

        if status:

            print()
            print("=" * 80)
            print(
                "GOLD CLIENTS ETL COMPLETED SUCCESSFULLY"
            )
            print("=" * 80)

            return True

        else:

            print()
            print("=" * 80)
            print(
                "GOLD CLIENTS ETL FAILED"
            )
            print("=" * 80)

            return False

    except Exception as e:

        print()
        print("=" * 80)
        print("GOLD CLIENTS ETL ERROR")
        print("=" * 80)

        print(
            "Error type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)[:3000]
        )

        traceback.print_exc()

        return False


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()