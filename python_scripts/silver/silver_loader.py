import pandas as pd

from silver.silver_helpers import (
    safe_read,
    round_decimal_columns,
    append_new_rows
)

from silver.investor_master import transform_investor_master
from silver.transaction import transform_transaction
from silver.sip import transform_sip_master

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

if __name__ == "__main__":
    load_silver()
