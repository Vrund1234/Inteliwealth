import pandas as pd
from silver.silver_helpers import load_state_dimension

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
