import pandas as pd
# from sqlalchemy import create_engine
import traceback
from utils.db import engine, master_engine

# =====================================================

# DATABASE CONNECTION

# ====================================================

# engine = create_engine(

#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"

# )



# master_engine = create_engine(

#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/inteliwealth_sh"

# )

# =====================================================

# EXTRACT

# =====================================================



def extract_transactions():



    print("=" * 80)

    print("Reading data from silver.transaction_master_new")

    print("=" * 80)



    query = """

        SELECT *

        FROM silver.transaction_master_new

    """



    df = pd.read_sql(query, engine)



    print(f"Rows fetched : {len(df)}")

    print(f"Columns : {len(df.columns)}")

    return df



# =====================================================

# NORMALIZE SCHEME NAME

# =====================================================



def normalize_scheme_name(series):

    return (

        series.fillna("")

        .astype(str)

        .str.upper()

        .str.replace(" - ", " ", regex=False)

        .str.replace("-", " ", regex=False)

        .str.replace("PLAN", "", regex=False)

        .str.replace("OPTION", "", regex=False)

        .str.replace("GROWTH", "GR", regex=False)

        .str.replace("REGULAR", "REG", regex=False)

        .str.replace("DIRECT", "DIR", regex=False)

        .str.replace("IDCW", "DIV", regex=False)

        .str.replace("DIVIDEND", "DIV", regex=False)

        .str.replace(r"[^A-Z0-9]", "", regex=True)

    )



# =====================================================

# TRANSFORM (RENAME ONLY)

# =====================================================



def transform_transactions(df):



    # Step 1: Rename columns

    gold_df = pd.DataFrame()



    # Feed columns

    gold_df["rta"] = (

        df["source"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )

    gold_df["rta_txn_no"] = (

        df["trxnno"]

        .fillna("")

        .astype(str)

        .str.strip()

    )

    print("Before drop:", len(gold_df))

    print(gold_df["rta_txn_no"].isna().sum())

    gold_df.loc[gold_df["rta_txn_no"] == "", "rta_txn_no"] = None

    # gold_df = gold_df.dropna(subset=["rta_txn_no"])



    gold_df["pan"] = (

        df["pan"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

        .str.replace(".0", "", regex=False)

    )

    gold_df.loc[gold_df["pan"] == "", "pan"] = None



    gold_df["folio_number"] = (

        df["folio_no"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.replace(".0", "", regex=False)

    )

    gold_df.loc[gold_df["folio_number"] == "", "folio_number"] = None



    # Business fields

    gold_df["txn_type"] = df.apply(classify_transaction, axis=1)          # We'll derive this next

    gold_df["txn_type_raw"] = df["trxntype"]

    gold_df["txn_desc"] = df["trxn_nature"]



    gold_df["txn_date"] = pd.to_datetime(

        df["traddate"],

        errors="coerce",

    ).dt.date



    gold_df["post_date"] = pd.to_datetime(

        df["postdate"],

        errors="coerce",

        # dayfirst=True

    ).dt.date



    gold_df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    gold_df["units"] = pd.to_numeric(df["units"], errors="coerce")

    gold_df["nav"] = pd.to_numeric(df["purprice"], errors="coerce")



    gold_df["load_amount"] = pd.to_numeric(df["load"], errors="coerce")

    gold_df["stt"] = pd.to_numeric(df["stt"], errors="coerce")

    gold_df["stamp_duty"] = pd.to_numeric(df["stamp_duty"], errors="coerce")



    # GST = IGST + CGST + SGST

    gold_df["gst"] = (

        pd.to_numeric(df["igst_amount"], errors="coerce").fillna(0)

        + pd.to_numeric(df["cgst_amount"], errors="coerce").fillna(0)

        + pd.to_numeric(df["sgst_amount"], errors="coerce").fillna(0)

    )



    gold_df["arn"] = (

        df["brokcode"]

        .fillna("")

        .astype(str)

        .str.strip()

    )



    gold_df["euin"] = (

        df["euin"]

        .fillna("")

        .astype(str)

        .str.strip()

    )

    gold_df["sip_ref"] = (

        df["siptrxnno"]

        .fillna("")

        .astype(str)

        .str.strip()

    )



    gold_df["status"] = (

        df["trxnstat"]

        .fillna("")

        .astype(str)

        .str.strip()

    )





    # =====================================================

    # SCHEME ID LOOKUP USING SCHEME NAME

    # =====================================================



    # =====================================================

    # SCHEME ID LOOKUP

    #

    # transaction.prodcode

    #          |

    #          ↓

    # gold.scheme.scheme_code

    #          |

    #          ↓

    # gold.scheme.id

    #

    # =====================================================





    # =====================================================

    # SCHEME ID LOOKUP

    # transaction.prodcode

    #        |

    #        ↓

    # gold.scheme.scheme_code

    #        |

    #        ↓

    # gold.scheme.id

    # =====================================================



    gold_scheme = pd.read_sql(

        """

        SELECT

            id,

            rta,

            scheme_code

        FROM gold.scheme

        """,

        engine

    )





    print("Gold Scheme Rows:", len(gold_scheme))





    # Clean transaction keys



    df["source"] = (

        df["source"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )



    df["prodcode"] = (

        df["prodcode"]

        .fillna("")

        .astype(str)

        .str.strip()

        .str.upper()

    )





    # Clean scheme master keys



    gold_scheme["rta"] = (

        gold_scheme["rta"]

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





    # Check before merge



    print("\nChecking B02G mapping")



    print(

        gold_scheme[

            (gold_scheme["rta"]=="CAMS") &

            (gold_scheme["scheme_code"]=="B02G")

        ]

    )





    # Merge only scheme id



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

        how="left",

        suffixes=("","_scheme")

    )





    # Create scheme_id safely



    gold_df["scheme_id"] = df["id"]







    # =====================================================

    # VALIDATION

    # =====================================================





    print("="*80)

    print("SCHEME ID VALIDATION")

    print("="*80)



    print(

        "Total Transactions:",

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





    missing_scheme = gold_df["scheme_id"].isna()





    print(

        df.loc[

            missing_scheme,

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

    # # Validation



    # print("="*80)

    # print("SCHEME ID VALIDATION")

    # print("="*80)





    # print("Total transactions :", len(gold_df))





    # print(

    #     "Matched scheme_id :",

    #     gold_df["scheme_id"].notna().sum()

    # )





    # print(

    #     "Missing scheme_id :",

    #     gold_df["scheme_id"].isna().sum()

    # )





    # print("\nTransaction scheme samples")

    # print(

    #     df[

    #         ["scheme","scheme_name_clean"]

    #     ]

    #     .head(10)

    # )





    print("\nMissing Scheme Samples")



    missing_scheme = gold_df["scheme_id"].isna()



    print(

        df.loc[

            missing_scheme,

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

    # App-managed columns

    gold_df["client_id"] = None

    gold_df["amc_id"] = None

    # gold_df["scheme_id"] = None

    gold_df["txn_sub_type"] = None

    gold_df["rta_txn_id"] = None

    gold_df["arn_id"] = None

    gold_df["sip_id"] = None

    gold_df["source"] = df["source"]

    gold_df["source_file_id"] = None



    print("=" * 80)

    print("Gold Transaction Preview")

    print("=" * 80)

    print(gold_df.head())



    gold_df = gold_df[

        [

            "rta",

            "rta_txn_no",

            "pan",

            "folio_number",

            "txn_type",

            "txn_type_raw",

            "txn_desc",

            "txn_date",

            "post_date",

            "amount",

            "units",

            "nav",

            "load_amount",

            "stt",

            "stamp_duty",

            "gst",

            "arn",

            "euin",

            "sip_ref",

            "status",

            "client_id",

            "amc_id",

            "scheme_id",

            "txn_sub_type",

            "rta_txn_id",

            "arn_id",

            "sip_id",

            "source",

            "source_file_id"

        ]

    ]



    # Deduplicate

    gold_df["rta"] = (

        gold_df["rta"]

        .astype("string")

        .str[:10]

    )



    gold_df["pan"] = (

        gold_df["pan"]

        .astype("string")

        .str[:10]

    )



    gold_df["txn_type"] = (

        gold_df["txn_type"]

        .astype("string")

        .str[:30]

    )



    gold_df["arn"] = (

        gold_df["arn"]

        .astype("string")

        .str[:20]

    )



    gold_df["euin"] = (

        gold_df["euin"]

        .astype("string")

        .str[:20]

    )



    gold_df["status"] = (

        gold_df["status"]

        .astype("string")

        .str[:10]

    )



    gold_df["rta_txn_no"] = gold_df["rta_txn_no"].where(

        gold_df["rta_txn_no"].isna(),

        gold_df["rta_txn_no"].astype(str).str[:50]

    )



    gold_df["folio_number"] = gold_df["folio_number"].where(

        gold_df["folio_number"].isna(),

        gold_df["folio_number"].astype(str).str[:40]

    )



    gold_df["txn_type_raw"] = (

        gold_df["txn_type_raw"]

        .astype("string")

        .str[:40]

    )



    gold_df["txn_desc"] = (

        gold_df["txn_desc"]

        .astype("string")

        .str[:120]

    )



    gold_df["sip_ref"] = (

        gold_df["sip_ref"]

        .astype("string")

        .str[:50]

    )



    gold_df["txn_desc"] = gold_df["txn_desc"].where(

        gold_df["txn_desc"].isna(),

        gold_df["txn_desc"].astype(str).str[:120]

    )



    gold_df["sip_ref"] = gold_df["sip_ref"].where(

        gold_df["sip_ref"].isna(),

        gold_df["sip_ref"].astype(str).str[:50]

    )



    # Remove rows where mandatory RTA transaction number is missing

    gold_df = gold_df.dropna(subset=["rta_txn_no"])

    # gold_df.drop(columns=["scheme_code"], inplace=True)



    print(

        "Final scheme_id NULL:",

        gold_df["scheme_id"].isna().sum()

    )



    return gold_df



# =====================================================

# LOAD

# =====================================================



def load_transactions(gold_df):



    print("=" * 80)

    print("Loading into gold.transactions")

    print("=" * 80)

    print(gold_df.dtypes)

    print(gold_df.shape)

    print(gold_df.head())



    try:

        gold_df.to_sql(

            name="transactions",

            con=engine,

            schema="gold",

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )



        print(f"{len(gold_df)} rows inserted successfully.")

        return True



    except Exception as e:

        print("\nERROR while loading gold.transactions")

        traceback.print_exc()

        print("\nActual PostgreSQL error:")

        print(e)

        return False



def classify_transaction(row):



    desc = str(row.get("trxn_nature", "")).lower()

    raw = str(row.get("trxntype", "")).lower()



    text = desc + " " + raw



    if raw in ["swi", "switch in"] or "switch in" in text or "switchin" in text or "swin" in text:

        return "SWITCH_IN"



    if raw in ["swo", "switch out"] or "switch out" in text or "switchout" in text or "swout" in text:

        return "SWITCH_OUT"



    if "redemption" in text or "redeem" in text or raw == "red":

        return "REDEMPTION"



    if "sip" in text or "systematic" in text:

        return "SIP"



    if "stp" in text:

        return "STP"



    if "dividend" in text:

        return "DIVIDEND"

   

    if "transfer-in" in text or "transfer in" in text:

        return "TRANSFER_IN"



    if "transfer-out" in text or "transfer out" in text:

        return "TRANSFER_OUT"



    if (

        "purchase" in text

        or "fresh purchase" in text

        or "additional purchase" in text

        or raw == "pur"

    ):

        return "PURCHASE"

    return "OTHER"



# =====================================================

# MAIN

# =====================================================



if __name__ == "__main__":



    df = extract_transactions()

    print(df.shape)

    print(df.head())



    if df.empty:

        print("No records found in silver.transaction_master_new")

        exit()



    gold_df = transform_transactions(df)

    print(gold_df.shape)

    print(gold_df.head())

    print("\nTransaction Classification Preview")

    print("=" * 80)

    print(gold_df[["txn_desc", "txn_type_raw", "txn_type"]].head(10))



    print("\nChecking string lengths...")



    limits = {

        "rta": 10,

        "rta_txn_no": 50,

        "pan": 10,

        "folio_number": 40,

        "txn_type": 30,

        "txn_type_raw": 40,

        "txn_desc": 120,

        "arn": 20,

        "euin": 20,

        "sip_ref": 50,

        "status": 10,

    }



    for col, limit in limits.items():

        if col in gold_df.columns:

            max_len = gold_df[col].fillna("").astype(str).str.len().max()

            print(f"{col:<20} Max={max_len:<5} Limit={limit}")



    success = load_transactions(gold_df)



    if success:

        print("\nGold Transaction ETL Completed Successfully.")

    else:

        print("\nGold Transaction ETL Failed.")



