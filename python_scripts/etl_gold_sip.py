import pandas as pd
import traceback

from datetime import datetime, timezone

from utils.db import engine



# =====================================================
# EXTRACT GOLD SIP DATA
# =====================================================


def extract_sip():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD SIP")
    print("=" * 80)



    query = """

    SELECT

        *

    FROM silver.sip_master_new

    """



    df = pd.read_sql(
        query,
        engine
    )



    print("\nExtraction Completed")
    print("-" * 80)


    print(
        f"Rows fetched : {len(df)}"
    )


    print(
        f"Columns fetched : {len(df.columns)}"
    )



    print("\nSample Data")
    print("-" * 80)


    print(
        df.head()
    )


    return df

# =====================================================
# TRANSFORM GOLD SIP DATA
# =====================================================


def transform_sip(df):

    print("=" * 80)
    print("TRANSFORMING DATA FOR GOLD SIP")
    print("=" * 80)



    gold_df = pd.DataFrame()



    # =====================================================
    # RTA
    # =====================================================


    gold_df["rta"] = (

        df["source"]

        .astype("string")

        .str.strip()

        .str.upper()

    )



    # =====================================================
    # SIP REGISTRATION NUMBER
    # Natural Key
    # =====================================================


    gold_df["sip_reg_no"] = (

        df["ft_sip_regno"]

        .combine_first(df["request_ref_no"])

        .astype("string")

        .str.strip()

        .str.replace(".0", "", regex=False)

    )


    gold_df["sip_reg_no"] = (

        gold_df["sip_reg_no"]

        .replace(

            {

                "": pd.NA,

                "nan": pd.NA,

                "<NA>": pd.NA

            }

        )

    )



    # =====================================================
    # FOLIO NUMBER
    # =====================================================


    gold_df["folio_number"] = (

        df["folio_no"]

        .astype("string")

        .str.strip()

        .str.replace(".0", "", regex=False)

    )


    gold_df.loc[

        gold_df["folio_number"] == "",

        "folio_number"

    ] = None



    # =====================================================
    # SCHEME CODE
    # =====================================================


    gold_df["scheme_code"] = (

        df["scheme_code"]

        .astype("string")

        .str.strip()

    )



    # =====================================================
    # SCHEME NAME
    # =====================================================


    gold_df["scheme_name"] = (

        df["scheme_name"]

        .astype("string")

        .str.strip()

    )



    # =====================================================
    # AMC CODE
    # =====================================================


    gold_df["amc_code"] = (

        df["amc_code"]

        .astype("string")

        .str.strip()

    )

    # =====================================================
    # ARN
    # =====================================================

    gold_df["arn"] = (

        df["sub_arn_code"]

        .astype("string")

        .str.strip()

    )

    # =====================================================
    # SUB ARN
    # =====================================================

    gold_df["sub_arn"] = (

        df["subbroker"]

        .astype("string")

        .str.strip()

    )



    # =====================================================
    # ISIN
    # =====================================================


    gold_df["isin"] = None



    # =====================================================
    # SIP AMOUNT
    # =====================================================


    gold_df["amount"] = pd.to_numeric(

        df["auto_amount"],

        errors="coerce"

    )



    # =====================================================
    # FREQUENCY
    # =====================================================


    gold_df["frequency"] = (

        df["periodicity"]

        .astype("string")

        .str.strip()

        .str.upper()

    )



    # =====================================================
    # START DATE
    # =====================================================


    gold_df["start_date"] = (

        pd.to_datetime(

            df["from_date"],

            errors="coerce"

        )

        .dt.date

    )



    # =====================================================
    # END DATE
    # =====================================================


    gold_df["end_date"] = (

        pd.to_datetime(

            df["to_date"],

            errors="coerce"

        )

        .dt.date

    )



    # =====================================================
    # NEXT DUE DATE
    # =====================================================


    gold_df["next_due_date"] = None



    # =====================================================
    # SIP DAY
    # =====================================================


    gold_df["sip_day"] = pd.to_numeric(

        df["period_day"],

        errors="coerce"

    )



    # =====================================================
    # MANDATE ID
    # =====================================================


    gold_df["mandate_id"] = (

        df["umrn_code"]

        .astype("string")

        .str.strip()

    )



    # =====================================================
    # STATUS
    # =====================================================


    gold_df["status"] = (

        df["status"]

        .astype("string")

        .str.strip()

        .str.upper()

        .str[:20]

    )



    # =====================================================
    # REGISTERED DATE
    # =====================================================


    gold_df["registered_date"] = (

        pd.to_datetime(

            df["reg_date"],

            errors="coerce"

        )

        .dt.date

    )



    # =====================================================
    # CEASED DATE
    # =====================================================


    gold_df["ceased_date"] = (

        pd.to_datetime(

            df["cease_date"],

            errors="coerce"

        )

        .dt.date

    )

        # =====================================================
    # APPLICATION MANAGED COLUMNS
    # =====================================================


    gold_df["scheme_id"] = None

    gold_df["amc_id"] = None

    gold_df["client_id"] = None



    gold_df["sip_type"] = None


    gold_df["registered_installments"] = None

    gold_df["completed_installments"] = None

    gold_df["bounced_installments"] = None


    gold_df["ceased_reason"] = None


    gold_df["arn_id"] = None



    # =====================================================
    # CREATED AT
    # =====================================================


    gold_df["created_at"] = datetime.now()



    # =====================================================
    # COLUMN LENGTH CLEANING
    # =====================================================


    gold_df["rta"] = (

        gold_df["rta"]

        .astype("string")

        .str[:10]

    )


    gold_df["sip_reg_no"] = (

        gold_df["sip_reg_no"]

        .where(

            gold_df["sip_reg_no"].isna(),

            gold_df["sip_reg_no"]

            .astype(str)

            .str[:50]

        )

    )


    gold_df["folio_number"] = (

        gold_df["folio_number"]

        .where(

            gold_df["folio_number"].isna(),

            gold_df["folio_number"]

            .astype(str)

            .str[:40]

        )

    )


    gold_df["scheme_code"] = (

        gold_df["scheme_code"]

        .astype("string")

        .str[:30]

    )


    gold_df["scheme_name"] = (

        gold_df["scheme_name"]

        .astype("string")

        .str[:255]

    )


    gold_df["amc_code"] = (

        gold_df["amc_code"]

        .astype("string")

        .str[:20]

    )


    gold_df["isin"] = (

        gold_df["isin"]

        .astype("string")

        .str[:20]

    )


    gold_df["frequency"] = (

        gold_df["frequency"]

        .astype("string")

        .str[:20]

    )


    gold_df["mandate_id"] = (

        gold_df["mandate_id"]

        .astype("string")

        .str[:50]

    )


    gold_df["status"] = (

        gold_df["status"]

        .astype("string")

        .str[:20]

    )


    gold_df["sip_type"] = (

        gold_df["sip_type"]

        .astype("string")

        .str[:20]

    )


    gold_df["ceased_reason"] = (

        gold_df["ceased_reason"]

        .astype("string")

        .str[:100]

    )

    gold_df["arn"] = (

        gold_df["arn"]

        .astype("string")

        .str[:20]

    )

    gold_df["sub_arn"] = (

        gold_df["sub_arn"]

        .astype("string")

        .str[:20]

    )



    # =====================================================
    # FINAL COLUMN ORDER
    # =====================================================


    gold_df = gold_df[

        [

            "rta",

            "sip_reg_no",

            "folio_number",

            "scheme_code",

            "scheme_name",

            "amc_code",

            "isin",

            "amount",

            "frequency",

            "start_date",

            "end_date",

            "next_due_date",

            "sip_day",

            "mandate_id",

            "status",

            "registered_date",

            "ceased_date",

            "scheme_id",

            "amc_id",

            "client_id",

            "sip_type",

            "registered_installments",

            "completed_installments",

            "bounced_installments",

            "ceased_reason",

            "arn",

            "sub_arn",

            "arn_id",

            "created_at"

        ]

    ]



    print("=" * 80)

    print("GOLD SIP PREVIEW")

    print("=" * 80)


    print(

        gold_df.head()

    )


    print(

        "\nTotal Gold SIP Rows:",

        len(gold_df)

    )


    return gold_df

# =====================================================
# LOAD GOLD SIP DATA
# =====================================================


def load_sip(gold_df):

    print("=" * 80)
    print("LOADING DATA INTO GOLD.SIP")
    print("=" * 80)



    # =====================================================
    # VARCHAR VALIDATION
    # =====================================================


    varchar_limits = {

        "rta":10,

        "sip_reg_no":50,

        "folio_number":40,

        "scheme_code":30,

        "scheme_name":255,

        "amc_code":20,

        "isin":20,

        "frequency":20,

        "mandate_id":50,

        "status":20,

        "sip_type":20,

        "arn":20,

        "sub_arn":20,

        "ceased_reason":100

    }



    for col, limit in varchar_limits.items():


        if col in gold_df.columns:


            max_length = (

                gold_df[col]

                .fillna("")

                .astype(str)

                .str.len()

                .max()

            )


            print(

                f"{col:<25} Max Length : {max_length}"

            )


            if max_length > limit:


                raise Exception(

                    f"{col} length {max_length} exceeds limit {limit}"

                )



    # =====================================================
    # TIMESTAMP DUPLICATE CHECK
    # Natural Key:
    # rta + sip_reg_no
    # =====================================================


    print()

    print("Checking existing gold SIP records")



    existing_sip = pd.read_sql(

        """

        SELECT

            rta,

            sip_reg_no,

            created_at

        FROM gold.sip

        """,

        engine

    )



    print(

        "Existing SIP records:",

        len(existing_sip)

    )



    if len(existing_sip) > 0:



        existing_sip["rta"] = (

            existing_sip["rta"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



        existing_sip["sip_reg_no"] = (

            existing_sip["sip_reg_no"]

            .fillna("")

            .astype(str)

            .str.strip()

        )



        gold_df["rta"] = (

            gold_df["rta"]

            .fillna("")

            .astype(str)

            .str.strip()

            .str.upper()

        )



        gold_df["sip_reg_no"] = (

            gold_df["sip_reg_no"]

            .fillna("")

            .astype(str)

            .str.strip()

        )



        compare_df = gold_df.merge(

            existing_sip,

            on=[

                "rta",

                "sip_reg_no"

            ],

            how="left",

            suffixes=(

                "_new",

                "_old"

            )

        )



        compare_df = compare_df[

            compare_df["created_at_old"].isna()

            |

            (

                compare_df["created_at_new"]

                >

                compare_df["created_at_old"]

            )

        ]



        gold_df = compare_df[

            gold_df.columns

        ]



    print(

        "Rows after timestamp duplicate check:",

        len(gold_df)

    )



    if len(gold_df) == 0:


        print(

            "No new SIP records to insert"

        )


        return True



    # =====================================================
    # INSERT INTO GOLD SIP
    # =====================================================


    try:


        gold_df.to_sql(

            name="sip",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print()

        print(

            f"Inserted Rows : {len(gold_df)}"

        )


        return True



    except Exception as e:


        print("\nFAILED LOADING GOLD SIP\n")


        traceback.print_exc(limit=5)



        if hasattr(e, "orig"):

            print("\n========== POSTGRES ERROR ==========")

            print(e.orig)

            print("====================================")



        return False

# =====================================================
# MAIN EXECUTION
# =====================================================


if __name__ == "__main__":


    print()

    print("=" * 80)

    print("STARTING GOLD SIP ETL")

    print("=" * 80)



    # =====================================================
    # EXTRACT
    # =====================================================


    df = extract_sip()



    # =====================================================
    # TRANSFORM
    # =====================================================


    gold_df = transform_sip(

        df

    )



    # =====================================================
    # LOAD
    # =====================================================


    status = load_sip(

        gold_df

    )



    # =====================================================
    # FINAL STATUS
    # =====================================================


    if status:


        print()

        print("=" * 80)

        print(

            "GOLD SIP ETL COMPLETED SUCCESSFULLY"

        )

        print("=" * 80)



    else:


        print()

        print("=" * 80)

        print(

            "GOLD SIP ETL FAILED"

        )

        print("=" * 80)