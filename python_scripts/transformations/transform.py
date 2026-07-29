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
            SELECT MAX(created_at) AS last_time
            FROM silver.{table_name}
            """,
            engine
        )


        last_time = result.iloc[0]["last_time"]


        if pd.isna(last_time):

            return pd.Timestamp("1900-01-01", tz="UTC")

        return pd.to_datetime(
            last_time
        )


    except Exception:

        return pd.Timestamp("1900-01-01", tz="UTC")



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
# NORMALIZE DATA FOR DUPLICATE CHECK
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
# CREATE ROW HASH KEY
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
# APPEND ONLY NEW DATA TO SILVER
# USING TIMESTAMP + FLAG LOGIC
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



    # -------------------------------------------------
    # GET LAST SILVER LOAD TIME
    # -------------------------------------------------

    last_time = get_last_processed_time(
        table_name
    )



    # -------------------------------------------------
    # FILTER BRONZE DATA BY TIMESTAMP
    # -------------------------------------------------

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )


    df = df[
        df["created_at"] > last_time
    ]



    if df.empty:

        print(
            f"{table_name} : No new timestamp records"
        )

        return




    # -------------------------------------------------
    # CHECK EXISTING SILVER DATA
    # -------------------------------------------------

    try:

        existing = pd.read_sql(
            f"""
            SELECT *
            FROM silver.{table_name}
            """,
            engine
        )


    except Exception:


        existing = pd.DataFrame()



    # -------------------------------------------------
    # REMOVE DUPLICATES
    # -------------------------------------------------

    if not existing.empty:


        old_keys = set(
            create_row_key(existing)
        )


        new_keys = create_row_key(df)


        df = df.loc[
            ~new_keys.isin(old_keys)
        ]



    if df.empty:


        print(
            f"{table_name} : Duplicate data skipped"
        )

        return




    # -------------------------------------------------
    # SILVER AUDIT TIMESTAMP
    # -------------------------------------------------

    load_time = pd.Timestamp.now()


    df["created_at"] = load_time

    df["updated_at"] = load_time



    df = df.drop(
        columns=[
            "flag"
        ],
        errors="ignore"
    )



    # -------------------------------------------------
    # MATCH DATABASE COLUMNS
    # -------------------------------------------------

    db_cols = get_table_columns(
        table_name
    )


    for col in db_cols:

        if col not in df.columns:

            df[col] = None



    df = df[db_cols]




    # -------------------------------------------------
    # INSERT INTO SILVER
    # -------------------------------------------------

    df.to_sql(
        table_name,
        engine,
        schema="silver",
        if_exists="append",
        index=False,
        method="multi",
        chunksize=5000
    )



    print(
        f"{table_name} : {len(df)} rows inserted into Silver"
    )

    # =====================================================
# LOAD SILVER LAYER
# =====================================================

def load_silver():


    # =====================================================
    # INVESTOR MASTER
    # =====================================================

    investor_df = safe_read(
        """
        SELECT *
        FROM bronze.investor_master
        WHERE flag = 0
        """
    )


    if not investor_df.empty:


        investor_df = transform_investor_master(
            investor_df
        )


        # Occupation mapping

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


            investor_df["occupation"] = pd.to_numeric(
                investor_df["occupation"],
                errors="coerce"
            ).astype("Int64")



        investor_df = round_decimal_columns(
            investor_df
        )


        append_new_rows(
            investor_df,
            "investor_master"
        )



    # =====================================================
    # TRANSACTION MASTER
    # =====================================================


    transaction_df = safe_read(
        """
        SELECT *
        FROM bronze.transaction_master_new
        WHERE flag = 0
        """
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




    # =====================================================
    # SIP MASTER
    # =====================================================


    sip_df = safe_read(
        """
        SELECT *
        FROM bronze.sip_master_new
        WHERE flag = 0
        """
    )


    if not sip_df.empty:


        sip_df = transform_sip_master(
            sip_df
        )


        sip_df = round_decimal_columns(
            sip_df
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

                sip_df[col] = (
                    sip_df[col]
                    .astype("string")
                    .str.strip()
                )



        append_new_rows(
            sip_df,
            "sip_master_new"
        )



    print(
        "\nSilver Layer Loaded Successfully"
    )

    # =====================================================
# INVESTOR MASTER TRANSFORMATION
# =====================================================

def transform_investor_master(df):


    df = df.copy()



    # =====================================================
    # REMOVE EXACT DUPLICATES
    # =====================================================

    df = df.drop_duplicates()



    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns


    for col in object_cols:

        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )



    # =====================================================
    # STATE MAPPING
    # =====================================================

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



        # STATE NAME -> CODE

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



        # CODE -> STATE NAME

        if "gst_state_code" in df.columns:


            mapped_state = (
                df["gst_state_code"]
                .map(code_lookup)
            )


            if "state" in df.columns:

                df["state"] = (
                    mapped_state
                    .combine_first(df["state"])
                )

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
            .astype("string")
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
            .astype("string")
            .str.upper()
            .map(tax_mapping)
            .fillna(df["tax_status"])
        )




    # =====================================================
    # HOLDING NATURE
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




    # =====================================================
    # PAN CLEAN
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
                .astype("string")
                .str.upper()
            )




    # =====================================================
    # EMAIL CLEAN
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
                .astype("string")
                .str.lower()
            )




    # =====================================================
    # PHONE CLEAN
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
                .astype("string")
                .str.replace(
                    " ",
                    "",
                    regex=False
                )
                .str.replace(
                    "-",
                    "",
                    regex=False
                )
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
    # EMPTY STRING TO NULL
    # =====================================================

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



    # =====================================================
    # REMOVE EXACT DUPLICATES
    # =====================================================

    df = df.drop_duplicates()



    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns


    for col in object_cols:


        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )




    # =====================================================
    # STATE MAPPING
    # =====================================================

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



        # STATE -> GST CODE

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




        # GST CODE -> STATE

        if "gst_state_code" in df.columns:


            mapped_state = (
                df["gst_state_code"]
                .map(code_lookup)
            )


            if "state" in df.columns:


                df["state"] = (
                    mapped_state
                    .combine_first(df["state"])
                )

            else:

                df["state"] = mapped_state





    # =====================================================
    # SOURCE SYSTEM
    # =====================================================

    if "source_system" in df.columns:


        df["source_system"] = (
            df["source_system"]
            .astype("string")
            .str.upper()
        )




    # =====================================================
    # LOCATION
    # =====================================================

    if "location" in df.columns:


        df["location"] = (
            df["location"]
            .astype("string")
            .str.title()
        )




    # =====================================================
    # BANK NAME MAPPING
    # =====================================================

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




    # =====================================================
    # TAX STATUS
    # =====================================================

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




    # =====================================================
    # PAN
    # =====================================================

    if "pan" in df.columns:


        df["pan"] = (
            df["pan"]
            .astype("string")
            .str.upper()
        )




    # =====================================================
    # EMAIL
    # =====================================================

    if "email" in df.columns:


        df["email"] = (

            df["email"]
            .astype("string")
            .str.lower()

        )




    # =====================================================
    # PHONE CLEANING
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
                .astype("string")
                .str.replace(
                    " ",
                    "",
                    regex=False
                )
                .str.replace(
                    "-",
                    "",
                    regex=False
                )

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

def transform_sip_master(df):


    df = df.copy()



    # =====================================================
    # REMOVE EXACT DUPLICATES
    # =====================================================

    df = df.drop_duplicates()




    # =====================================================
    # TRIM STRING COLUMNS
    # =====================================================

    object_cols = df.select_dtypes(
        include="object"
    ).columns


    for col in object_cols:


        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )




    # =====================================================
    # IDENTIFIER COLUMNS AS STRING
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
                    lambda x:

                    str(int(x))
                    if isinstance(x, float)
                    and not pd.isna(x)

                    else x

                )
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
                .astype("string")
                .str.title()

            )




    # =====================================================
    # PAN CLEAN
    # =====================================================

    if "pan" in df.columns:


        df["pan"] = (

            df["pan"]
            .astype("string")
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
    # PLAN MAPPING
    # =====================================================

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




    # =====================================================
    # SIP TYPE
    # =====================================================

    if "sip_type" in df.columns:


        df["sip_type"] = (

            df["sip_type"]
            .astype("string")
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
            .astype("string")
            .str.upper()
            .map(sip_mode_mapping)
            .fillna(
                df["sip_mode"]
                .astype("string")
                .str.title()
            )

        )




    # =====================================================
    # FREQUENCY
    # =====================================================

    if "frequency" in df.columns:


        df["frequency"] = (

            df["frequency"]
            .astype("string")
            .str.title()

        )




    # =====================================================
    # TRANSACTION TYPE
    # =====================================================

    if "trtype" in df.columns:


        df["trtype"] = (

            df["trtype"]
            .astype("string")
            .str.title()

        )




    # =====================================================
    # STATUS
    # =====================================================

    if "status" in df.columns:


        df["status"] = (

            df["status"]
            .astype("string")
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
            .astype("string")
            .str.upper()
            .map(modify_mapping)
            .fillna(df["modify_flag"])

        )




    # =====================================================
    # ACCOUNT NUMBER CLEAN
    # =====================================================

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
# MAIN EXECUTION
# =====================================================

if __name__ == "__main__":

    load_silver()