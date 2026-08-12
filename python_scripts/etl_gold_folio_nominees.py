import pandas as pd
import traceback

from utils.db import engine


# ============================================================
# SAFE READ
# ============================================================

def safe_read(query):

    try:

        return pd.read_sql(
            query,
            engine
        )

    except Exception as e:

        print("SQL ERROR :", e)

        traceback.print_exc(limit=3)

        return pd.DataFrame()


# ============================================================
# CLEAN FOLIO
# ============================================================

def clean_folio(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(
            r"\.0$",
            "",
            regex=True
        )
        .str.upper()
    )


# ============================================================
# CLEAN STRING
# ============================================================

def clean_string(value):

    if pd.isna(value):

        return None

    value = str(value).strip()

    if value == "":
        return None

    if value.upper() in [
        "NAN",
        "NONE",
        "NULL",
        "NAT"
    ]:

        return None

    return value


# ============================================================
# EXTRACT
# ============================================================

def extract_folio_nominees():

    print("=" * 80)
    print("EXTRACTING DATA FOR GOLD FOLIO NOMINEES")
    print("=" * 80)

    df = safe_read(
        """
        SELECT *
        FROM silver.investor_master
        WHERE flag = 0
        """
    )

    if df.empty:

        print(
            "No unprocessed investor records found."
        )

        return df

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    print(
        "Rows fetched from Silver :",
        len(df)
    )

    # --------------------------------------------------------
    # CHECK ACTUAL NOMINEE COLUMNS
    # --------------------------------------------------------

    nominee_columns = [

        "nominee1_name",
        "nominee1_relation",
        "nominee1_percentage",

        "nominee2_name",
        "nominee2_relation",
        "nominee2_percentage",

        "nominee3_name",
        "nominee3_relation",
        "nominee3_percentage",

        "nominee_dob",
        "nominee_guardian_name",
        "guardian_name"

    ]

    print("\nNominee column availability")
    print("-" * 80)

    for col in nominee_columns:

        print(
            f"{col:30}",
            "YES" if col in df.columns else "NO"
        )

    return df


# ============================================================
# TRANSFORM
# ============================================================

def transform_folio_nominees(df):

    print("=" * 80)
    print("TRANSFORMING GOLD FOLIO NOMINEES")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    df = df.copy()

    df.columns = (
        df.columns
        .astype(str)
        .str.lower()
        .str.strip()
    )

    # ========================================================
    # VALIDATE ONLY REQUIRED COLUMNS
    #
    # scheme_code IS NOT REQUIRED
    # ========================================================

    required_columns = [
        "folio_no",
        "source"
    ]

    missing = [
        col
        for col in required_columns
        if col not in df.columns
    ]

    if missing:

        raise Exception(
            "Required Silver columns missing: "
            + ", ".join(missing)
        )

    # ========================================================
    # LOAD GOLD HOLDINGS
    # ========================================================

    holdings = safe_read(
        """
        SELECT
            id,
            rta,
            folio_number
        FROM gold.holdings
        """
    )

    if holdings.empty:

        print(
            "Gold Holdings table is empty."
        )

        return pd.DataFrame()

    # ========================================================
    # LOAD TRANSACTIONS
    #
    # ARN:
    #     brokcode
    #
    # SUB ARN:
    #     src_brk_code
    #
    # Match:
    #     source + folio_no
    # ========================================================

    transactions = safe_read(
        """
        SELECT
            folio_no,
            source,
            brokcode,
            src_brk_code,
            created_at
        FROM silver.transaction_master_new
        """
    )

    if transactions.empty:

        print(
            "Silver Transaction Master is empty."
        )

        return pd.DataFrame()

    # ========================================================
    # CLEAN INVESTOR KEYS
    # ========================================================

    df["source"] = (
        df["source"]
        .apply(clean_string)
        .fillna("")
        .astype(str)
        .str.upper()
        .str.strip()
    )

    df["folio_no"] = clean_folio(
        df["folio_no"]
    )

    # ========================================================
    # CLEAN HOLDING KEYS
    # ========================================================

    holdings["rta"] = (
        holdings["rta"]
        .apply(clean_string)
        .fillna("")
        .astype(str)
        .str.upper()
        .str.strip()
    )

    holdings["folio_number"] = clean_folio(
        holdings["folio_number"]
    )

    # ========================================================
    # REMOVE DUPLICATE HOLDING KEYS
    # ========================================================

    holdings = (
        holdings
        .drop_duplicates(
            subset=[
                "rta",
                "folio_number"
            ],
            keep="last"
        )
    )

    # ========================================================
    # CLEAN TRANSACTIONS
    # ========================================================

    transactions["source"] = (
        transactions["source"]
        .apply(clean_string)
        .fillna("")
        .astype(str)
        .str.upper()
        .str.strip()
    )

    transactions["folio_no"] = clean_folio(
        transactions["folio_no"]
    )

    transactions["brokcode"] = (
        transactions["brokcode"]
        .apply(clean_string)
    )

    transactions["src_brk_code"] = (
        transactions["src_brk_code"]
        .apply(clean_string)
    )

    transactions["created_at"] = pd.to_datetime(
        transactions["created_at"],
        errors="coerce"
    )

    # ========================================================
    # IMPORTANT
    #
    # There can be many transactions for one folio.
    #
    # Sort newest first and take the latest record
    # containing the broker information.
    # ========================================================

    transactions = (
        transactions
        .sort_values(
            [
                "source",
                "folio_no",
                "created_at"
            ],
            ascending=[
                True,
                True,
                False
            ],
            na_position="last"
        )
    )

    # Prefer rows where broker information exists

    transactions["broker_available"] = (
        transactions["brokcode"].notna()
        |
        transactions["src_brk_code"].notna()
    )

    transactions = (
        transactions
        .sort_values(
            [
                "source",
                "folio_no",
                "broker_available",
                "created_at"
            ],
            ascending=[
                True,
                True,
                False,
                False
            ]
        )
        .drop_duplicates(
            subset=[
                "source",
                "folio_no"
            ],
            keep="first"
        )
    )

    transactions = transactions.drop(
        columns=[
            "broker_available"
        ]
    )

    # ========================================================
    # MAP HOLDING ID
    # ========================================================

    df = df.merge(

        holdings,

        left_on=[
            "source",
            "folio_no"
        ],

        right_on=[
            "rta",
            "folio_number"
        ],

        how="left"
    )

    df.rename(
        columns={
            "id": "holding_id"
        },
        inplace=True
    )

    print(
        "Matched Holding IDs :",
        df["holding_id"].notna().sum()
    )

    print(
        "Missing Holding IDs :",
        df["holding_id"].isna().sum()
    )

    # ========================================================
    # MAP ARN / SUB ARN
    # ========================================================

    df = df.merge(

        transactions[
            [
                "source",
                "folio_no",
                "brokcode",
                "src_brk_code"
            ]
        ],

        on=[
            "source",
            "folio_no"
        ],

        how="left"
    )

    print(
        "Matched ARN records :",
        df["brokcode"].notna().sum()
    )

    print(
        "Missing ARN records :",
        df["brokcode"].isna().sum()
    )

    print(
        "Matched Sub ARN records :",
        df["src_brk_code"].notna().sum()
    )

    print(
        "Missing Sub ARN records :",
        df["src_brk_code"].isna().sum()
    )

    # ========================================================
    # BUILD NOMINEE ROWS
    # ========================================================

    gold_rows = []

    nominee_configs = [

        (1, "nominee1"),
        (2, "nominee2"),
        (3, "nominee3")

    ]

    for _, row in df.iterrows():

        for seq, prefix in nominee_configs:

            # ------------------------------------------------
            # NAME
            # ------------------------------------------------

            nominee_name = clean_string(
                row.get(
                    f"{prefix}_name"
                )
            )

            # ------------------------------------------------
            # RELATIONSHIP
            # ------------------------------------------------

            relationship = clean_string(
                row.get(
                    f"{prefix}_relation"
                )
            )

            # ------------------------------------------------
            # PERCENTAGE
            # ------------------------------------------------

            percentage = pd.to_numeric(
                row.get(
                    f"{prefix}_percentage"
                ),
                errors="coerce"
            )

            # ------------------------------------------------
            # DOB
            # ------------------------------------------------

            nominee_dob = row.get(
                "nominee_dob"
            )

            if nominee_dob is not None:

                nominee_dob = pd.to_datetime(
                    nominee_dob,
                    errors="coerce"
                )

            if pd.isna(nominee_dob):

                nominee_dob = None

            # ------------------------------------------------
            # GUARDIAN
            # ------------------------------------------------

            source = str(
                row.get(
                    "source",
                    ""
                )
            ).upper().strip()

            if source == "CAMS":

                guardian_name = clean_string(
                    row.get(
                        "nominee_guardian_name"
                    )
                )

            else:

                guardian_name = clean_string(
                    row.get(
                        "guardian_name"
                    )
                )

            # ------------------------------------------------
            # IS MINOR
            # ------------------------------------------------

            is_minor = "N"

            if guardian_name is not None:

                is_minor = "Y"

            elif nominee_dob is not None:

                today = pd.Timestamp.today()

                age = (
                    today.year
                    - nominee_dob.year
                    - (
                        (today.month, today.day)
                        <
                        (
                            nominee_dob.month,
                            nominee_dob.day
                        )
                    )
                )

                if age < 18:

                    is_minor = "Y"

            # ------------------------------------------------
            # ARN
            # ------------------------------------------------

            arn = clean_string(
                row.get(
                    "brokcode"
                )
            )

            # ------------------------------------------------
            # SUB ARN
            # ------------------------------------------------

            sub_arn = clean_string(
                row.get(
                    "src_brk_code"
                )
            )

            # ------------------------------------------------
            # ONLY CREATE NOMINEE ROW IF THERE IS NOMINEE DATA
            # ------------------------------------------------

            if (
                nominee_name is None
                and relationship is None
                and pd.isna(percentage)
                and guardian_name is None
                and nominee_dob is None
            ):

                continue

            gold_rows.append({

                "holding_id":
                    row["holding_id"],

                "seq":
                    seq,

                "name":
                    nominee_name,

                "relationship":
                    relationship,

                "percentage":
                    percentage,

                "dob":
                    nominee_dob,

                "is_minor":
                    is_minor,

                "guardian_name":
                    guardian_name,

                "id_type":
                    None,

                "id_no":
                    None,

                "address":
                    None,

                "arn":
                    arn,

                "sub_arn":
                    sub_arn

            })

    # ========================================================
    # CREATE DATAFRAME
    # ========================================================

    gold_df = pd.DataFrame(
        gold_rows,
        columns=[
            "holding_id",
            "seq",
            "name",
            "relationship",
            "percentage",
            "dob",
            "is_minor",
            "guardian_name",
            "id_type",
            "id_no",
            "address",
            "arn",
            "sub_arn"
        ]
    )

    if gold_df.empty:

        print(
            "No nominee records generated."
        )

        return gold_df

    # ========================================================
    # REMOVE INVALID HOLDING IDs
    # ========================================================

    gold_df = gold_df[
        gold_df["holding_id"].notna()
    ]

    # ========================================================
    # CLEAN STRING LENGTHS
    # ========================================================

    gold_df["name"] = (
        gold_df["name"]
        .astype("string")
        .str[:255]
    )

    gold_df["relationship"] = (
        gold_df["relationship"]
        .astype("string")
        .str[:60]
    )

    gold_df["guardian_name"] = (
        gold_df["guardian_name"]
        .astype("string")
        .str[:255]
    )

    gold_df["id_type"] = (
        gold_df["id_type"]
        .astype("string")
        .str[:20]
    )

    gold_df["id_no"] = (
        gold_df["id_no"]
        .astype("string")
        .str[:50]
    )

    # Address intentionally NULL for now

    gold_df["address"] = None

    gold_df["arn"] = (
        gold_df["arn"]
        .astype("string")
        .str.strip()
        .str[:50]
    )

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .astype("string")
        .str.strip()
        .str[:50]
    )

    # ========================================================
    # REMOVE CURRENT-BATCH DUPLICATES
    # ========================================================

    gold_df = gold_df.drop_duplicates(
        subset=[
            "holding_id",
            "seq"
        ],
        keep="last"
    )

    # ========================================================
    # CREATED AT
    # ========================================================

    gold_df["created_at"] = pd.Timestamp.now()

    print(
        "\nRows Ready :",
        len(gold_df)
    )

    print(
        "Unique Holdings :",
        gold_df["holding_id"].nunique()
    )

    return gold_df


# ============================================================
# LOAD
# ============================================================

def load_folio_nominees(gold_df):

    print("=" * 80)
    print("LOADING GOLD FOLIO NOMINEES")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No nominee records to insert."
        )

        return True

    existing = safe_read(
        """
        SELECT
            holding_id,
            seq
        FROM gold.folio_nominees
        """
    )

    # ========================================================
    # REMOVE EXISTING KEYS
    # ========================================================

    if not existing.empty:

        existing["key"] = (
            existing["holding_id"]
            .astype(str)
            + "|"
            + existing["seq"]
            .astype(str)
        )

        gold_df["key"] = (
            gold_df["holding_id"]
            .astype(str)
            + "|"
            + gold_df["seq"]
            .astype(str)
        )

        gold_df = gold_df[
            ~gold_df["key"].isin(
                set(existing["key"])
            )
        ].copy()

        gold_df.drop(
            columns=["key"],
            inplace=True
        )

    print(
        "Rows after duplicate check :",
        len(gold_df)
    )

    if gold_df.empty:

        print(
            "No new nominee records to insert."
        )

        return True

    try:

        gold_df.to_sql(
            "folio_nominees",
            engine,
            schema="gold",
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000
        )

        print(
            f"{len(gold_df)} rows inserted."
        )

        return True

    except Exception as e:

        print(
            "FAILED LOADING GOLD FOLIO NOMINEES"
        )

        print(e)

        traceback.print_exc(
            limit=5
        )

        return False


# ============================================================
# UPDATE SILVER FLAG
# ============================================================

def update_investor_master_flag(source_df):

    print("=" * 80)
    print("UPDATING SILVER INVESTOR MASTER FLAGS")
    print("=" * 80)

    if source_df.empty:

        return True

    try:

        # Use one UPDATE instead of executing one SQL
        # statement per row.

        source_keys = source_df[
            [
                "folio_no",
                "source",
                "created_at"
            ]
        ].copy()

        source_keys["folio_no"] = clean_folio(
            source_keys["folio_no"]
        )

        source_keys["source"] = (
            source_keys["source"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        # ----------------------------------------------------
        # Update using source + folio + created_at
        # ----------------------------------------------------

        with engine.begin() as conn:

            for _, row in source_keys.iterrows():

                conn.exec_driver_sql(
                    """
                    UPDATE silver.investor_master
                    SET flag = 1
                    WHERE flag = 0
                      AND REGEXP_REPLACE(
                          TRIM(CAST(folio_no AS TEXT)),
                          '\\.0$',
                          ''
                      ) = %s
                      AND UPPER(TRIM(source)) = %s
                      AND created_at = %s
                    """,
                    (
                        row["folio_no"],
                        row["source"],
                        row["created_at"]
                    )
                )

        print(
            "Silver investor_master rows marked flag = 1."
        )

        return True

    except Exception as e:

        print(
            "FAILED UPDATING SILVER INVESTOR MASTER FLAGS"
        )

        print(e)

        traceback.print_exc(
            limit=5
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 80)
    print("STARTING GOLD FOLIO NOMINEES ETL")
    print("=" * 80)

    try:

        # ====================================================
        # EXTRACT
        # ====================================================

        silver_df = extract_folio_nominees()

        if silver_df.empty:

            print(
                "No nominee source data found."
            )

            return

        source_batch = silver_df.copy()

        # ====================================================
        # TRANSFORM
        # ====================================================

        gold_df = transform_folio_nominees(
            silver_df
        )

        # ====================================================
        # IMPORTANT
        #
        # If there are no nominees, we still consider the
        # Silver records evaluated. However, don't mark them
        # processed here unless that is the desired business
        # behavior.
        # ====================================================

        if gold_df.empty:

            print(
                "No valid nominee rows generated."
            )

            print(
                "Silver flags were NOT changed."
            )

            return

        # ====================================================
        # VALIDATION
        # ====================================================

        print("=" * 80)
        print("FINAL GOLD FOLIO NOMINEES VALIDATION")
        print("=" * 80)

        print(
            "Rows :",
            len(gold_df)
        )

        print(
            "Duplicate holding_id + seq :",
            gold_df
            .duplicated(
                subset=[
                    "holding_id",
                    "seq"
                ]
            )
            .sum()
        )

        print(
            "\nSample:"
        )

        print(
            gold_df.head()
        )

        # ====================================================
        # LOAD
        # ====================================================

        status = load_folio_nominees(
            gold_df
        )

        if not status:

            print(
                "Gold Folio Nominees load failed."
            )

            return

        # ====================================================
        # FLAG UPDATE
        # ====================================================

        flag_status = update_investor_master_flag(
            source_batch
        )

        if not flag_status:

            print(
                "Gold loaded but Silver flag update failed."
            )

            return

        # ====================================================
        # SUCCESS
        # ====================================================

        print("=" * 80)
        print(
            "GOLD FOLIO NOMINEES ETL COMPLETED SUCCESSFULLY"
        )
        print("=" * 80)

        final_count = safe_read(
            """
            SELECT COUNT(*) AS total_rows
            FROM gold.folio_nominees
            """
        )

        print(
            "\nGold Folio Nominees Row Count:"
        )

        print(
            final_count
        )

    except Exception as e:

        print("=" * 80)
        print("GOLD FOLIO NOMINEES ETL ERROR")
        print("=" * 80)

        print(
            type(e).__name__
        )

        print(e)

        traceback.print_exc()


if __name__ == "__main__":

    main()