import pandas as pd

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
# GET LAST PROCESSED TIME FROM SILVER
# =====================================================

def get_last_processed_time(table_name):

    try:

        result = pd.read_sql(
            f"""
            SELECT
                MAX(created_at) AS last_time
            FROM silver.{table_name}
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
# LOAD STATE DIMENSION
# =====================================================

def load_state_dimension():

    state_dim = safe_read(
        """
        SELECT
            state_id,
            state_name
        FROM bronze.state_code
        """
    )

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
# LOAD SCHEME MAPPING
#
# IMPORTANT:
#
# scheme_mapping table columns:
#
# rta_scheme_code
# scheme_id
#
# Mapping:
#
# investor_master.product_code
#          ↓
# scheme_mapping.rta_scheme_code
#          ↓
# scheme_mapping.scheme_id
#
# transaction_master_new.prodcode
#          ↓
# scheme_mapping.rta_scheme_code
#          ↓
# scheme_mapping.scheme_id
#
# sip_master_new.product_code
#          ↓
# scheme_mapping.rta_scheme_code
#          ↓
# scheme_mapping.scheme_id
#
# No is_active condition.
# No other filtering condition.
# =====================================================

def load_scheme_mapping():

    scheme_mapping = safe_read(
        """
        SELECT
            rta_scheme_code,
            scheme_id
        FROM bronze.scheme_mapping
        """
    )

    if scheme_mapping.empty:

        print(
            "scheme_mapping : No data found."
        )

        return scheme_mapping

    # -------------------------------------------------
    # NORMALIZE RTA SCHEME CODE
    # -------------------------------------------------

    scheme_mapping["rta_scheme_code"] = (
        scheme_mapping["rta_scheme_code"]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # -------------------------------------------------
    # KEEP SCHEME ID AS PROVIDED
    # -------------------------------------------------

    scheme_mapping["scheme_id"] = (
        scheme_mapping["scheme_id"]
        .astype("string")
        .str.strip()
    )

    # -------------------------------------------------
    # REMOVE EMPTY SCHEME CODES
    # -------------------------------------------------

    scheme_mapping = scheme_mapping[
        scheme_mapping["rta_scheme_code"].notna()
        & (
            scheme_mapping["rta_scheme_code"] != ""
        )
    ].copy()

    # -------------------------------------------------
    # REMOVE DUPLICATE RTA SCHEME CODES
    # -------------------------------------------------

    scheme_mapping = (
        scheme_mapping
        .drop_duplicates(
            subset=["rta_scheme_code"],
            keep="first"
        )
        .copy()
    )

    print(
        "Scheme mapping rows loaded :",
        len(scheme_mapping)
    )

    return scheme_mapping


# =====================================================
# MAP SCHEME ID
# =====================================================

def map_scheme_id(
    df,
    product_column
):

    df = df.copy()

    # Always create scheme_id.
    # If no mapping is found, it remains NULL.

    df["scheme_id"] = None

    if product_column not in df.columns:

        print(
            f"Scheme mapping skipped: "
            f"'{product_column}' column not found."
        )

        return df

    scheme_mapping = load_scheme_mapping()

    if scheme_mapping.empty:

        print(
            "Scheme mapping is empty. "
            "scheme_id will remain NULL."
        )

        return df

    # -------------------------------------------------
    # NORMALIZE PRODUCT CODE
    # -------------------------------------------------

    product_code = (
        df[product_column]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # -------------------------------------------------
    # CREATE LOOKUP
    #
    # rta_scheme_code → scheme_id
    # -------------------------------------------------

    scheme_lookup = dict(
        zip(
            scheme_mapping["rta_scheme_code"],
            scheme_mapping["scheme_id"]
        )
    )

    # -------------------------------------------------
    # MAP SCHEME ID
    # -------------------------------------------------

    df["scheme_id"] = (
        product_code
        .map(scheme_lookup)
    )

    # -------------------------------------------------
    # CONVERT MISSING VALUES TO NULL
    # -------------------------------------------------

    df["scheme_id"] = (
        df["scheme_id"]
        .where(
            df["scheme_id"].notna(),
            None
        )
    )

    matched_count = (
        df["scheme_id"]
        .notna()
        .sum()
    )

    unmatched_count = (
        df["scheme_id"]
        .isna()
        .sum()
    )

    print(
        f"Scheme ID mapping using "
        f"{product_column}"
    )

    print(
        "Matched scheme_id :",
        int(matched_count)
    )

    print(
        "Unmatched scheme_id :",
        int(unmatched_count)
    )

    return df


# =====================================================
# NORMALIZE DATA FOR DUPLICATE COMPARISON
# =====================================================

def normalize_for_compare(df):

    df = df.copy()

    # These columns are NOT business fields.
    #
    # scheme_id is specifically ignored so that
    # adding/fixing scheme_id does not make an
    # otherwise identical row appear as a new row.

    ignore_cols = {
        "created_at",
        "updated_at",
        "flag",
        "source",
        "scheme_id"
    }

    df = df.drop(
        columns=list(ignore_cols),
        errors="ignore"
    )

    for col in df.columns:

        if pd.api.types.is_datetime64_any_dtype(
            df[col]
        ):

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
# CREATE FULL ROW KEY
# =====================================================

def create_row_key(df):

    normalized = normalize_for_compare(df)

    return (
        normalized
        .fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )


# =====================================================
# GET SILVER TABLE COLUMNS
# =====================================================

def get_table_columns(table_name):

    query = f"""
        SELECT
            column_name
        FROM information_schema.columns
        WHERE table_schema = 'silver'
          AND table_name = '{table_name}'
        ORDER BY ordinal_position
    """

    try:

        return pd.read_sql(
            query,
            engine
        )["column_name"].tolist()

    except Exception as e:

        print(
            f"Could not get columns for "
            f"silver.{table_name}"
        )

        print(e)

        return []


# =====================================================
# APPEND NEW ROWS TO SILVER
# =====================================================

def append_new_rows(
    df,
    table_name
):

    if df.empty:

        print(
            f"{table_name} : No data"
        )

        return

    print("=" * 80)
    print(
        f"PROCESSING {table_name.upper()} "
        "BRONZE → SILVER"
    )
    print("=" * 80)

    df = df.copy()

    print(
        "Incoming Bronze rows :",
        len(df)
    )

    # =================================================
    # BRONZE FLAG CHECK
    # =================================================

    if "flag" not in df.columns:

        print(
            f"{table_name} : "
            "Bronze flag column is missing."
        )

        return

    # Only Bronze flag = 0 is processed.

    df = df[
        df["flag"] == 0
    ].copy()

    print(
        "Bronze rows with flag = 0 :",
        len(df)
    )

    if df.empty:

        print(
            f"{table_name} : "
            "No Bronze rows with flag = 0."
        )

        return

    # =================================================
    # REMOVE EXACT DUPLICATES INSIDE CURRENT BATCH
    # =================================================

    batch_ignore_cols = {
        "flag",
        "created_at",
        "updated_at",
        "source",
        "scheme_id"
    }

    batch_compare_cols = [
        col
        for col in df.columns
        if col not in batch_ignore_cols
    ]

    if batch_compare_cols:

        before_count = len(df)

        df = (
            df
            .drop_duplicates(
                subset=batch_compare_cols,
                keep="first"
            )
            .copy()
        )

        print(
            "Duplicate rows removed "
            "inside current Bronze batch :",
            before_count - len(df)
        )

    if df.empty:

        print(
            f"{table_name} : "
            "No rows after batch duplicate removal."
        )

        return

    # =================================================
    # READ EXISTING SILVER DATA
    # =================================================

    try:

        existing = pd.read_sql(
            f"""
            SELECT *
            FROM silver.{table_name}
            """,
            engine
        )

    except Exception as e:

        print(
            f"Could not read silver.{table_name}"
        )

        print(e)

        existing = pd.DataFrame()

    print(
        "Existing Silver rows :",
        len(existing)
    )

    # =================================================
    # SILVER EMPTY
    # =================================================

    if existing.empty:

        df["flag"] = 0

        print()
        print(
            "Silver table is empty."
        )

        print(
            "All incoming rows are unique."
        )

        print(
            "All rows assigned flag = 0."
        )

    # =================================================
    # SILVER HAS DATA
    # =================================================

    else:

        # -------------------------------------------------
        # COLUMNS TO IGNORE DURING DUPLICATE COMPARISON
        # -------------------------------------------------

        ignore_cols = {
            "flag",
            "created_at",
            "updated_at",
            "source",
            "scheme_id"
        }

        # -------------------------------------------------
        # COMMON BUSINESS COLUMNS
        # -------------------------------------------------

        compare_cols = [
            col
            for col in df.columns
            if col in existing.columns
            and col not in ignore_cols
        ]

        if not compare_cols:

            print(
                f"{table_name} : "
                "No common business columns "
                "available for duplicate comparison."
            )

            return

        print()
        print(
            "Columns used for duplicate comparison:"
        )

        print(compare_cols)

        print()
        print(
            "Ignored columns:"
        )

        print(ignore_cols)

        # -------------------------------------------------
        # NORMALIZE NEW DATA
        # -------------------------------------------------

        new_compare = normalize_for_compare(
            df[compare_cols]
        )

        # -------------------------------------------------
        # NORMALIZE EXISTING SILVER
        # -------------------------------------------------

        old_compare = normalize_for_compare(
            existing[compare_cols]
        )

        # -------------------------------------------------
        # CREATE NEW ROW KEYS
        # -------------------------------------------------

        new_keys = (
            new_compare
            .fillna("")
            .astype(str)
            .agg("|".join, axis=1)
        )

        # -------------------------------------------------
        # CREATE EXISTING SILVER KEYS
        # -------------------------------------------------

        old_keys = set(
            old_compare
            .fillna("")
            .astype(str)
            .agg("|".join, axis=1)
        )

        # -------------------------------------------------
        # DUPLICATE CHECK
        # -------------------------------------------------

        duplicate_mask = new_keys.isin(
            old_keys
        )

        # -------------------------------------------------
        # FLAG ASSIGNMENT
        # -------------------------------------------------

        df["flag"] = (
            duplicate_mask
            .astype(int)
        )

        print()
        print(
            "Existing rows → flag = 1 :",
            int(duplicate_mask.sum())
        )

        print(
            "Unique rows → flag = 0 :",
            int((~duplicate_mask).sum())
        )

    # =================================================
    # SILVER AUDIT TIMESTAMP
    # =================================================

    load_time = pd.Timestamp.now()

    df["created_at"] = load_time

    df["updated_at"] = load_time

    # =================================================
    # GET SILVER TABLE COLUMNS
    # =================================================

    db_cols = get_table_columns(
        table_name
    )

    if not db_cols:

        print(
            f"{table_name} : "
            "Silver table not found."
        )

        return

    # =================================================
    # ADD MISSING COLUMNS
    # =================================================

    for col in db_cols:

        if col not in df.columns:

            df[col] = None

    # =================================================
    # KEEP ONLY DATABASE COLUMNS
    # =================================================

    df = df[
        db_cols
    ]

    # =================================================
    # NULL HANDLING
    # =================================================

    df = df.where(
        pd.notnull(df),
        None
    )

    # =================================================
    # FINAL FLAG COUNTS
    # =================================================

    if "flag" in df.columns:

        print()
        print("=" * 80)
        print(
            f"FINAL FLAG COUNTS - SILVER.{table_name}"
        )
        print("=" * 80)

        print(
            df["flag"]
            .value_counts()
            .sort_index()
        )

        print(
            "flag = 0 :",
            int(
                (df["flag"] == 0).sum()
            )
        )

        print(
            "flag = 1 :",
            int(
                (df["flag"] == 1).sum()
            )
        )

    # =================================================
    # INSERT INTO SILVER
    # =================================================

    print()
    print("=" * 80)
    print(
        f"INSERTING {len(df)} ROWS "
        f"INTO SILVER.{table_name}"
    )
    print("=" * 80)

    try:

        df.to_sql(
            table_name,
            engine,
            schema="silver",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=5000
        )

    except Exception as e:

        print("=" * 80)
        print(
            f"{table_name.upper()} "
            "SILVER INSERT ERROR"
        )
        print(e)
        print("=" * 80)

        return

    print("=" * 80)
    print(
        f"{table_name} : "
        f"{len(df)} rows inserted into Silver"
    )
    print("=" * 80)


# =====================================================
# INVESTOR MASTER TRANSFORMATION
# =====================================================

def transform_investor_master(df):

    df = df.copy()

    # =================================================
    # REMOVE EXACT DUPLICATES
    # =================================================

    df = df.drop_duplicates()

    # =================================================
    # TRIM STRING COLUMNS
    # =================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =================================================
    # SCHEME ID MAPPING
    #
    # product_code
    # → scheme_mapping.rta_scheme_code
    # → scheme_mapping.scheme_id
    # =================================================

    df = map_scheme_id(
        df,
        "product_code"
    )

    # =================================================
    # STATE MAPPING
    # =================================================

    state_dim = load_state_dimension()

    if not state_dim.empty:

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

        if "state" in df.columns:

            df["state"] = (
                df["state"]
                .astype("string")
                .str.strip()
                .str.title()
            )

        if "gst_state_code" in df.columns:

            df["gst_state_code"] = pd.to_numeric(
                df["gst_state_code"],
                errors="coerce"
            )

        if "state" in df.columns:

            mapped_code = (
                df["state"]
                .str.upper()
                .map(state_lookup)
            )

            if "gst_state_code" in df.columns:

                df["gst_state_code"] = (
                    df["gst_state_code"]
                    .fillna(mapped_code)
                )

            else:

                df["gst_state_code"] = mapped_code

        if "gst_state_code" in df.columns:

            mapped_state = (
                df["gst_state_code"]
                .map(code_lookup)
            )

            if "state" in df.columns:

                df["state"] = (
                    mapped_state
                    .combine_first(
                        df["state"]
                    )
                )

            else:

                df["state"] = mapped_state

    # =================================================
    # ACCOUNT TYPE
    # =================================================

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
            .astype("string")
            .str.upper()
            .map(account_mapping)
            .fillna(df["account_type"])
        )

    # =================================================
    # TAX STATUS
    # =================================================

    tax_mapping = {
        "I": "Individual",
        "1": "Individual",
        "INDIVIDUAL": "Individual",
        "N": "N"
    }

    if "tax_status" in df.columns:

        df["tax_status"] = (
            df["tax_status"]
            .astype("string")
            .str.upper()
            .map(tax_mapping)
            .fillna(df["tax_status"])
        )

    # =================================================
    # HOLDING NATURE
    # =================================================

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

    for col in [
        "holding_nature",
        "mode_of_holding_description"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.upper()
                .str.strip()
                .map(holding_mapping)
                .fillna(
                    df[col]
                    .astype("string")
                    .str.title()
                )
            )

    # =================================================
    # PAN CLEAN
    # =================================================

    for col in [
        "pan_no",
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.upper()
            )

    # =================================================
    # EMAIL CLEAN
    # =================================================

    for col in [
        "email",
        "nominee1_email",
        "nominee2_email",
        "nominee3_email"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.lower()
            )

    # =================================================
    # PHONE CLEAN
    # =================================================

    for col in [
        "mobile_no",
        "phone_res",
        "phone_off"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.replace(" ", "", regex=False)
                .str.replace("-", "", regex=False)
            )

    # =================================================
    # DATE COLUMNS
    # =================================================

    for col in [
        "dob",
        "report_date",
        "folio_date"
    ]:

        if col in df.columns:

            df[col] = pd.to_datetime(
                df[col],
                errors="coerce"
            )

    # =================================================
    # EMPTY STRING → NULL
    # =================================================

    df = df.replace(
        r"^\s*$",
        pd.NA,
        regex=True
    )

    return df


# =====================================================
# TRANSACTION MASTER TRANSFORMATION
# =====================================================

def transform_transaction(df):

    df = df.copy()

    # =================================================
    # REMOVE EXACT DUPLICATES
    # =================================================

    df = df.drop_duplicates()

    # =================================================
    # TRIM STRING COLUMNS
    # =================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =================================================
    # SCHEME ID MAPPING
    #
    # prodcode
    # → scheme_mapping.rta_scheme_code
    # → scheme_mapping.scheme_id
    # =================================================

    df = map_scheme_id(
        df,
        "prodcode"
    )

    # =================================================
    # STATE MAPPING
    # =================================================

    state_dim = load_state_dimension()

    if not state_dim.empty:

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

        if "state" in df.columns:

            df["state"] = (
                df["state"]
                .astype("string")
                .str.strip()
                .str.title()
            )

        if "gst_state_code" in df.columns:

            df["gst_state_code"] = pd.to_numeric(
                df["gst_state_code"],
                errors="coerce"
            )

        if "state" in df.columns:

            mapped_code = (
                df["state"]
                .str.upper()
                .map(state_lookup)
            )

            if "gst_state_code" in df.columns:

                df["gst_state_code"] = (
                    df["gst_state_code"]
                    .fillna(mapped_code)
                )

            else:

                df["gst_state_code"] = mapped_code

        if "gst_state_code" in df.columns:

            mapped_state = (
                df["gst_state_code"]
                .map(code_lookup)
            )

            if "state" in df.columns:

                df["state"] = (
                    mapped_state
                    .combine_first(
                        df["state"]
                    )
                )

            else:

                df["state"] = mapped_state

    # =================================================
    # SOURCE SYSTEM
    # =================================================

    if "source_system" in df.columns:

        df["source_system"] = (
            df["source_system"]
            .astype("string")
            .str.upper()
        )

    # =================================================
    # LOCATION
    # =================================================

    if "location" in df.columns:

        df["location"] = (
            df["location"]
            .astype("string")
            .str.title()
        )

    # =================================================
    # BANK NAME
    # =================================================

    bank_mapping = {
        "HDFCBANK": "HDFC Bank",
        "HDFC BANK": "HDFC Bank",
        "HDFC BANK LTD": "HDFC Bank",
        "HDFC BANK LIMITED": "HDFC Bank",
        "SBI": "State Bank Of India",
        "STATE BANK OF INDIA": "State Bank Of India",
        "ICICI BANK": "ICICI Bank",
        "ICICI BANK LIMITED": "ICICI Bank",
        "AXIS BANK": "Axis Bank",
        "AXIS BANK LTD": "Axis Bank",
        "BANK OF BARODA": "Bank Of Baroda",
        "BANKOFBARODA": "Bank Of Baroda",
        "BANK OF INDIA": "Bank Of India",
        "KOTAK BANK": "Kotak Mahindra Bank",
        "KOTAK MAHINDRA BANK LIMITED":
            "Kotak Mahindra Bank"
    }

    if "bank_name" in df.columns:

        df["bank_name"] = (
            df["bank_name"]
            .astype("string")
            .str.upper()
            .map(bank_mapping)
            .fillna(
                df["bank_name"]
                .astype("string")
                .str.title()
            )
        )

    # =================================================
    # TAX STATUS
    # =================================================

    tax_mapping = {
        "I": "Individual",
        "1": "Individual",
        "INDIVIDUAL": "Individual",
        "N": "NRI",
        "NRI - REPATRIATION":
            "NRI - Repatriation"
    }

    if "tax_status" in df.columns:

        df["tax_status"] = (
            df["tax_status"]
            .astype("string")
            .str.upper()
            .map(tax_mapping)
            .fillna(df["tax_status"])
        )

    # =================================================
    # PAN
    # =================================================

    if "pan" in df.columns:

        df["pan"] = (
            df["pan"]
            .astype("string")
            .str.upper()
        )

    # =================================================
    # EMAIL
    # =================================================

    if "email" in df.columns:

        df["email"] = (
            df["email"]
            .astype("string")
            .str.lower()
        )

    # =================================================
    # PHONE CLEANING
    # =================================================

    for col in [
        "mobile",
        "rphone",
        "ophone"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.replace(" ", "", regex=False)
                .str.replace("-", "", regex=False)
            )

    # =================================================
    # DATE COLUMNS
    # =================================================

    for col in [
        "trade_date",
        "post_date",
        "report_date",
        "purdate",
        "chqdate",
        "sys_regn_d"
    ]:

        if col in df.columns:

            df[col] = (
                pd.to_datetime(
                    df[col],
                    errors="coerce",
                    dayfirst=True
                )
                .dt.date
            )

    # =================================================
    # NUMERIC COLUMNS
    # =================================================

    for col in [
        "units",
        "amount",
        "load_amount",
        "broker_percent",
        "broker_commission",
        "purprice",
        "stamp_duty"
    ]:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # =================================================
    # EMPTY → NULL
    # =================================================

    df = df.replace(
        r"^\s*$",
        pd.NA,
        regex=True
    )

    return df


# =====================================================
# SIP MASTER TRANSFORMATION
# =====================================================

def transform_sip_master(df):

    df = df.copy()

    # =================================================
    # REMOVE EXACT DUPLICATES
    # =================================================

    df = df.drop_duplicates()

    # =================================================
    # TRIM STRING COLUMNS
    # =================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns

    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    # =================================================
    # SCHEME ID MAPPING
    #
    # product_code
    # → scheme_mapping.rta_scheme_code
    # → scheme_mapping.scheme_id
    # =================================================

    df = map_scheme_id(
        df,
        "product_code"
    )

    # =================================================
    # IDENTIFIER COLUMNS
    # =================================================

    for col in [
        "inv_iin",
        "inv_dp_id",
        "inv_client_id",
        "ecsno",
        "umrncode",
        "instrm_no",
        "cheq_micr_no",
        "request_ref_no",
        "ft_sip_regno"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .apply(
                    lambda x:
                    str(int(x))
                    if isinstance(x, float)
                    and not pd.isna(x)
                    else x
                )
                .astype("string")
                .str.strip()
            )

    # =================================================
    # TITLE CASE COLUMNS
    # =================================================

    for col in [
        "location",
        "investor_name",
        "agent_name",
        "subbroker",
        "scheme_name",
        "to_scheme_name",
        "ecs_bank_name",
        "ecs_holder_name",
        "dp_inv_name"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.title()
            )

    # =================================================
    # PAN
    # =================================================

    if "pan" in df.columns:

        df["pan"] = (
            df["pan"]
            .astype("string")
            .str.upper()
        )

    # =================================================
    # UPPER CASE COLUMNS
    # =================================================

    for col in [
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
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.upper()
                .str.strip()
            )

    # =================================================
    # PLAN MAPPING
    # =================================================

    plan_mapping = {
        "REGULAR": "Regular",
        "DIRECT": "Direct"
    }

    for col in [
        "plan",
        "to_plan"
    ]:

        if col in df.columns:

            df[col] = (
                df[col]
                .astype("string")
                .str.upper()
                .map(plan_mapping)
                .fillna(
                    df[col]
                    .astype("string")
                    .str.title()
                )
            )

    # =================================================
    # SIP TYPE
    # =================================================

    if "sip_type" in df.columns:

        df["sip_type"] = (
            df["sip_type"]
            .astype("string")
            .str.title()
        )

    # =================================================
    # SIP MODE
    # =================================================

    sip_mode_mapping = {
        "AUTO-DEBIT": "Auto Debit",
        "AUTO DEBIT": "Auto Debit",
        "NACH": "NACH",
        "ECS": "ECS"
    }

    if "sip_mode" in df.columns:

        df["sip_mode"] = (
            df["sip_mode"]
            .astype("string")
            .str.upper()
            .map(sip_mode_mapping)
            .fillna(
                df["sip_mode"]
                .astype("string")
                .str.title()
            )
        )

    # =================================================
    # FREQUENCY
    # =================================================

    if "frequency" in df.columns:

        df["frequency"] = (
            df["frequency"]
            .astype("string")
            .str.title()
        )

    # =================================================
    # TRANSACTION TYPE
    # =================================================

    if "trtype" in df.columns:

        df["trtype"] = (
            df["trtype"]
            .astype("string")
            .str.title()
        )

    # =================================================
    # STATUS
    # =================================================

    if "status" in df.columns:

        df["status"] = (
            df["status"]
            .astype("string")
            .str.title()
        )

    # =================================================
    # MODIFY FLAG
    # =================================================

    modify_mapping = {
        "Y": "Yes",
        "N": "No"
    }

    if "modify_flag" in df.columns:

        df["modify_flag"] = (
            df["modify_flag"]
            .astype("string")
            .str.upper()
            .map(modify_mapping)
            .fillna(
                df["modify_flag"]
            )
        )

    # =================================================
    # ACCOUNT NUMBER
    # =================================================

    if "ecs_acno" in df.columns:

        df["ecs_acno"] = (
            df["ecs_acno"]
            .astype("string")
            .str.replace(
                " ",
                "",
                regex=False
            )
        )

    # =================================================
    # NUMERIC COLUMNS
    # =================================================

    for col in [
        "amount",
        "no_of_installments"
    ]:

        if col in df.columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # =================================================
    # EMPTY → NULL
    # =================================================

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
        include=[
            "float16",
            "float32",
            "float64"
        ]
    ).columns

    for col in float_cols:

        df[col] = df[col].round(4)

    return df


# =====================================================
# LOAD SILVER LAYER
# =====================================================

def load_silver():

    print("=" * 80)
    print("STARTING BRONZE → SILVER ETL")
    print("=" * 80)

    # =================================================
    # INVESTOR MASTER
    # =================================================

    print()
    print("=" * 80)
    print("PROCESSING INVESTOR MASTER")
    print("=" * 80)

    investor_df = safe_read(
        """
        SELECT *
        FROM bronze.investor_master
        WHERE flag = 0
        """
    )

    print(
        "Bronze Investor rows :",
        len(investor_df)
    )

    if not investor_df.empty:

        investor_df = transform_investor_master(
            investor_df
        )

        # -------------------------------------------------
        # OCCUPATION MAPPING
        # -------------------------------------------------

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

        if "occupation" in investor_df.columns:

            investor_df["occupation"] = (
                investor_df["occupation"]
                .astype("string")
                .str.upper()
                .str.strip()
                .replace(occupation_mapping)
            )

            investor_df["occupation"] = (
                pd.to_numeric(
                    investor_df["occupation"],
                    errors="coerce"
                )
                .astype("Int64")
            )

        investor_df = round_decimal_columns(
            investor_df
        )

        append_new_rows(
            investor_df,
            "investor_master"
        )

    else:

        print(
            "No Investor Bronze rows "
            "with flag = 0."
        )

    # =================================================
    # TRANSACTION MASTER
    # =================================================

    print()
    print("=" * 80)
    print("PROCESSING TRANSACTION MASTER")
    print("=" * 80)

    transaction_df = safe_read(
        """
        SELECT *
        FROM bronze.transaction_master_new
        WHERE flag = 0
        """
    )

    print(
        "Bronze Transaction rows :",
        len(transaction_df)
    )

    if not transaction_df.empty:

        transaction_df = transform_transaction(
            transaction_df
        )

        transaction_df = round_decimal_columns(
            transaction_df
        )

        append_new_rows(
            transaction_df,
            "transaction_master_new"
        )

    else:

        print(
            "No Transaction Bronze rows "
            "with flag = 0."
        )

    # =================================================
    # SIP MASTER
    # =================================================

    print()
    print("=" * 80)
    print("PROCESSING SIP MASTER")
    print("=" * 80)

    sip_df = safe_read(
        """
        SELECT *
        FROM bronze.sip_master_new
        WHERE flag = 0
        """
    )

    print(
        "Bronze SIP rows :",
        len(sip_df)
    )

    if not sip_df.empty:

        sip_df = transform_sip_master(
            sip_df
        )

        sip_df = round_decimal_columns(
            sip_df
        )

        append_new_rows(
            sip_df,
            "sip_master_new"
        )

    else:

        print(
            "No SIP Bronze rows "
            "with flag = 0."
        )

    print()
    print("=" * 80)
    print("SILVER LAYER LOADED SUCCESSFULLY")
    print("=" * 80)


# =====================================================
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":

    load_silver()