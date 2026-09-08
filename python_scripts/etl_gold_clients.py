import pandas as pd
import traceback

from datetime import datetime, timezone
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

    # ========================================================
    # 91 + 10 DIGITS
    # ========================================================

    mask_91 = (
        (digits.str.len() == 12)
        &
        digits.str.startswith("91")
    )

    mobile.loc[mask_91] = (
        digits.loc[mask_91].str[-10:]
    )

    mobile_isd.loc[mask_91] = "91"

    # ========================================================
    # 10 DIGITS
    # ========================================================

    mask_10 = (
        digits.str.len() == 10
    )

    mobile.loc[mask_10] = (
        digits.loc[mask_10]
    )

    mobile_isd.loc[mask_10] = "91"

    # ========================================================
    # INTERNATIONAL
    # ========================================================

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
# GET CKYC / DP ID LOOKUP
#
# IMPORTANT:
# Mapping is done using PAN.
#
# silver.investor_master
#        |
#        | PAN
#        v
# ckyc_no / dp_id
# ============================================================

def get_ckyc_dp_lookup():

    print()
    print("=" * 80)
    print("BUILDING PAN -> CKYC / DP ID LOOKUP")
    print("=" * 80)

    query = """

    SELECT
        pan_no,
        ckyc_no,
        dp_id
    FROM silver.investor_master
    WHERE pan_no IS NOT NULL
      AND TRIM(pan_no) <> ''

    """

    lookup = safe_read(query)

    if lookup.empty:

        print(
            "No CKYC / DP ID records found "
            "in silver.investor_master."
        )

        return pd.DataFrame(
            columns=[
                "pan",
                "ckyc_no",
                "dp_id"
            ]
        )

    # ========================================================
    # CLEAN PAN
    # ========================================================

    lookup["pan"] = clean_pan(
        lookup["pan_no"]
    )

    # ========================================================
    # CLEAN CKYC / DP ID
    # ========================================================

    lookup["ckyc_no"] = clean_string(
        lookup["ckyc_no"]
    )

    lookup["dp_id"] = clean_string(
        lookup["dp_id"]
    )

    lookup = lookup[
        lookup["pan"].notna()
    ].copy()

    # ========================================================
    # SORT SO THAT RECORDS HAVING CKYC / DP ID
    # ARE PREFERRED
    # ========================================================

    lookup["_has_ckyc"] = (
        lookup["ckyc_no"].notna()
    )

    lookup["_has_dp_id"] = (
        lookup["dp_id"].notna()
    )

    lookup = lookup.sort_values(
        by=[
            "pan",
            "_has_ckyc",
            "_has_dp_id"
        ],
        ascending=[
            True,
            False,
            False
        ]
    )

    # ========================================================
    # ONE RECORD PER PAN
    # ========================================================

    lookup = (
        lookup
        .drop_duplicates(
            subset=["pan"],
            keep="first"
        )
        [
            [
                "pan",
                "ckyc_no",
                "dp_id"
            ]
        ]
    )

    print(
        "Unique PANs in CKYC / DP ID lookup:",
        len(lookup)
    )

    print(
        "PANs having CKYC:",
        lookup["ckyc_no"].notna().sum()
    )

    print(
        "PANs having DP ID:",
        lookup["dp_id"].notna().sum()
    )

    return lookup


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

    # ========================================================
    # FINAL PAN
    #
    # silver.investor_master.pan_no  ->  gold.clients.pan
    #
    # Straight across, with no transaction or SIP fallback.
    #
    # Those fallbacks were how a guardian's PAN ended up in the
    # pan column: CAMS puts the guardian's PAN into the ordinary
    # transaction `pan` field with no flag at all, so a folio
    # with no PAN of its own silently picked it up and the minor
    # was stored as though the PAN were theirs. A folio's own
    # PAN is the one the registry recorded against the investor,
    # and that is pan_no.
    # ========================================================

    df["pan"] = clean_pan(df["pan_no"])

    # ========================================================
    # GUARDIAN PAN
    #
    # Carried through as an attribute only. Deciding WHICH
    # client a folio belongs to -- including the guardian-PAN
    # rule for minors -- is not done here: that lives in
    # python_scripts/client_mapping.py and is recorded in
    # bronze.client_mapping_review.
    # ========================================================

    if "guardian_pan" in df.columns:

        df["guardian_pan"] = clean_pan(df["guardian_pan"])

    else:

        df["guardian_pan"] = pd.NA

    # A guardian's PAN belongs in guardian_pan, never in pan.
    #
    # CAMS puts the guardian's PAN into the ordinary transaction
    # `pan` field with no flag at all, so the txn_pan fallback
    # above silently adopts it as the investor's own. Whenever
    # the resolved PAN is this folio's own guardian_pan, it is
    # not the investor's PAN and is cleared.
    borrowed = (
        df["pan"].notna()
        &
        df["guardian_pan"].notna()
        &
        (df["pan"] == df["guardian_pan"])
    )

    if borrowed.any():

        print(
            "Guardian PAN cleared from pan :",
            int(borrowed.sum())
        )

    df["pan"] = df["pan"].where(~borrowed, pd.NA)


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
    # CKYC / DP ID PAN MAPPING
    #
    # THIS IS THE IMPORTANT FIX.
    #
    # We do NOT take CKYC / DP ID simply from the
    # folio-joined investor row.
    #
    # Instead:
    #
    # final gold PAN
    #       ↓
    # silver.investor_master.pan_no
    #       ↓
    # ckyc_no / dp_id
    # ========================================================

    ckyc_dp_lookup = get_ckyc_dp_lookup()

    if not ckyc_dp_lookup.empty:

        df = df.merge(
            ckyc_dp_lookup,
            how="left",
            left_on="pan",
            right_on="pan",
            suffixes=(
                "",
                "_lookup"
            )
        )

    else:

        df["ckyc_no"] = pd.NA
        df["dp_id"] = pd.NA

    print("\nPAN Based CKYC / DP ID Mapping")
    print("-" * 80)

    print(
        "Final PANs:",
        df["pan"].notna().sum()
    )

    print(
        "CKYC mapped:",
        df["ckyc_no"].notna().sum()
    )

    print(
        "CKYC missing:",
        df["ckyc_no"].isna().sum()
    )

    print(
        "DP ID mapped:",
        df["dp_id"].notna().sum()
    )

    print(
        "DP ID missing:",
        df["dp_id"].isna().sum()
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

    gold["guardian_pan"] = df["guardian_pan"]

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
                errors="coerce",
                format="ISO8601"
            )
            .dt.date
        )

    else:

        gold["date_of_birth"] = pd.NaT

    # ========================================================
    # AGE
    # ========================================================

    if "age" in df.columns:
        gold["age"] = df["age"].astype("Int64")
    else:
        gold["age"] = pd.NA

    # ========================================================
    # IS MINOR
    # ========================================================

    if "is_minor" in df.columns:
        gold["is_minor"] = df["is_minor"].astype("boolean")
    else:
        gold["is_minor"] = pd.NA

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

    gold["occupation"] = pd.NA

    if "occupation_description" in df.columns:

        gold["occupation"] = clean_string(
            df["occupation_description"]
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
    # CKYC NO
    # ========================================================

    gold["ckyc_no"] = clean_string(
        df["ckyc_no"]
    )

    # ========================================================
    # DP ID
    # ========================================================

    gold["dp_id"] = clean_string(
        df["dp_id"]
    )

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
            errors="coerce",
            format="ISO8601"
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
            errors="coerce",
            format="ISO8601"
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
            errors="coerce",
            format="ISO8601"
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

    gold["created_at"] = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )

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
            "guardian_pan",
            "arn",
            "sub_arn",
            "email",
            "date_of_birth",
            "age",
            "is_minor",
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
            "ckyc_no",
            "dp_id",
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

    print(
        "Gold CKYC values    :",
        gold["ckyc_no"].notna().sum()
    )

    print(
        "Gold DP ID values   :",
        gold["dp_id"].notna().sum()
    )

    return gold


# ============================================================
# UPDATE EXISTING CLIENT CKYC / DP ID
# ============================================================

def update_existing_ckyc_dp(gold_df):

    print()
    print("=" * 80)
    print("UPDATING EXISTING CLIENT CKYC / DP ID")
    print("=" * 80)

    update_df = gold_df[
        gold_df["pan"].notna()
        &
        (
            gold_df["ckyc_no"].notna()
            |
            gold_df["dp_id"].notna()
        )
    ][
        [
            "pan",
            "ckyc_no",
            "dp_id"
        ]
    ].copy()

    if update_df.empty:

        print(
            "No existing clients have CKYC / DP ID values to update."
        )

        return 0

    # ========================================================
    # GET EXISTING CLIENTS
    # ========================================================

    existing = safe_read(
        """
        SELECT
            id,
            pan,
            ckyc_no,
            dp_id
        FROM gold.clients
        WHERE pan IS NOT NULL
        """
    )

    if existing.empty:

        print(
            "No existing clients found."
        )

        return 0

    existing["pan"] = clean_pan(
        existing["pan"]
    )

    # ========================================================
    # MAP GOLD DATA TO EXISTING CLIENT ID
    # ========================================================

    update_df = update_df.merge(
        existing[
            [
                "id",
                "pan",
                "ckyc_no",
                "dp_id"
            ]
        ],
        on="pan",
        how="inner",
        suffixes=(
            "_new",
            "_existing"
        )
    )

    if update_df.empty:

        print(
            "No matching existing clients found."
        )

        return 0

    # ========================================================
    # ONLY UPDATE WHEN GOLD HAS A VALUE
    # AND EXISTING VALUE IS NULL
    # ========================================================

    update_df["final_ckyc"] = (
        update_df["ckyc_no_existing"]
        .fillna(update_df["ckyc_no_new"])
    )

    update_df["final_dp_id"] = (
        update_df["dp_id_existing"]
        .fillna(update_df["dp_id_new"])
    )

    changed = update_df[
        (
            update_df["ckyc_no_existing"].isna()
            &
            update_df["ckyc_no_new"].notna()
        )
        |
        (
            update_df["dp_id_existing"].isna()
            &
            update_df["dp_id_new"].notna()
        )
    ].copy()

    if changed.empty:

        print(
            "No existing client CKYC / DP ID values required updating."
        )

        return 0

    # ========================================================
    # UPDATE DATABASE
    # ========================================================

    updated_count = 0

    try:

        with engine.begin() as connection:

            for _, row in changed.iterrows():

                query = """

                UPDATE gold.clients

                SET
                    ckyc_no = COALESCE(:ckyc_no, ckyc_no),
                    dp_id = COALESCE(:dp_id, dp_id)

                WHERE id = :id

                """

                connection.execute(
                    __import__(
                        "sqlalchemy"
                    ).text(query),
                    {
                        "ckyc_no": (
                            None
                            if pd.isna(
                                row["ckyc_no_new"]
                            )
                            else str(
                                row["ckyc_no_new"]
                            )
                        ),
                        "dp_id": (
                            None
                            if pd.isna(
                                row["dp_id_new"]
                            )
                            else str(
                                row["dp_id_new"]
                            )
                        ),
                        "id": row["id"]
                    }
                )

                updated_count += 1

        print(
            "Existing clients updated:",
            updated_count
        )

        return updated_count

    except Exception as e:

        print(
            "Existing CKYC / DP ID update failed:",
            str(e)[:3000]
        )

        traceback.print_exc()

        return 0


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
    # CLEAN VALUES
    # ========================================================

    gold_df["pan"] = clean_pan(
        gold_df["pan"]
    )

    gold_df["ckyc_no"] = clean_string(
        gold_df["ckyc_no"]
    )

    gold_df["dp_id"] = clean_string(
        gold_df["dp_id"]
    )

    # ========================================================
    # DROP ROWS WITH NO IDENTITY AT ALL
    #
    # A missing PAN is no longer a reason to discard a client.
    # A minor has no PAN of their own -- the registry records
    # only the guardian's -- so requiring one deleted every
    # minor along with their address and bank rows.
    #
    # A row still needs SOMETHING to be identified by: a PAN, or
    # failing that a name.
    # ========================================================

    before = len(gold_df)

    gold_df = gold_df[
        gold_df["pan"].notna()
        |
        gold_df["full_name"].notna()
    ].copy()

    print(
        "Rows with no identity removed:",
        before - len(gold_df)
    )

    # ========================================================
    # REMOVE DUPLICATES
    #
    # Two populations, two keys:
    #   pan present -> the PAN identifies the person
    #   pan absent  -> guardian PAN + name + date of birth does
    # ========================================================

    has_pan = gold_df["pan"].notna()

    before = len(gold_df)

    with_pan = (
        gold_df[has_pan]
        .drop_duplicates(subset=["pan"], keep="last")
        .copy()
    )

    # The same person arrives under several spellings:
    #   "Agam Singh Saini"  / "AGAM SINGH SAINI"   (case)
    #   "Dhyani N Patel"    / "DHYANI PATEL"       (middle initial)
    #
    # client_mapping normalises exactly this, so its helpers are
    # reused rather than reimplemented here -- one matcher, one
    # place to fix.
    from client_mapping import norm_name

    without_pan = gold_df[~has_pan].copy()

    # Inside one guardian's family the first name identifies the
    # child: it survives middle-name drift while still keeping
    # twins apart (NIVAA / NIVAAN).
    without_pan["_name_key"] = (
        norm_name(without_pan["full_name"])
        .fillna("")
        .str.split(" ")
        .str[0]
    )

    # With no guardian PAN there is no family to disambiguate
    # within, so the whole name is the key.
    no_guardian = without_pan["guardian_pan"].isna()

    without_pan.loc[no_guardian, "_name_key"] = (
        norm_name(without_pan.loc[no_guardian, "full_name"])
        .fillna("")
    )

    without_pan = (
        without_pan
        .drop_duplicates(
            subset=[
                "guardian_pan",
                "_name_key",
                "date_of_birth"
            ],
            keep="last"
        )
        .drop(columns=["_name_key"])
        .copy()
    )

    gold_df = pd.concat(
        [with_pan, without_pan],
        ignore_index=True
    )

    print(
        "Duplicate rows removed:",
        before - len(gold_df)
    )

    print(
        "  keyed on PAN                   :",
        len(with_pan)
    )

    print(
        "  keyed on guardian PAN + name   :",
        len(without_pan)
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
    # UPDATE EXISTING CLIENTS FIRST
    # ========================================================

    update_existing_ckyc_dp(
        gold_df
    )

    # ========================================================
    # GET EXISTING PANS
    # ========================================================

    print()
    print("Checking existing clients in gold.clients...")

    existing = safe_read(
        """
        SELECT
            pan,
            guardian_pan,
            full_name,
            date_of_birth
        FROM gold.clients
        """
    )

    if not existing.empty:

        existing_pans = set(
            clean_pan(existing["pan"]).dropna().tolist()
        )

        # PAN-less clients are recognised by the same key they
        # are de-duplicated on, so a re-run does not insert them
        # a second time.
        def person_key(guardian, name, dob):

            return (
                None if pd.isna(guardian) else str(guardian).upper(),
                None if pd.isna(name)
                else " ".join(str(name).upper().split()),
                None if pd.isna(dob) else str(dob),
            )

        existing_people = {
            person_key(row[0], row[1], row[2])
            for row in existing.loc[
                existing["pan"].isna(),
                ["guardian_pan", "full_name", "date_of_birth"]
            ].to_numpy()
        }

        # Someone who already has a client row under their own
        # PAN is not a new PAN-less client just because ONE of
        # their folios is missing that PAN. Animesh J Mehta,
        # Saleel Y Bhatt, Sureel Yogendra Bhatt and Pritipal
        # Shah each have such a folio; without this they are
        # stored twice.
        #
        # Matched with client_mapping's scorer, not string
        # equality: the PAN row reads "Saleel Yogendra Bhatt"
        # while the PAN-less folio reads "Saleel Y Bhatt".
        from client_mapping import (
            norm_name as _norm_name,
            name_match_score as _name_match_score,
            dob_conflicts as _dob_conflicts,
            NAME_MATCH_MERGE as _NAME_MATCH_MERGE,
        )

        named = existing.loc[
            existing["pan"].notna(),
            ["full_name", "date_of_birth"]
        ].copy()

        named["_norm"] = _norm_name(named["full_name"])

        existing_named = [
            (
                row[0],
                None if pd.isna(row[1]) else str(row[1]),
            )
            for row in named[["_norm", "date_of_birth"]].to_numpy()
            if row[0] is not None and not pd.isna(row[0])
        ]

        before = len(gold_df)

        def already_loaded(row):

            if pd.notna(row["pan"]):

                return row["pan"] in existing_pans

            if person_key(
                row["guardian_pan"],
                row["full_name"],
                row["date_of_birth"],
            ) in existing_people:

                return True

            # already a client under their own PAN?
            if pd.notna(row["full_name"]):

                mine = _norm_name(
                    pd.Series([row["full_name"]])
                ).iloc[0]

                if mine is None or pd.isna(mine):

                    return False

                my_dob = (
                    None if pd.isna(row["date_of_birth"])
                    else str(row["date_of_birth"])
                )

                for other_name, other_dob in existing_named:

                    if _dob_conflicts(my_dob, other_dob):

                        continue

                    if _name_match_score(
                        mine, other_name
                    ) >= _NAME_MATCH_MERGE:

                        return True

            return False

        gold_df = gold_df[
            ~gold_df.apply(already_loaded, axis=1)
        ].copy()

        print(
            "Existing client rows skipped for INSERT:",
            before - len(gold_df)
        )

    print(
        "Rows actually going to INSERT:",
        len(gold_df)
    )

    # ========================================================
    # NOTHING NEW
    # ========================================================

    if gold_df.empty:

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
    # KEEP DATABASE COLUMNS
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
    # ========================================================

    print()
    print("Starting database insert...")

    inserted_rows = 0

    try:

        from utils.db import upsert_dataframe

        # uq_clients_pan is a plain unique index on pan, so
        # Postgres treats NULLs as DISTINCT: real PANs stay
        # unique, and any number of PAN-less clients is allowed.
        #
        # A PAN-less row therefore never matches ON CONFLICT and
        # is simply inserted -- which is correct, because the
        # ones already in the table were filtered out above on
        # guardian_pan + name + date_of_birth. The partial index
        # uq_clients_pan_absent backstops that check.
        # Rows WITH a PAN go through the upsert, keyed on pan.
        #
        # Rows WITHOUT one cannot: upsert_dataframe de-duplicates
        # the batch with PARTITION BY <conflict column>, and
        # every PAN-less row has pan = NULL, so all of them fall
        # into a single partition and 35 of 36 are silently
        # discarded. They are plain-inserted instead, which is
        # safe because the ones already in the table were
        # filtered out above on guardian_pan + name + dob, and
        # uq_clients_pan_absent backstops that check.
        with_pan = gold_df[gold_df["pan"].notna()]

        without_pan = gold_df[gold_df["pan"].isna()]

        inserted_rows = 0

        if not with_pan.empty:

            upsert_result = upsert_dataframe(
                with_pan,
                schema="gold",
                table="clients",
                conflict_columns=["pan"],
                chunksize=100,
                updated_at_column=None,
            )

            inserted_rows += upsert_result["inserted"]

        if not without_pan.empty:

            without_pan.to_sql(
                "clients",
                engine,
                schema="gold",
                if_exists="append",
                index=False,
                method="multi",
                chunksize=100,
            )

            inserted_rows += len(without_pan)

            print(
                "PAN-less clients inserted:",
                len(without_pan)
            )

        print(
            f"Inserted {inserted_rows} / "
            f"{len(gold_df)} rows"
        )

        # ====================================================
        # VERIFY
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

            print(
                "Total rows in gold.clients:",
                int(
                    count_df.iloc[0]["total_clients"]
                )
            )

        # ====================================================
        # CKYC / DP ID COUNTS
        # ====================================================

        validation_df = safe_read(
            """
            SELECT

                COUNT(*) AS total_clients,

                COUNT(ckyc_no) AS clients_with_ckyc,

                COUNT(dp_id) AS clients_with_dp_id

            FROM gold.clients
            """
        )

        if not validation_df.empty:

            print()
            print(
                "Clients with CKYC:",
                int(
                    validation_df.iloc[0][
                        "clients_with_ckyc"
                    ]
                )
            )

            print(
                "Clients with DP ID:",
                int(
                    validation_df.iloc[0][
                        "clients_with_dp_id"
                    ]
                )
            )

        # ====================================================
        # PREVIEW
        # ====================================================

        database_preview = safe_read(
            """
            SELECT
                pan,
                full_name,
                ckyc_no,
                dp_id,
                arn,
                sub_arn,
                email,
                investor_type,
                tax_status,
                kyc_status,
                arn_id,
                onboarded_at,
                source,
                created_at
            FROM gold.clients
            WHERE ckyc_no IS NOT NULL
               OR dp_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 10
            """
        )

        print()
        print(
            "Sample clients having CKYC / DP ID:"
        )

        if database_preview.empty:

            print(
                "WARNING: No CKYC / DP ID values found "
                "in gold.clients."
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

        print(
            "Database error:",
            str(e)[:3000]
        )

        print(
            "Rows successfully inserted before failure:",
            inserted_rows
        )

        print(
            "Rows remaining:",
            len(gold_df) - inserted_rows
        )

        traceback.print_exc()

        return False


# ============================================================
# MAIN FUNCTION
# ============================================================

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