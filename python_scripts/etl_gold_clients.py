import pandas as pd
from datetime import datetime

from utils.db import engine, restore_engine


# ============================================================
# EXTRACT GOLD CLIENT DATA
# ============================================================

def extract_clients():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD CLIENTS")
    print("=" * 80)

    query = """
    SELECT
        i.*,

        t.pan AS txn_pan,
        t.traddate AS txn_traddate,
        t.common_account_number AS txn_common_account_number,

        s.pan AS sip_pan

    FROM silver.investor_master i

    LEFT JOIN
    (
        SELECT
            folio_no,
            MAX(pan) AS pan,
            MIN(traddate) AS traddate,
            MAX(common_account_number) AS common_account_number
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

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    print("\nExtraction Completed")
    print("-" * 80)
    print("Rows fetched    :", len(df))
    print("Columns fetched :", len(df.columns))

    return df


# ============================================================
# TRANSFORM GOLD CLIENT DATA
# ============================================================

def transform_clients(df):

    print("=" * 80)
    print("TRANSFORMING GOLD CLIENTS")
    print("=" * 80)

    df = df.copy()

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    # ========================================================
    # HELPERS
    # ========================================================

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
                    "NAT",
                    "NaT"
                ],
                pd.NA
            )
        )

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

    def clean_phone(series):

        raw = clean_string(series)

        # Keep only the first comma-separated number
        raw = (
            raw
            .str.split(",")
            .str[0]
            .str.strip()
        )

        # Reject values containing alphabetic characters.
        # This prevents scheme/client names from entering phone.
        invalid_alpha = raw.str.contains(
            r"[A-Za-z]",
            regex=True,
            na=False
        )

        raw.loc[invalid_alpha] = pd.NA

        # Keep only digits
        raw = (
            raw
            .str.replace(
                r"\D",
                "",
                regex=True
            )
        )

        # Phone column allows max 20 characters
        raw = raw.str[:20]

        return raw.replace("", pd.NA)

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

        # +91XXXXXXXXXX / 91XXXXXXXXXX
        mask_91 = (
            (digits.str.len() == 12)
            &
            digits.str.startswith("91")
        )

        mobile.loc[mask_91] = (
            digits.loc[mask_91].str[-10:]
        )

        mobile_isd.loc[mask_91] = "91"

        # 10 digit Indian mobile
        mask_10 = (
            digits.str.len() == 10
        )

        mobile.loc[mask_10] = (
            digits.loc[mask_10]
        )

        mobile_isd.loc[mask_10] = "91"

        # Other international numbers
        mask_other = (
            (digits.str.len() > 10)
            & (~mask_91)
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

    # ========================================================
    # SOURCE
    # ========================================================

    df["source"] = (
        clean_string(df["source"])
        .str.upper()
        .str.strip()
    )

    cams_mask = df["source"] == "CAMS"
    kfin_mask = df["source"] == "KFIN"

    # ========================================================
    # PAN
    #
    # CAMS:
    # PAN / pan_no
    #
    # KFIN:
    # PANGNO / PAN Number
    #
    # Silver should expose these as pan_no.
    # ========================================================

    if "pan_no" not in df.columns:

        raise Exception(
            "pan_no is missing from silver.investor_master. "
            "Fix the Investor Master mapping before Gold."
        )

    df["pan_no"] = clean_pan(df["pan_no"])
    df["txn_pan"] = clean_pan(df["txn_pan"])
    df["sip_pan"] = clean_pan(df["sip_pan"])

    df["pan"] = (
        df["pan_no"]
        .fillna(df["txn_pan"])
        .fillna(df["sip_pan"])
    )

    print("\nPAN Statistics")
    print("-" * 80)

    print("Investor PAN    :", df["pan_no"].notna().sum())
    print("Transaction PAN :", df["txn_pan"].notna().sum())
    print("SIP PAN         :", df["sip_pan"].notna().sum())
    print("Final PAN       :", df["pan"].notna().sum())
    print("Missing PAN     :", df["pan"].isna().sum())

    # ========================================================
    # CREATE GOLD
    # ========================================================

    gold = pd.DataFrame(index=df.index)

    # ========================================================
    # status
    # ========================================================

    gold["status"] = None

    # ========================================================
    # full_name
    #
    # Silver investor_name
    # ========================================================

    gold["full_name"] = clean_string(
        df["investor_name"]
    )

    # ========================================================
    # client_label
    # ========================================================

    gold["client_label"] = None

    # ========================================================
    # PHONE
    #
    # CAMS:
    # PHONE_RES / PHONE_OFF
    #
    # KFIN:
    # Phone Residence / Phone Office
    #
    # IMPORTANT:
    # Silver must already alias these correctly.
    # ========================================================

    if "phone_res" in df.columns:
        phone_res = clean_phone(df["phone_res"])
    else:
        phone_res = pd.Series(
            pd.NA,
            index=df.index,
            dtype="object"
        )

    if "phone_off" in df.columns:
        phone_off = clean_phone(df["phone_off"])
    else:
        phone_off = pd.Series(
            pd.NA,
            index=df.index,
            dtype="object"
        )

    gold["phone"] = (
        phone_res
        .fillna(phone_off)
    )

    # ========================================================
    # MOBILE
    #
    # CAMS: MOBILE_NO
    # KFIN: Mobile Number
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
    #
    # FLAG ONLY
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
        gold["email"] = clean_string(df["email"])
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
    #
    # KFIN:
    # CommonAccNo from master
    # fallback transaction CAN
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

    kfin_can_missing = (
        kfin_mask
        & gold["can"].isna()
    )

    gold.loc[
        kfin_can_missing,
        "can"
    ] = clean_string(
        df.loc[
            kfin_can_missing,
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
    #
    # CAMS: TAX_STATUS
    # KFIN: StatusDesc -> CategoryDesc
    # ========================================================

    investor_type_source = pd.Series(
        pd.NA,
        index=df.index,
        dtype="object"
    )

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

        kfin_missing = (
            kfin_mask
            & investor_type_source.isna()
        )

        investor_type_source.loc[
            kfin_missing
        ] = clean_string(
            df.loc[
                kfin_missing,
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

    gold["tax_status"] = clean_string(
        df["tax_status"]
    )

    # ========================================================
    # KYC
    #
    # CAMS: Verified if CKYC present
    # KFIN: Kyc1Flag
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
            clean_string(df["kyc1flag"])
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
    #
    # Keep NULL until ARN lookup exists.
    # ========================================================

    gold["arn_id"] = None

    broker_code = pd.Series(
        pd.NA,
        index=df.index,
        dtype="object"
    )

    # --------------------------------------------------------
    # Investor Master broker_code
    # --------------------------------------------------------

    if "broker_code" in df.columns:

        broker_code = clean_string(
            df["broker_code"]
        )

    # --------------------------------------------------------
    # Transaction brokcode fallback
    # --------------------------------------------------------

    if "brokcode" in df.columns:

        txn_broker_code = clean_string(
            df["brokcode"]
        )

        broker_code = (
            broker_code
            .fillna(txn_broker_code)
        )

    # --------------------------------------------------------
    # ARN LOOKUP FROM RESTORE DATABASE
    # --------------------------------------------------------

    if broker_code.notna().any():

        arn_lookup = pd.read_sql(
            """
            SELECT
                arn_code,
                id AS arn_id
            FROM arn
            WHERE arn_code IS NOT NULL
            AND TRIM(arn_code) <> ''
            AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            restore_engine
        )

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

    # --------------------------------------------------------
    # ARN VALIDATION
    # --------------------------------------------------------

    print("\nARN Mapping")
    print("-" * 80)
    print("Broker codes found :", broker_code.notna().sum())
    print("ARN IDs mapped     :", gold["arn_id"].notna().sum())
    print("ARN IDs missing    :", gold["arn_id"].isna().sum())
    # ========================================================
    # ONBOARDED AT
    #
    # CAMS: min(FOLIO_DATE)
    # KFIN: min(transaction date)
    # ========================================================

    gold["onboarded_at"] = pd.NaT

    if "folio_date" in df.columns:

        cams_folio_date = pd.to_datetime(
            df["folio_date"],
            errors="coerce"
        )

        gold.loc[
            cams_mask,
            "onboarded_at"
        ] = cams_folio_date.loc[
            cams_mask
        ]

    kfin_trxn_date = pd.to_datetime(
        df["txn_traddate"],
        errors="coerce"
    )

    gold.loc[
        kfin_mask,
        "onboarded_at"
    ] = kfin_trxn_date.loc[
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
    # ROW COUNT
    # ========================================================

    print("\nTransformation Completed")
    print("-" * 80)

    print("Silver rows :", len(df))
    print("Gold rows   :", len(gold))

    if len(df) != len(gold):

        raise Exception(
            "Row count mismatch. "
            "Data loss detected between Silver and Gold."
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

    # ========================================================
    # VALIDATION
    # ========================================================

    print("\nGold Clients Preview")
    print("-" * 80)
    print(gold.head())

    print("\nSource Distribution")
    print("-" * 80)
    print(
        gold["source"]
        .value_counts(dropna=False)
    )

    # --------------------------------------------------------
    # PHONE VALIDATION
    # --------------------------------------------------------

    print("\nPhone Validation")
    print("-" * 80)

    phone_values = (
        gold["phone"]
        .dropna()
        .astype(str)
    )

    print(
        "Phone values:",
        len(phone_values)
    )

    invalid_phone = (
        phone_values.str.contains(
            r"[A-Za-z]",
            regex=True,
            na=False
        )
        |
        (phone_values.str.len() > 20)
    )

    print(
        "Invalid phone values:",
        invalid_phone.sum()
    )

    if invalid_phone.any():

        print(
            gold.loc[
                phone_values[
                    invalid_phone
                ].index,
                [
                    "full_name",
                    "phone",
                    "source"
                ]
            ].head(20)
        )

        raise Exception(
            "Invalid phone values found."
        )

    # --------------------------------------------------------
    # MOBILE VALIDATION
    # --------------------------------------------------------

    mobile_values = (
        gold["mobile"]
        .dropna()
        .astype(str)
    )

    invalid_mobile = (
        ~mobile_values.str.match(
            r"^\d{10}$"
        )
    )

    print(
        "Mobile values:",
        len(mobile_values)
    )

    print(
        "Invalid mobile values:",
        invalid_mobile.sum()
    )

    if invalid_mobile.any():

        raise Exception(
            "Invalid mobile values found. "
            "Gold.mobile must contain exactly 10 digits."
        )

    # --------------------------------------------------------
    # LENGTH VALIDATION
    # --------------------------------------------------------

    column_limits = {

        "status": 20,
        "full_name": 255,
        "client_label": 255,
        "phone": 20,
        "mobile_isd": 5,
        "mobile": 20,
        "whatsapp_isd": 5,
        "whatsapp_no": 20,
        "aadhaar": 12,
        "pan": 10,
        "email": 255,
        "marital_status": 10,
        "blood_group": 5,
        "equity_ucc": 30,
        "can": 30,
        "occupation": 30,
        "family_relation": 20,
        "gender": 10,
        "investor_type": 20,
        "tax_status": 30,
        "kyc_status": 20,
        "risk_profile": 20,
        "source": 30
    }

    validation_failed = False

    print("\nString Length Validation")
    print("-" * 80)

    for column, max_length in column_limits.items():

        if column not in gold.columns:
            continue

        values = (
            gold[column]
            .dropna()
            .astype(str)
        )

        if len(values) == 0:

            print(
                f"{column:25} no values"
            )

            continue

        max_found = values.str.len().max()

        print(
            f"{column:25} "
            f"max={max_found} "
            f"allowed={max_length}"
        )

        invalid = (
            values.str.len() > max_length
        )

        if invalid.any():

            validation_failed = True

            print(
                f"\nINVALID VALUES IN: {column}"
            )

            for idx in values[invalid].index[:10]:

                print(
                    f"Row {idx}: "
                    f"{repr(gold.loc[idx, column])}"
                )

    if validation_failed:

        raise Exception(
            "Gold clients validation failed."
        )

    print(
        "\nAll Gold Client values passed validation."
    )

    return gold


# ============================================================
# LOAD GOLD CLIENT DATA
# ============================================================

def load_clients(gold_df):

    print("=" * 80)
    print("LOADING GOLD CLIENTS")
    print("=" * 80)

    # ========================================================
    # CHECK EXISTING
    # ========================================================

    existing_clients = pd.read_sql(
        """
        SELECT
            pan,
            created_at
        FROM gold.clients
        """,
        engine
    )

    print(
        "Existing clients:",
        len(existing_clients)
    )

    # ========================================================
    # DUPLICATE CHECK
    # ========================================================

    if not existing_clients.empty:

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

        existing_pan = set(
            existing_clients.loc[
                existing_clients["pan"] != "",
                "pan"
            ]
        )

        before = len(gold_df)

        gold_df = gold_df[
            ~gold_df["pan"].isin(existing_pan)
        ].copy()

        print(
            "Existing PAN rows removed:",
            before - len(gold_df)
        )

    print(
        "Rows after duplicate check:",
        len(gold_df)
    )

    # ========================================================
    # NOTHING TO INSERT
    # ========================================================

    if gold_df.empty:

        print(
            "\nNo new clients to insert."
        )

        return True

    # ========================================================
    # INSERT
    # ========================================================

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

        print("\nERROR WHILE LOADING GOLD CLIENTS")
        print(type(e).__name__)
        print(e)

        return False


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("\n")
    print("=" * 80)
    print("STARTING GOLD CLIENTS ETL")
    print("=" * 80)

    try:

        clients_df = extract_clients()

        gold_clients = transform_clients(
            clients_df
        )

        status = load_clients(
            gold_clients
        )

        if status:

            print("\n")
            print("=" * 80)
            print(
                "GOLD CLIENTS ETL COMPLETED SUCCESSFULLY"
            )
            print("=" * 80)

        else:

            raise Exception(
                "Gold clients load failed."
            )

    except Exception as e:

        print("\n")
        print("=" * 80)
        print("GOLD CLIENTS ETL ERROR")
        print("=" * 80)
        print(type(e).__name__)
        print(e)

        raise