import pandas as pd
from silver.silver_helpers import load_state_dimension

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
