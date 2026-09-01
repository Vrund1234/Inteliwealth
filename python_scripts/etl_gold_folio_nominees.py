import pandas as pd
import traceback

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
# CLEAN KEY
# =====================================================

def clean_key(series):

    return (
        series
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
        .str.replace(".0", "", regex=False)
    )


# =====================================================
# CREATE NATURAL KEY
#
# One nominee row is uniquely identified by:
#
# holding_id + seq
# =====================================================

def create_row_key(df):

    df = df.copy()

    return (
        df[
            [
                "holding_id",
                "seq"
            ]
        ]
        .fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )


# =====================================================
# EXTRACT FOLIO NOMINEES
# =====================================================

def extract_folio_nominees():

    print("=" * 80)
    print("EXTRACTING GOLD FOLIO NOMINEES")
    print("=" * 80)

    df = safe_read(
        """
        SELECT *
        FROM silver.investor_master
        """
    )

    if df.empty:

        print("No data found.")

        return pd.DataFrame()

    # =================================================
    # REQUIRED COLUMNS
    # =================================================

    required_columns = [

        "source",
        "folio_no",
        "pan_no",

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

    missing_columns = [

        col
        for col in required_columns
        if col not in df.columns

    ]

    if missing_columns:

        print(
            "ERROR: Required columns are missing "
            "from silver.investor_master:"
        )

        for col in missing_columns:

            print(" -", col)

        return pd.DataFrame()

    print(
        "Silver investor rows fetched :",
        len(df)
    )

    return df


# =====================================================
# TRANSFORM GOLD FOLIO NOMINEES
# =====================================================

def transform_folio_nominees(df):

    print("=" * 80)
    print("TRANSFORMING GOLD FOLIO NOMINEES")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    # =================================================
    # LOAD HOLDINGS
    #
    # IMPORTANT:
    #
    # We DO NOT use scheme_id.
    # We DO NOT use product_code.
    # We DO NOT join gold.scheme.
    #
    # Holding mapping is based on:
    #
    # silver.source     -> gold.rta
    # silver.folio_no   -> gold.folio_number
    # silver.pan_no     -> gold.pan
    #
    # =================================================

    holdings = safe_read(
        """
        SELECT
            id AS holding_id,
            rta,
            folio_number,
            pan
        FROM gold.holdings
        """
    )

    if holdings.empty:

        print(
            "Gold holdings data is empty."
        )

        return pd.DataFrame()

    print(
        "Gold holdings rows fetched :",
        len(holdings)
    )

    # =================================================
    # CLEAN SILVER KEYS
    # =================================================

    df = df.copy()

    df["source"] = clean_key(
        df["source"]
    )

    df["folio_no"] = clean_key(
        df["folio_no"]
    )

    df["pan_no"] = clean_key(
        df["pan_no"]
    )

    # =================================================
    # CLEAN HOLDING KEYS
    # =================================================

    holdings["rta"] = clean_key(
        holdings["rta"]
    )

    holdings["folio_number"] = clean_key(
        holdings["folio_number"]
    )

    holdings["pan"] = clean_key(
        holdings["pan"]
    )

    # =================================================
    # DEBUG COUNTS
    # =================================================

    print("=" * 80)
    print("HOLDING MAPPING")
    print("=" * 80)

    print(
        "Silver investor rows :",
        len(df)
    )

    print(
        "Gold holdings rows :",
        len(holdings)
    )

    print(
        "Silver PAN available :",
        (df["pan_no"] != "").sum()
    )

    print(
        "Silver PAN missing :",
        (df["pan_no"] == "").sum()
    )

    print(
        "Gold PAN available :",
        (holdings["pan"] != "").sum()
    )

    print(
        "Gold PAN missing :",
        (holdings["pan"] == "").sum()
    )

    # =================================================
    # DIRECT LEFT JOIN
    #
    # source     -> rta
    # folio_no   -> folio_number
    # pan_no     -> pan
    #
    # NO SCHEME MATCHING
    # NO PRODUCT CODE MATCHING
    # =================================================

    df = df.merge(

        holdings[
            [
                "holding_id",
                "rta",
                "folio_number",
                "pan"
            ]
        ],

        left_on=[
            "source",
            "folio_no",
            "pan_no"
        ],

        right_on=[
            "rta",
            "folio_number",
            "pan"
        ],

        how="left"

    )

    # =================================================
    # MAPPING VALIDATION
    # =================================================

    print("=" * 80)
    print("HOLDING MAPPING RESULT")
    print("=" * 80)

    print(
        "Total Silver investor rows :",
        len(df)
    )

    print(
        "Matched Holding IDs :",
        df["holding_id"].notna().sum()
    )

    print(
        "Missing Holding IDs :",
        df["holding_id"].isna().sum()
    )

    # =================================================
    # MATCHED SAMPLE
    # =================================================

    matched = df[
        df["holding_id"].notna()
    ]

    if not matched.empty:

        print(
            "\nMatched Holding Examples:"
        )

        print(
            matched[
                [
                    "source",
                    "folio_no",
                    "pan_no",
                    "holding_id"
                ]
            ]
            .head(20)
            .to_string(index=False)
        )

    # =================================================
    # UNMATCHED SAMPLE
    # =================================================

    unmatched = df[
        df["holding_id"].isna()
    ]

    if not unmatched.empty:

        print(
            "\nUnmatched Investor Examples:"
        )

        print(
            unmatched[
                [
                    "source",
                    "folio_no",
                    "pan_no"
                ]
            ]
            .head(20)
            .to_string(index=False)
        )

    # =================================================
    # NOMINEE CONFIGURATION
    # =================================================

    nominee_configs = [

        (
            "nominee1_name",
            "nominee1_relation",
            "nominee1_percentage"
        ),

        (
            "nominee2_name",
            "nominee2_relation",
            "nominee2_percentage"
        ),

        (
            "nominee3_name",
            "nominee3_relation",
            "nominee3_percentage"
        )

    ]

    # =================================================
    # BUILD GOLD ROWS
    # =================================================

    gold_rows = []

    for _, row in df.iterrows():

        # =================================================
        # HOLDING MUST EXIST
        #
        # The investor row itself was NOT dropped.
        # We simply cannot create a folio_nominee row
        # without a holding_id.
        # =================================================

        if pd.isna(
            row["holding_id"]
        ):

            continue

        # =================================================
        # COLLECT AVAILABLE NOMINEES
        #
        # Only nominee NAME decides whether nominee exists.
        #
        # Blank name = no nominee row.
        # =================================================

        available_nominees = []

        for (
            name_col,
            relation_col,
            percentage_col
        ) in nominee_configs:

            # ---------------------------------------------
            # NAME
            # ---------------------------------------------

            nominee_name = row.get(
                name_col
            )

            if pd.isna(
                nominee_name
            ):

                nominee_name = None

            else:

                nominee_name = str(
                    nominee_name
                ).strip()

                if nominee_name == "":

                    nominee_name = None

            # ---------------------------------------------
            # NO NAME = NO NOMINEE
            # ---------------------------------------------

            if nominee_name is None:

                continue

            # ---------------------------------------------
            # RELATIONSHIP
            # ---------------------------------------------

            relationship = row.get(
                relation_col
            )

            if pd.isna(
                relationship
            ):

                relationship = None

            else:

                relationship = str(
                    relationship
                ).strip()

                if relationship == "":

                    relationship = None

            # ---------------------------------------------
            # PERCENTAGE
            # ---------------------------------------------

            percentage = pd.to_numeric(

                row.get(
                    percentage_col
                ),

                errors="coerce"

            )

            # ---------------------------------------------
            # STORE NOMINEE
            # ---------------------------------------------

            available_nominees.append({

                "name":
                    nominee_name,

                "relationship":
                    relationship,

                "percentage":
                    percentage

            })

        # =================================================
        # ZERO NOMINEES
        #
        # Do NOT create a gold row.
        # =================================================

        if not available_nominees:

            continue

        # =================================================
        # NOMINEE DOB
        # =================================================

        nominee_dob = row.get(
            "nominee_dob"
        )

        if pd.isna(
            nominee_dob
        ):

            nominee_dob = None

        else:

            nominee_dob = pd.to_datetime(
                nominee_dob,
                errors="coerce",
                format="ISO8601"
            )

            if pd.isna(
                nominee_dob
            ):

                nominee_dob = None

        # =================================================
        # GUARDIAN NAME
        # =================================================

        source = str(
            row.get(
                "source",
                ""
            )
        ).strip().upper()

        if source == "CAMS":

            guardian_name = row.get(
                "nominee_guardian_name"
            )

        else:

            guardian_name = row.get(
                "guardian_name"
            )

        if pd.isna(
            guardian_name
        ):

            guardian_name = None

        else:

            guardian_name = str(
                guardian_name
            ).strip()

            if guardian_name == "":

                guardian_name = None

        # =================================================
        # IS MINOR
        # =================================================

        is_minor = False

        if guardian_name is not None:

            is_minor = True

        elif nominee_dob is not None:

            today = pd.Timestamp.today()

            age = (

                today.year
                - nominee_dob.year
                - (
                    (
                        today.month,
                        today.day
                    )
                    <
                    (
                        nominee_dob.month,
                        nominee_dob.day
                    )
                )

            )

            if age < 18:

                is_minor = True

        # =================================================
        # CREATE NOMINEE ROWS
        # =================================================

        for seq, nominee in enumerate(
            available_nominees,
            start=1
        ):

            gold_rows.append({

                "holding_id":
                    row["holding_id"],

                "seq":
                    seq,

                "name":
                    nominee["name"],

                "relationship":
                    nominee["relationship"],

                "percentage":
                    nominee["percentage"],

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
                    None

            })

    # =================================================
    # CREATE DATAFRAME
    # =================================================

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
            "address"

        ]

    )

    # =================================================
    # NO NOMINEES
    # =================================================

    if gold_df.empty:

        print(
            "No valid nominee records generated."
        )

        return gold_df

    # =================================================
    # REMOVE DUPLICATES
    #
    # Natural key:
    #
    # holding_id + seq
    # =================================================

    gold_df = gold_df.drop_duplicates(

        subset=[
            "holding_id",
            "seq"
        ],

        keep="last"

    )

    # =================================================
    # STRING LENGTH VALIDATION
    # =================================================

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

    gold_df["address"] = (

        gold_df["address"]
        .astype("string")
        .str[:500]

    )

    # =================================================
    # AUDIT TIMESTAMP
    # =================================================

    # UTC, not naive local time: this column is `timestamp without time zone`,
    # so a naive IST value is stored verbatim as IST while every other
    # loader stores UTC -- which made cross-table time-window queries wrong.
    gold_df["created_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)

    # =================================================
    # VALIDATION
    # =================================================

    print(
        "Rows Ready :",
        len(gold_df)
    )

    print(
        "Unique Holdings with Nominees :",
        gold_df["holding_id"].nunique()
    )

    print(
        "Sequence Distribution:"
    )

    print(
        gold_df[
            "seq"
        ]
        .value_counts()
        .sort_index()
    )

    # =================================================
    # CHECK NO SEQUENCE GAPS
    # =================================================

    sequence_errors = 0

    for holding_id, group in gold_df.groupby(
        "holding_id"
    ):

        actual = sorted(
            group["seq"].tolist()
        )

        expected = list(
            range(
                1,
                len(actual) + 1
            )
        )

        if actual != expected:

            sequence_errors += 1

            print(
                "SEQUENCE ERROR:",
                holding_id,
                "actual=",
                actual,
                "expected=",
                expected
            )

    print(
        "Holdings with sequence errors :",
        sequence_errors
    )

    if sequence_errors > 0:

        print(
            "ERROR: Sequence validation failed."
        )

        return pd.DataFrame()

    return gold_df


# =====================================================
# LOAD GOLD FOLIO NOMINEES
# =====================================================

def load_folio_nominees(gold_df):

    print("=" * 80)
    print("LOADING GOLD FOLIO NOMINEES")
    print("=" * 80)

    if gold_df.empty:

        print(
            "No new nominee records found."
        )

        return True

    # =================================================
    # LOAD EXISTING DATA
    # =================================================

    try:

        existing = pd.read_sql(

            """
            SELECT
                holding_id,
                seq
            FROM gold.folio_nominees
            """,

            engine

        )

    except Exception as e:

        print(
            "Could not read existing "
            "folio nominees:",
            e
        )

        existing = pd.DataFrame()

    # =================================================
    # REMOVE ALREADY EXISTING KEYS
    #
    # Natural key:
    #
    # holding_id + seq
    # =================================================

    if not existing.empty:

        old_keys = set(
            create_row_key(
                existing
            )
        )

        new_keys = create_row_key(
            gold_df
        )

        gold_df = gold_df.loc[
            ~new_keys.isin(
                old_keys
            )
        ]

    if gold_df.empty:

        print(
            "All nominee records already exist."
        )

        return True

    # =================================================
    # FINAL DUPLICATE CHECK
    # =================================================

    duplicate_count = (

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
        "Duplicate holding_id + seq :",
        duplicate_count
    )

    if duplicate_count > 0:

        print(
            "ERROR: Duplicate nominee keys "
            "found before insert."
        )

        return False

    # =================================================
    # INSERT
    # =================================================

    try:

        from utils.db import upsert_dataframe

        upsert_dataframe(
            gold_df,
            schema="gold",
            table="folio_nominees",
            conflict_columns=["holding_id", "seq"],
            chunksize=500,
            # gold.folio_nominees has no updated_at (or equivalent) column.
            updated_at_column=None,
        )

        print(
            f"{len(gold_df)} rows inserted "
            "into Gold Folio Nominees."
        )

        return True

    except Exception:

        print(
            "FAILED LOADING GOLD FOLIO NOMINEES"
        )

        traceback.print_exc(
            limit=5
        )

        return False


# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD FOLIO NOMINEES ETL")
    print("=" * 80)

    # =================================================
    # EXTRACT
    # =================================================

    silver_df = extract_folio_nominees()

    if silver_df.empty:

        print(
            "No nominee data found."
        )

        return
        
    # TRANSFORM
    # =================================================

    gold_df = transform_folio_nominees(
        silver_df
    )

    if gold_df.empty:

        print(
            "No valid nominee records generated."
        )

        return

    # =================================================
    # FINAL VALIDATION
    # =================================================

    print("=" * 80)
    print(
        "FINAL GOLD FOLIO NOMINEES VALIDATION"
    )
    print("=" * 80)

    print(
        "\nColumns:"
    )

    print(
        gold_df.columns.tolist()
    )

    print(
        "\nData Types:"
    )

    print(
        gold_df.dtypes
    )

    print(
        "\nNull Count:"
    )

    print(
        gold_df.isnull().sum()
    )

    print(
        "\nDuplicate Check:"
    )

    duplicate_count = (

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
        "Duplicate holding_id + seq :",
        duplicate_count
    )

    print(
        "\nSequence Counts:"
    )

    print(
        gold_df[
            "seq"
        ]
        .value_counts()
        .sort_index()
    )

    print(
        "\nSample Data:"
    )

    print(
        gold_df.head(20)
    )

    # =================================================
    # LOAD
    # =================================================

    status = load_folio_nominees(
        gold_df
    )

    if status:

        print("\n")

        print("=" * 80)

        print(
            "GOLD FOLIO NOMINEES ETL "
            "COMPLETED SUCCESSFULLY"
        )

        print("=" * 80)

        # =================================================
        # FINAL DATABASE CHECK
        # =================================================

        final_count = safe_read(

            """
            SELECT
                COUNT(*) AS total_rows
            FROM gold.folio_nominees
            """

        )

        print(
            "\nGold Folio Nominees Row Count"
        )

        print(
            final_count
        )

    else:

        print("\n")

        print("=" * 80)

        print(
            "GOLD FOLIO NOMINEES ETL FAILED"
        )

        print("=" * 80)


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    main()