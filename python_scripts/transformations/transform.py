import pandas as pd
from sqlalchemy import create_engine

# =====================================================
# DATABASE CONNECTION
# =====================================================

engine = create_engine(
    "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
)

# =====================================================
# SAFE READ
# =====================================================

def safe_read(query):
    try:
        return pd.read_sql(query, engine)
    except Exception as e:
        print(e)
        return pd.DataFrame()


# =====================================================
# LOAD STATE DIMENSION
# =====================================================

def load_state_dimension():

    state_dim = safe_read("""
        SELECT
            state_id,
            state_name
        FROM bronze.state_code
    """)

    if state_dim.empty:
        return state_dim

    state_dim["state_id"] = pd.to_numeric(
        state_dim["state_id"],
        errors="coerce"
    )

    state_dim["state_name"] = (
        state_dim["state_name"]
        .astype("string")
        .str.strip()
        .str.title()
    )

    return state_dim


# =====================================================
# NORMALIZE DATA FOR COMPARISON
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    df = df.drop(
        columns=[
            "created_at",
            "updated_at",
            "flag"
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
# CREATE ROW KEY
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)

    return (
        df.fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )


# =====================================================
# GET SILVER TABLE COLUMNS
# =====================================================

def get_table_columns(table_name):

    query = f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='silver'
        AND table_name='{table_name}'
        ORDER BY ordinal_position
    """

    return pd.read_sql(
        query,
        engine
    )["column_name"].tolist()

   # =====================================================
# APPEND ONLY NEW RECORDS
# =====================================================

def append_new_rows(df, table_name):

    if df.empty:
        print(f"{table_name} : No rows found.")
        return

    # -----------------------------------------
    # Read Existing Silver Table
    # -----------------------------------------

    try:

        existing = pd.read_sql(
            f"""
            SELECT
                <only business columns>
            FROM silver.{table_name}
            """,
            engine
        )

    except Exception:

        existing = pd.DataFrame()

    # -----------------------------------------
    # First Load
    # -----------------------------------------

    if existing.empty:

        now = pd.Timestamp.now()

        df["created_at"] = now
        df["updated_at"] = now

        db_cols = get_table_columns(table_name)

        for col in db_cols:

            if col not in df.columns:
                df[col] = None

        df = df[db_cols]

        df.to_sql(
            table_name,
            engine,
            schema="silver",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000
        )

        print(f"{table_name} : Initial Load ({len(df)} rows)")
        return

    # -----------------------------------------
    # Compare Rows
    # -----------------------------------------

    existing_keys = set(
        create_row_key(existing)
    )

    new_keys = create_row_key(df)

    df = df.loc[
        ~new_keys.isin(existing_keys)
    ].copy()

    if df.empty:

        print(f"{table_name} : No New Records")
        return

    # -----------------------------------------
    # Add Audit Columns
    # -----------------------------------------

    now = pd.Timestamp.now()

    df["created_at"] = now
    df["updated_at"] = now

    db_cols = get_table_columns(table_name)

    for col in db_cols:

        if col not in df.columns:
            df[col] = None

    df = df[db_cols]

    df.to_sql(
        table_name,
        engine,
        schema="silver",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )

    print(f"{table_name} : {len(df)} New Rows Inserted")

    # =====================================================
# LOAD SILVER
# =====================================================

def load_silver():

    # =====================================================
    # INVESTOR MASTER
    # =====================================================

    investor_df = safe_read("""
        SELECT *
        FROM bronze.investor_master
        WHERE flag = 0
    """)
    
    if not investor_df.empty:
    
        investor_df = transform_investor_master(investor_df)
    
        # ===========================
        # Occupation Name -> Code Mapping
        # ===========================
        occupation_mapping = {
            "SERVICE": 1,
            "BUSINESS": 2,
            "PROFESSIONAL": 3,
            "AGRICULTURE": 4,
            "STUDENT": 5,
            "RETIRED": 6,
            "HOUSEWIFE": 7,
            "OTHERS": 8,
            "PRIVATE SECTOR": 9,
            "PUBLIC SECTOR": 10,
            "SELF EMPLOYED": 11,
            "NOT APPLICABLE": 41
        }
    
        investor_df["occupation"] = (
            investor_df["occupation"]
            .astype(str)
            .str.strip()
            .str.upper()
            .replace(occupation_mapping)
        )
    
        investor_df["occupation"] = pd.to_numeric(
            investor_df["occupation"],
            errors="coerce"
        ).astype("Int64")
    
        investor_df = round_decimal_columns(investor_df)
    
        investor_df = investor_df.drop(
            columns=["flag"],
            errors="ignore"
        )
    
        append_new_rows(
            investor_df,
            "investor_master"
        )

    # =====================================================
    # TRANSACTION MASTER
    # =====================================================

    transaction_df = safe_read("""
        SELECT *
        FROM bronze.transaction_master_new
        WHERE flag = 0
    """)

    if not transaction_df.empty:

        transaction_df = transform_transaction(transaction_df)

        transaction_df = round_decimal_columns(transaction_df)

        transaction_df = transaction_df.drop(
            columns=["flag"],
            errors="ignore"
        )

        append_new_rows(
            transaction_df,
            "transaction_master_new"
        )

    # =====================================================
    # SIP MASTER
    # =====================================================

    sip_df = safe_read("""
        SELECT *
        FROM bronze.sip_master_new
        WHERE flag = 0
    """)

    if not sip_df.empty:

        sip_df = transform_sip_master(sip_df)

        sip_df = round_decimal_columns(sip_df)

        sip_df = sip_df.drop(
            columns=["flag"],
            errors="ignore"
        )

        id_cols = [
            "inv_iin",
            "inv_dp_id",
            "inv_client_id",
            "ecsno",
            "umrncode",
            "instrm_no",
            "cheq_micr_no",
            "request_ref_no",
            "ft_sip_regno"
        ]

        for col in id_cols:
            if col in sip_df.columns:
                sip_df[col] = sip_df[col].astype("string")

        append_new_rows(
            sip_df,
            "sip_master_new"
        )

    print("\nSilver Layer Loaded Successfully")

# =====================================================
# INVESTOR MASTER TRANSFORMATION
# =====================================================

def transform_investor_master(df):

    df = df.copy()

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    df = df.drop_duplicates()

    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(include="object").columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =====================================================
    # LOAD STATE DIMENSION
    # =====================================================

    state_dim = load_state_dimension()

    if not state_dim.empty:

        # -----------------------------------------
        # Prepare Dimension Table
        # -----------------------------------------

        state_dim["state_id"] = pd.to_numeric(
            state_dim["state_id"],
            errors="coerce"
        )

        state_dim["state_name"] = (
            state_dim["state_name"]
            .astype("string")
            .str.strip()
            .str.title()
        )

        state_lookup = dict(
            zip(
                state_dim["state_name"].str.upper(),
                state_dim["state_id"]
            )
        )

        code_lookup = dict(
            zip(
                state_dim["state_id"],
                state_dim["state_name"]
            )
        )

        # -----------------------------------------
        # Clean DataFrame Columns
        # -----------------------------------------

        if "state" in df.columns:

            df["state"] = (
                df["state"]
                .astype("string")
                .str.strip()
                .replace(r"^\s*$", pd.NA, regex=True)
                .str.title()
            )

        if "gst_state_code" in df.columns:

            df["gst_state_code"] = pd.to_numeric(
                df["gst_state_code"],
                errors="coerce"
            )

        # -----------------------------------------
        # STATE -> GST STATE CODE
        # -----------------------------------------

        if "state" in df.columns:

            mapped_code = (
                df["state"]
                .str.upper()
                .map(state_lookup)
            )

            if "gst_state_code" in df.columns:
                df["gst_state_code"] = df["gst_state_code"].fillna(mapped_code)
            else:
                df["gst_state_code"] = mapped_code

        # -----------------------------------------
        # GST STATE CODE -> STATE
        # -----------------------------------------

        if "gst_state_code" in df.columns:

            mapped_state = (
                df["gst_state_code"]
                .map(code_lookup)
            )

            if "state" in df.columns:
                df["state"] = mapped_state.combine_first(df["state"])
            else:
                df["state"] = mapped_state

    # =====================================================
    # ACCOUNT TYPE
    # =====================================================

    account_mapping = {
        "SAV": "Savings",
        "SAVINGS": "Savings",
        "CURRENT": "Current",
        "CUR": "Current",
        "NRE": "NRE",
        "NRO": "NRO"
    }

    if "account_type" in df.columns:

        df["account_type"] = (
            df["account_type"]
            .str.upper()
            .map(account_mapping)
            .fillna(df["account_type"])
        )

    # =====================================================
    # TAX STATUS
    # =====================================================

    tax_mapping = {
        "I": "Individual",
        "1": "Individual",
        "INDIVIDUAL": "Individual",
        "N": "N"
    }

    if "tax_status" in df.columns:

        df["tax_status"] = (
            df["tax_status"]
            .str.upper()
            .map(tax_mapping)
            .fillna(df["tax_status"])
        )

    # =====================================================
    # HOLDING DESCRIPTION
    # =====================================================

    holding_mapping = {
        "SI": "Single",
        "SINGLE": "Single",

        "AS": "Anyone Or Survivor",
        "ANYONE OR SURVIVOR": "Anyone Or Survivor",

        "JO": "Joint",
        "JOINT": "Joint",

        "EO": "Either Or Survivor",
        "EITHER OR SURVIVOR": "Either Or Survivor"
    }

    holding_cols = [
        "holding_nature",
        "mode_of_holding_description"
    ]

    for col in holding_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.strip()
                .str.upper()
                .map(holding_mapping)
                .fillna(
                    df[col]
                    .astype("string")
                    .str.strip()
                    .str.title()
                )
            )

    # =====================================================
    # IFSC
    # =====================================================

    if "ifsc_code" in df.columns:

        df["ifsc_code"] = (
            df["ifsc_code"]
            .str.upper()
        )

    # =====================================================
    # PAN
    # =====================================================

    pan_cols = [
        "pan_no",
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    ]

    for col in pan_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.upper()
            )

    # =====================================================
    # EMAIL
    # =====================================================

    email_cols = [
        "email",
        "nominee1_email",
        "nominee2_email",
        "nominee3_email"
    ]

    for col in email_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.lower()
            )

    # =====================================================
    # PHONE
    # =====================================================

    phone_cols = [
        "mobile_no",
        "phone_res",
        "phone_off"
    ]

    for col in phone_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.replace(" ", "", regex=False)
                .str.replace("-", "", regex=False)
            )

    # =====================================================
    # DATE COLUMNS
    # =====================================================

    date_cols = [
        "dob",
        "report_date",
        "folio_date"
    ]

    for col in date_cols:

        if col in df.columns:

            df[col] = pd.to_datetime(
                df[col],
                errors="coerce"
            )

    # =====================================================
    # EMPTY TO NULL
    # =====================================================

    df = df.replace(
        r"^\s*$",
        pd.NA,
        regex=True
    )

    return df 

# =====================================================
# TRANSACTION TRANSFORMATION
# =====================================================

def transform_transaction(df):

    df = df.copy()

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    df = df.drop_duplicates()

    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(include="object").columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =====================================================
    # LOAD STATE DIMENSION
    # =====================================================

    state_dim = load_state_dimension()

    if not state_dim.empty:

        # -----------------------------------------
        # Create Lookups
        # -----------------------------------------

        state_lookup = dict(
            zip(
                state_dim["state_name"].str.upper(),
                state_dim["state_id"]
            )
        )

        code_lookup = dict(
            zip(
                state_dim["state_id"],
                state_dim["state_name"]
            )
        )

        # -----------------------------------------
        # Clean State Column
        # -----------------------------------------

        if "state" in df.columns:

            df["state"] = (
                df["state"]
                .astype("string")
                .str.strip()
                .str.title()
            )

        # -----------------------------------------
        # Clean GST State Code
        # -----------------------------------------

        if "gst_state_code" in df.columns:

            df["gst_state_code"] = pd.to_numeric(
                df["gst_state_code"],
                errors="coerce"
            )

        # -----------------------------------------
        # STATE -> GST STATE CODE
        # -----------------------------------------

        if "state" in df.columns:

            mapped_codes = (
                df["state"]
                .str.upper()
                .map(state_lookup)
            )

            if "gst_state_code" in df.columns:

                df["gst_state_code"] = (
                    df["gst_state_code"]
                    .fillna(mapped_codes)
                )

            else:

                df["gst_state_code"] = mapped_codes

        # -----------------------------------------
        # GST STATE CODE -> STATE
        # -----------------------------------------

        if "gst_state_code" in df.columns:

            mapped_states = (
                df["gst_state_code"]
                .map(code_lookup)
            )

            if "state" in df.columns:

                df["state"] = (
                    mapped_states
                    .combine_first(df["state"])
                )

            else:

                df["state"] = mapped_states

    # =====================================================
    # SOURCE SYSTEM
    # =====================================================

    if "source_system" in df.columns:

        df["source_system"] = (
            df["source_system"]
            .str.upper()
        )

    # =====================================================
    # LOCATION
    # =====================================================

    if "location" in df.columns:

        df["location"] = (
            df["location"]
            .str.title()
        )

    # =====================================================
    # BANK NAME
    # =====================================================

    bank_mapping = {
        "HDFCBANK": "HDFC Bank",
        "HDFC BANK": "HDFC Bank",
        "HDFC BANK LTD": "HDFC Bank",
        "HDFC BANK LIMITED": "HDFC Bank",
        "STATE BANK OF INDIA": "State Bank Of India",
        "SBI": "State Bank Of India",
        "ICICI BANK": "ICICI Bank",
        "ICICI BANK LIMITED": "ICICI Bank",
        "AXIS BANK": "Axis Bank",
        "AXIS BANK LTD": "Axis Bank",
        "BANK OF BARODA": "Bank Of Baroda",
        "BANKOFBARODA": "Bank Of Baroda",
        "BANK OF INDIA": "Bank Of India",
        "KOTAK BANK": "Kotak Mahindra Bank",
        "KOTAK MAHINDRA BANK LIMITED": "Kotak Mahindra Bank"
    }

    if "bank_name" in df.columns:

        df["bank_name"] = (
            df["bank_name"]
            .str.upper()
            .map(bank_mapping)
            .fillna(df["bank_name"].str.title())
        )

    # =====================================================
    # TAX STATUS
    # =====================================================

    tax_mapping = {
        "I": "Individual",
        "1": "Individual",
        "INDIVIDUAL": "Individual",
        "N": "NRI",
        "NRI - REPATRIATION": "NRI - Repatriation"
    }

    if "tax_status" in df.columns:

        df["tax_status"] = (
            df["tax_status"]
            .str.upper()
            .map(tax_mapping)
            .fillna(df["tax_status"])
        )

    # =====================================================
    # PAN
    # =====================================================

    if "pan" in df.columns:

        df["pan"] = (
            df["pan"]
            .str.upper()
        )

    # =====================================================
    # EMAIL
    # =====================================================

    if "email" in df.columns:

        df["email"] = (
            df["email"]
            .str.lower()
        )

    # =====================================================
    # PHONE
    # =====================================================

    phone_cols = [
        "mobile",
        "rphone",
        "ophone"
    ]

    for col in phone_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.replace(" ", "", regex=False)
                .str.replace("-", "", regex=False)
            )

    # =====================================================
    # DATE COLUMNS
    # =====================================================

    date_cols = [
        "trade_date",
        "post_date",
        "report_date",
        "purdate",
        "chqdate",
        "sys_regn_d"
    ]

    for col in date_cols:

        if col in df.columns:

            df[col] = pd.to_datetime(
                df[col],
                errors="coerce"
            ).dt.date

    # =====================================================
    # NUMERIC COLUMNS
    # =====================================================

    numeric_cols = [
        "units",
        "amount",
        "load_amount",
        "broker_percent",
        "broker_commission",
        "purprice",
        "stamp_duty"
    ]

    for col in numeric_cols:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # =====================================================
    # EMPTY TO NULL
    # =====================================================

    df = df.replace(
        r"^\s*$",
        pd.NA,
        regex=True
    )

    return df

# =====================================================
# SIP MASTER TRANSFORMATION
# =====================================================

# =====================================================
# CLEAN SIP IDENTIFIER COLUMNS
# REMOVE SCIENTIFIC NOTATION AND .0
# =====================================================

def clean_sip_identifiers(df):

    # identifier_cols = [
    #     "folio_no",
    #     "folio_old",
    #     "inv_iin",
    #     "inv_dp_id",
    #     "inv_client_id",
    #     "ecsno",
    #     "umrncode",
    #     "instrm_no",
    #     "cheq_micr_no",
    #     "request_ref_no",
    #     "ft_sip_regno"
    # ]

    # for col in identifier_cols:

    #     if col in df.columns:

    #         df[col] = (
    #             df[col]
    #             .apply(
    #                 lambda x: (
    #                     format(x, "f")
    #                     if isinstance(x, float)
    #                     else x
    #                 )
    #             )
    #             .astype("string")
    #             .str.replace(
    #                 r"\.0$",
    #                 "",
    #                 regex=True
    #             )
    #             .str.strip()
    #         )

    return df

def transform_sip_master(df):

    df = df.copy()

    # =====================================================
    # REMOVE DUPLICATES
    # =====================================================

    df = df.drop_duplicates()

    df = clean_sip_identifiers(df)

    # Convert all object-like columns safely
    for col in df.columns:
        if df[col].dtype != "object":
            continue

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =====================================================
    # FORCE BIGINT IDENTIFIER COLUMNS
    # =====================================================

    identifier_cols = [
        "inv_iin",
        "inv_dp_id",
        "inv_client_id",
        "ecsno",
        "umrncode",
        "instrm_no",
        "cheq_micr_no",
        "request_ref_no",
        "ft_sip_regno"
    ]


    for col in identifier_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .apply(
                    lambda x: (
                        str(int(x))
                        if isinstance(x, float) and not pd.isna(x)
                        else x
                    )
                )
                .astype("string")
                .str.strip()
            )

    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(include="object").columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =====================================================
    # TITLE CASE COLUMNS
    # =====================================================

    title_cols = [
        "location",
        "investor_name",
        "agent_name",
        "subbroker",
        "scheme_name",
        "to_scheme_name",
        "ecs_bank_name",
        "ecs_holder_name",
        "dp_inv_name"
    ]

    for col in title_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.title()
            )

    # =====================================================
    # PAN
    # =====================================================

    if "pan" in df.columns:

        df["pan"] = (
            df["pan"]
            .str.upper()
        )

    # =====================================================
    # UPPER CASE COLUMNS
    # =====================================================

    upper_cols = [
        "zone",
        "branch",
        "ihno",
        "folio",
        "agent_code",
        "fund_code",
        "product_code",
        "to_product_code",
        "ecsno",
        "reg_slno",
        "inv_dp_id",
        "inv_client_id",
        "umrncode"
    ]

    for col in upper_cols:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.upper()
                .str.strip()
            )
    # =====================================================
    # PLAN
    # =====================================================

    plan_mapping = {
        "REGULAR": "Regular",
        "DIRECT": "Direct"
    }

    for col in ["plan", "to_plan"]:

        if col in df.columns:

            df[col] = (
                df[col]
                .str.upper()
                .map(plan_mapping)
                .fillna(df[col].str.title())
            )

    # =====================================================
    # SIP TYPE
    # =====================================================

    if "sip_type" in df.columns:

        df["sip_type"] = (
            df["sip_type"]
            .str.title()
        )

    # =====================================================
    # SIP MODE
    # =====================================================

    sip_mode_mapping = {
        "AUTO-DEBIT": "Auto Debit",
        "AUTO DEBIT": "Auto Debit",
        "NACH": "NACH",
        "ECS": "ECS"
    }

    if "sip_mode" in df.columns:

        df["sip_mode"] = (
            df["sip_mode"]
            .str.upper()
            .map(sip_mode_mapping)
            .fillna(df["sip_mode"].str.title())
        )

    # =====================================================
    # FREQUENCY
    # =====================================================

    if "frequency" in df.columns:

        df["frequency"] = (
            df["frequency"]
            .str.title()
        )

    # =====================================================
    # TRANSACTION TYPE
    # =====================================================

    if "trtype" in df.columns:

        df["trtype"] = (
            df["trtype"]
            .str.title()
        )

    # =====================================================
    # STATUS
    # =====================================================

    if "status" in df.columns:

        df["status"] = (
            df["status"]
            .str.title()
        )

    # =====================================================
    # MODIFY FLAG
    # =====================================================

    modify_mapping = {
        "Y": "Yes",
        "N": "No"
    }

    if "modify_flag" in df.columns:

        df["modify_flag"] = (
            df["modify_flag"]
            .str.upper()
            .map(modify_mapping)
            .fillna(df["modify_flag"])
        )

    # =====================================================
    # REMOVE SPACES FROM ACCOUNT NUMBER
    # =====================================================

    if "ecs_acno" in df.columns:

        df["ecs_acno"] = (
            df["ecs_acno"]
            .str.replace(" ", "", regex=False)
        )

    # =====================================================
    # DATE COLUMNS
    # =====================================================

    # date_cols = [
    #     "registration_date",
    #     "start_date",
    #     "end_date",
    #     "terminate_date"
    # ]

    # for col in date_cols:

    #     if col in df.columns:

    #         df[col] = pd.to_datetime(
    #             df[col],
    #             errors="coerce"
    #         ).dt.date

    # =====================================================
    # NUMERIC COLUMNS
    # =====================================================

    numeric_cols = [
        "amount",
        "no_of_installments"
    ]

    for col in numeric_cols:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # =====================================================
    # EMPTY TO NULL
    # =====================================================

    df = df.replace(
        r"^\s*$",
        pd.NA,
        regex=True
    )

    return df

# =====================================================
# ROUND DECIMAL COLUMNS
# =====================================================

def round_decimal_columns(df):

    df = df.copy()

    float_cols = df.select_dtypes(
        include=["float16", "float32", "float64"]
    ).columns

    for col in float_cols:

        df[col] = df[col].round(4)

    return df