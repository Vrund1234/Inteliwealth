
import pandas as pd
from utils.db import engine
from utils.db import master_engine
import uuid
from datetime import datetime, timezone
# from sqlalchemy import create_engine

# =====================================================

# DATABASE CONNECTION

# =====================================================



# engine = create_engine(

#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"

# )





# master_engine = create_engine(

#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/inteliwealth_sh"

# )


# =====================================================

# CONNECTION CHECK

# =====================================================



print(

    pd.read_sql(

        "SELECT current_database();",

        master_engine

    )

)

# =====================================================

# EXTRACT GOLD HOLDINGS DATA

# =====================================================





def extract_holdings():



    print("=" * 80)

    print("Extracting data for Gold Holdings")

    print("=" * 80)





    query = """



    WITH investor_base AS
    (
        SELECT DISTINCT ON
        (
            source,
            folio_no
        )
            source,
            folio_no,
            holding_nature,
            nominee1_name,
            nominee1_relation,
            nominee1_percentage,
            bank_name,
            bank_account_no,
            demat_flag,
            ckyc_no,
            broker_code,
            subbroker

        FROM silver.investor_master

        ORDER BY
            source,
            folio_no
    )

    SELECT

        t.*,

        t.brokcode AS transaction_broker_code,

        t.src_brk_code AS transaction_sub_arn,

        i.holding_nature AS investor_holding_nature,

        i.nominee1_name AS investor_nominee_name,

        i.nominee1_relation AS investor_nominee_relation,

        i.nominee1_percentage AS investor_nominee_percentage,

        i.bank_name AS investor_bank_name,

        i.bank_account_no AS investor_bank_account_no,

        i.demat_flag AS investor_demat_flag,

        i.ckyc_no AS investor_ckyc_no,

        i.broker_code AS investor_broker_code,

        i.subbroker AS investor_subbroker

    FROM silver.transaction_master_new t

    LEFT JOIN investor_base i

    ON t.source = i.source

    AND t.folio_no = i.folio_no



    """


    df = pd.read_sql(

        query,

        engine

    )





    print("\nExtraction Completed")

    print("-" * 80)



    print(

        "Rows fetched:",

        len(df)

    )



    print(

        "Columns fetched:",

        len(df.columns)

    )





    print(df.head())





    return df











# =====================================================

# NORMALIZE SCHEME NAME

# =====================================================





def normalize_scheme_name(series):



    return (



        series

        .fillna("")

        .astype(str)

        .str.upper()

        .str.replace("-", " ", regex=False)

        .str.replace("PLAN", "", regex=False)

        .str.replace("OPTION", "", regex=False)

        .str.replace("GROWTH", "GR", regex=False)

        .str.replace("DIRECT", "DIR", regex=False)

        .str.replace("REGULAR", "REG", regex=False)

        .str.replace(r"[^A-Z0-9]", "", regex=True)



    )











# =====================================================

# TRANSFORM GOLD HOLDINGS DATA

# =====================================================





def transform_holdings(df):





    print("=" * 80)

    print("Transforming Gold Holdings")

    print("=" * 80)







    gold_df = pd.DataFrame()







    gold_df["id"] = [



        uuid.uuid4()



        for _ in range(len(df))



    ]







    # =====================================================

    # CLEAN TRANSACTION PROD CODE

    # =====================================================





    df["prodcode"] = (



        df["prodcode"]



        .fillna("")



        .astype(str)



        .str.strip()



        .str.upper()



    )



    # =====================================================

    # LOAD GOLD SCHEME BRIDGE

    # transaction.prodcode -> gold.scheme.scheme_code

    # gold.scheme.amfi_code -> scheme_master

    # =====================================================





    gold_scheme = pd.read_sql(



        """



        SELECT



            id,

            rta,

            scheme_code,

            amfi_code,

            isin



        FROM gold.scheme



        """,



        engine



    )





    print(

        "Gold Scheme Rows:",

        len(gold_scheme)

    )







    # =====================================================

    # CLEAN KEYS

    # =====================================================





    df["prodcode"] = (



        df["prodcode"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()



    )







    gold_scheme["scheme_code"] = (



        gold_scheme["scheme_code"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()



    )







    gold_scheme["rta"] = (



        gold_scheme["rta"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()



    )







    # =====================================================

    # PROD CODE -> GOLD SCHEME ID

    # =====================================================





    df = df.merge(



        gold_scheme[



            [



                "id",

                "rta",

                "scheme_code"



            ]



        ],



        left_on=[



            "source",

            "prodcode"



        ],



        right_on=[



            "rta",

            "scheme_code"



        ],



        how="left"



    )







    df.rename(



        columns={



            "id":"scheme_id"



        },



        inplace=True



    )







    print("="*80)

    print("SCHEME ID VALIDATION")

    print("="*80)







    print(

        "Total Rows:",

        len(df)

    )







    print(

        "Matched scheme_id:",

        df["scheme_id"].notna().sum()

    )







    print(

        "Missing scheme_id:",

        df["scheme_id"].isna().sum()

    )







    print("\nMissing Samples")





    print(



        df.loc[



            df["scheme_id"].isna(),



            [



                "source",

                "prodcode",

                "scheme",

                "funddesc"



            ]



        ]



        .drop_duplicates()



        .head(20)



    )





    print("="*80)

    print("SCHEME ID VALIDATION")

    print("="*80)





    print(

        "Total Rows:",

        len(df)

    )





    print(

        "Matched scheme_id:",

        df["scheme_id"].notna().sum()

    )





    print(

        "Missing scheme_id:",

        df["scheme_id"].isna().sum()

    )





    print("\nMissing Samples")





    print(



        df.loc[



            df["scheme_id"].isna(),



            [



                "source",



                "prodcode",



                "scheme",



                "funddesc"



            ]



        ]



        .drop_duplicates()



        .head(20)



    )



    # =====================================================

    # RTA

    # =====================================================



    gold_df["rta"] = (



        df["source"]



        .fillna("")



        .astype(str)



        .str.strip()



        .str.upper()



    )







    # =====================================================

    # PAN

    # =====================================================



    gold_df["pan"] = (



        df["pan"]



        .astype("string")



        .str.strip()



        .str.upper()



        .str.replace(".0", "", regex=False)



    )





    gold_df.loc[



        gold_df["pan"].isna()



        |



        (gold_df["pan"].str.len() != 10),



        "pan"



    ] = None







    # =====================================================

    # FOLIO NUMBER

    #

    # Priority:

    # 1. folio_no

    # 2. scheme_folio_number

    # =====================================================





    folio = (



        df["folio_no"]



        .astype("string")



        .str.strip()



        .str.replace(".0", "", regex=False)



    )





    scheme_folio = (



        df["scheme_folio_number"]



        .astype("string")



        .str.strip()



        .str.replace(".0", "", regex=False)



    )





    gold_df["folio_number"] = (



        folio



        .fillna(scheme_folio)



    )





    gold_df.loc[



        gold_df["folio_number"] == "",



        "folio_number"



    ] = None











    # =====================================================

    # HOLDING VALUES

    # =====================================================





    gold_df["units"] = pd.to_numeric(



        df["units"],



        errors="coerce"



    )





    gold_df["market_value"] = pd.to_numeric(



        df["amount"],



        errors="coerce"



    )











    # =====================================================

    # DATES

    # =====================================================





    gold_df["as_on_date"] = (



        pd.to_datetime(



            df["rep_date"],



            errors="coerce"



        )



        .dt.date



    )







    gold_df["folio_date"] = (



        pd.to_datetime(



            df["traddate"],



            errors="coerce"



        )



        .dt.date



    )

    # =====================================================
    # ARN AND SUB ARN
    # =====================================================


    gold_df["arn"] = (
        df["transaction_broker_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df.loc[
        gold_df["arn"] == "",
        "arn"
    ] = None



    gold_df["sub_arn"] = (
        df["transaction_sub_arn"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    gold_df.loc[
        gold_df["sub_arn"] == "",
        "sub_arn"
    ] = None
    # =====================================================

    # HOLDING DETAILS

    # =====================================================

    gold_df["holding_nature"] = (


        df["investor_holding_nature"]



        .fillna("")



        .astype(str)



        .str.strip()



    )







    gold_df["nominee_name"] = (



        df["investor_nominee_name"]



        .fillna("")



        .astype(str)



        .str.strip()



    )







    gold_df["nominee_relation"] = (



        df["investor_nominee_relation"]



        .fillna("")



        .astype(str)



        .str.strip()



    )







    gold_df["nominee_pct"] = (



        df["investor_nominee_percentage"]



        .fillna("")



        .astype(str)



        .str.strip()



    )











    # =====================================================

    # KYC STATUS

    # =====================================================





    gold_df["kyc_status"] = None







    kyc_available = (



        df["investor_ckyc_no"]



        .fillna("")



        .astype(str)



        .str.strip()



        != ""



    )





    gold_df.loc[



        kyc_available,



        "kyc_status"



    ] = "Verified"













    # =====================================================

    # BANK DETAILS

    # =====================================================





    gold_df["bank_name"] = (



        df["investor_bank_name"]



        .fillna("")



        .astype(str)



        .str.strip()



    )







    gold_df.loc[



        gold_df["bank_name"] == "",



        "bank_name"



    ] = None









    gold_df["bank_ac_last4"] = (



        df["investor_bank_account_no"]



        .fillna("")



        .astype(str)



        .str.replace(".0", "", regex=False)



        .str.strip()



        .str[-4:]



    )







    gold_df.loc[



        gold_df["bank_ac_last4"] == "",



        "bank_ac_last4"



    ] = None

    # =====================================================

    # DEMAT

    # =====================================================

    gold_df["demat_flag"] = (

        df["investor_demat_flag"]

        .fillna("")

        .astype(str)

        .str.strip()



    )
    gold_df.loc[



        gold_df["demat_flag"] == "",



        "demat_flag"



    ] = None



        # =====================================================

    # APPLICATION MANAGED FIELDS

    # =====================================================





    gold_df["client_id"] = None





    gold_df["amc_id"] = None





    # Already created from scheme bridge



    gold_df["scheme_id"] = df["scheme_id"]







    gold_df["purchase_date"] = None





    gold_df["arn_id"] = None





    gold_df["avg_cost_nav"] = None





    gold_df["invested_amount"] = None





    gold_df["current_nav"] = None





    gold_df["current_value"] = None





    gold_df["nav_date"] = None





    gold_df["unrealised_gain"] = None





    gold_df["xirr"] = None





    gold_df["first_purchase_date"] = None





    gold_df["source_file_id"] = None











    # =====================================================

    # FINAL SCHEME ID VALIDATION

    # =====================================================





    print("=" * 80)



    print("FINAL SCHEME ID VALIDATION")



    print("=" * 80)







    print(



        "Total Holdings:",



        len(gold_df)



    )





    print(



        "Matched scheme_id:",



        gold_df["scheme_id"].notna().sum()



    )





    print(



        "Missing scheme_id:",



        gold_df["scheme_id"].isna().sum()



    )









    print("\nMissing Scheme Samples")







    print(



        df.loc[



            gold_df["scheme_id"].isna(),



            [



                "source",



                "prodcode",



                "scheme",



                "funddesc"



            ]



        ]



        .drop_duplicates()



        .head(20)



    )













    # =====================================================

    # LAST SYNC TIME

    # =====================================================





    gold_df["last_synced_at"] = datetime.now(



        timezone.utc



    )













    # =====================================================

    # FINAL GOLD.HOLDINGS COLUMN ORDER

    # =====================================================





    gold_df = gold_df[



        [



            "id",



            "rta",



            "pan",



            "folio_number",



            "units",



            "market_value",



            "as_on_date",



            "folio_date",



            "arn",
            "sub_arn",
            "holding_nature",
            "nominee_name",
            "nominee_relation",
            "nominee_pct",
            "kyc_status",
            "bank_name",
            "bank_ac_last4",
            "demat_flag",
            "client_id",
            "amc_id",
            "scheme_id",
            "purchase_date",
            "arn_id",
            "avg_cost_nav",
            "invested_amount",
            "current_nav",
            "current_value",
            "nav_date",
            "unrealised_gain",
            "xirr",
            "first_purchase_date",
            "source_file_id",
            "last_synced_at"

        ]

    ]

    # =====================================================

    # PREVIEW

    # =====================================================





    print("=" * 80)



    print("GOLD HOLDINGS PREVIEW")



    print("=" * 80)





    print(



        gold_df.head()



    )





    print(



        "Total Gold Rows:",



        len(gold_df)



    )







    return gold_df



# =====================================================

# LOAD GOLD HOLDINGS DATA

# =====================================================





def load_holdings(gold_df):





    print("=" * 80)



    print("Loading data into gold.holdings")



    print("=" * 80)









    # =====================================================

    # VARCHAR LENGTH VALIDATION

    # =====================================================





    varchar_limits = {





        "rta": 10,



        "pan": 10,



        "folio_number": 40,



        "arn": 20,
        "sub_arn":20,



        "holding_nature": 40,



        "nominee_name": 255,



        "nominee_relation": 40,



        "nominee_pct": 20,



        "kyc_status": 20,



        "bank_name": 120,



        "bank_ac_last4": 8,



        "demat_flag": 10



    }









    for col, limit in varchar_limits.items():





        if col in gold_df.columns:





            max_len = (



                gold_df[col]



                .fillna("")



                .astype(str)



                .str.len()



                .max()



            )







            print(



                f"{col:<25} Max Length : {max_len}"



            )







            if max_len > limit:





                raise ValueError(



                    f"{col} length {max_len} exceeds limit {limit}"



                )













    # =====================================================

    # DUPLICATE CHECK

    # =====================================================





    print("\nChecking duplicate holdings")







    duplicates = (



        gold_df



        .groupby(



            [



                "rta",



                "folio_number",



                "scheme_id"



            ]



        )



        .size()



        .reset_index(name="count")



        .query("count > 1")



    )







    print(



        "Duplicate records:",



        len(duplicates)



    )







    if len(duplicates) > 0:





        print(



            duplicates.head(10)



        )















    # =====================================================

    # LOAD TO POSTGRES

    # =====================================================





    try:





        gold_df.to_sql(



            name="holdings",



            con=engine,



            schema="gold",



            if_exists="append",



            index=False,



            method="multi",



            chunksize=5000



        )







        print()



        print(



            "Inserted Rows:",



            len(gold_df)



        )







        return True









    except Exception as e:





        print()



        print(



            "ERROR WHILE LOADING GOLD HOLDINGS"



        )





        print(



            type(e).__name__



        )





        print(e)







        return False













# =====================================================

# MAIN EXECUTION

# =====================================================





if __name__ == "__main__":





    print("\n")



    print("=" * 80)



    print("STARTING GOLD HOLDINGS ETL")



    print("=" * 80)









    # =================================================

    # EXTRACT

    # =================================================





    df = extract_holdings()









    # =================================================

    # TRANSFORM

    # =================================================





    gold_df = transform_holdings(df)









    # =================================================

    # LOAD

    # =================================================





    status = load_holdings(



        gold_df



    )











    if status:





        print("\n")



        print("=" * 80)



        print("GOLD HOLDINGS ETL COMPLETED SUCCESSFULLY")



        print("=" * 80)







    else:





        print("\n")



        print("=" * 80)



        print("GOLD HOLDINGS ETL FAILED")



        print("=" * 80)