import pandas as pd

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
