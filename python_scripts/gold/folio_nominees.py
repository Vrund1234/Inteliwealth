import pandas as pd
import hashlib
import uuid
import traceback

from utils.db import engine
from common.etl_helpers import safe_read, get_last_processed_time, normalize_for_compare


# =====================================================
# NOMINEE FLATTENING SHAPE
# =====================================================
# One Gold row per (silver row, nominee slot).  Kept module-level so the
# reshape below and the DataFrame it produces cannot drift apart.

NOMINEE_CONFIGS = [

    (1, "nominee1"),
    (2, "nominee2"),
    (3, "nominee3")

]

GOLD_NOMINEE_COLUMNS = [

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

# Columns the old loop hard-coded to None on every emitted row.
EMPTY_NOMINEE_COLUMNS = [

    "dob",
    "is_minor",
    "guardian_name",
    "id_type",
    "id_no",
    "address"

]


# =====================================================
# CREATE NATURAL KEY  (local — keys on holding_id + seq)
# normalize_for_compare imported from common.etl_helpers
# =====================================================

def create_row_key(df):

    df = normalize_for_compare(df)

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
    print("Extracting Gold Folio Nominees")
    print("=" * 80)

    last_time = get_last_processed_time("gold.folio_nominees")

    df = safe_read(
        """
        SELECT *
        FROM silver.investor_master
        """
    )

    if df.empty:

        print("No data found.")

        return df

    df["created_at"] = pd.to_datetime(
        df["created_at"],
        errors="coerce"
    )

    last_time = pd.Timestamp(last_time)

    if getattr(df["created_at"].dt, "tz", None) is not None:

        df["created_at"] = (
            df["created_at"]
            .dt.tz_localize(None)
        )

    if last_time.tzinfo is not None:

        last_time = last_time.tz_localize(None)

    df = df[
        df["created_at"] > last_time
    ]

    print("Rows fetched :", len(df))

    return df

# =====================================================
# FLATTEN NOMINEE COLUMN GROUPS  (vectorised — Phase C)
# =====================================================

def _optional_column(df, column):
    """Return ``df[column]``, or an all-null object column when it is absent.

    Mirrors ``row.get(column)`` in the old row-wise loop, which yielded None
    for a column the Silver frame never carried.
    """
    if column in df.columns:

        return df[column]

    return pd.Series(
        None,
        index=df.index,
        dtype=object
    )


def _reinfer_column(values):
    """Re-run pandas' own dtype inference over an object column.

    The old loop handed pandas one list of dicts, so each column's dtype was
    inferred from all of its Python values in a single pass.  Building the
    three nominee slices separately would instead freeze each slice's dtype
    before the concat, so the text columns are re-inferred afterwards to land
    on exactly the dtype the loop produced — ``str`` for a column holding any
    string, plain ``object`` for one that is entirely null.
    """
    return pd.Series(
        values.to_numpy(dtype=object),
        index=values.index
    )


def _as_object_with_none(values):
    """Object-dtype copy of *values* whose nulls are literal ``None``.

    The old loop built plain Python dicts, so every null it emitted was
    ``None`` in an object column.  ``astype("string")`` is what makes the
    strip vectorised, but it yields ``pd.NA`` in a ``string`` column — this
    converts back so the reshape is dtype-identical to the loop it replaces.
    """
    values = values.astype(object)

    return values.where(
        values.notna(),
        None
    )


def _clean_nominee_name(values):
    """``str(v).strip()``, with blank-after-strip collapsed to null."""

    names = (
        values
        .astype("string")
        .str.strip()
    )

    names = names.mask(
        (names == "").fillna(False),
        pd.NA
    )

    return _as_object_with_none(names)


def _clean_nominee_relationship(values):
    """``str(v).strip()`` only.

    Deliberately does NOT collapse "" to null — the old loop applied that
    rule to ``name`` but not to ``relationship``, and that asymmetry is
    preserved here on purpose.
    """
    return _as_object_with_none(
        values
        .astype("string")
        .str.strip()
    )


def flatten_nominee_rows(df):
    """Explode the nominee1/2/3 column groups into one row per (row, seq).

    Vectorised replacement (Phase C) for the ``df.iterrows()`` double loop
    that appended one dict per (row, nominee slot).  Each nominee slot is
    built as a whole-column slice and the three slices are concatenated, so
    the work is O(rows) pandas operations instead of O(rows x 3) Python
    dict builds.

    The interleaved row order of the old loop (row0/seq1, row0/seq2,
    row0/seq3, row1/seq1, ...) is reproduced exactly by giving each slice
    the index ``position * 3 + (seq - 1)`` and sorting, so the positional
    index — which ``drop_duplicates(keep="last")`` downstream depends on —
    is identical to before.
    """
    positions = pd.RangeIndex(len(df))

    blocks = []

    for seq, prefix in NOMINEE_CONFIGS:

        block = pd.DataFrame(
            {
                "holding_id": df["holding_id"],

                "seq": seq,

                "name": _clean_nominee_name(
                    _optional_column(df, f"{prefix}_name")
                ),

                "relationship": _clean_nominee_relationship(
                    _optional_column(df, f"{prefix}_relation")
                ),

                "percentage": pd.to_numeric(
                    _optional_column(df, f"{prefix}_percentage"),
                    errors="coerce"
                ),
            }
        )

        for column in EMPTY_NOMINEE_COLUMNS:

            block[column] = None

        block.index = (
            positions * len(NOMINEE_CONFIGS) + (seq - 1)
        )

        blocks.append(block)

    gold_df = (
        pd.concat(blocks)
        .sort_index()
        .reindex(columns=GOLD_NOMINEE_COLUMNS)
    )

    for column in ("name", "relationship"):

        gold_df[column] = _reinfer_column(gold_df[column])

    return gold_df


# =====================================================
# TRANSFORM GOLD FOLIO NOMINEES
# =====================================================

def transform_folio_nominees(df):

    print("=" * 80)
    print("Transforming Gold Folio Nominees")
    print("=" * 80)

    if df.empty:

        return pd.DataFrame()

    # =====================================================
    # LOAD GOLD HOLDINGS
    # =====================================================

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

        print("Gold Holdings table is empty.")

        return pd.DataFrame()

    # =====================================================
    # CLEAN JOIN KEYS
    # =====================================================

    df["source"] = (
        df["source"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["folio_no"] = (
        df["folio_no"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    holdings["rta"] = (
        holdings["rta"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    holdings["folio_number"] = (
        holdings["folio_number"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(".0", "", regex=False)
    )

    # =====================================================
    # MAP HOLDING ID
    # =====================================================

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

    print("Matched Holding IDs :", df["holding_id"].notna().sum())
    print("Missing Holding IDs :", df["holding_id"].isna().sum())

    # =====================================================
    # BUILD NOMINEE ROWS  (vectorised reshape — see flatten_nominee_rows)
    # =====================================================

    gold_df = flatten_nominee_rows(df)

    # =====================================================
    # REMOVE INVALID ROWS
    # =====================================================

    gold_df = gold_df[

        gold_df["holding_id"].notna()

    ]

    # =====================================================
    # REMOVE DUPLICATES IN CURRENT BATCH
    # =====================================================

    gold_df = gold_df.drop_duplicates(

        subset=[

            "holding_id",

            "seq"

        ],

        keep="last"

    )

    # =====================================================
    # LENGTH VALIDATION
    # =====================================================

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

    # =====================================================
    # AUDIT TIMESTAMP
    # =====================================================

    gold_df["created_at"] = pd.Timestamp.now()

    print("Rows Ready :", len(gold_df))

    return gold_df

# =====================================================
# LOAD GOLD FOLIO NOMINEES
# =====================================================

def load_folio_nominees(gold_df):

    print("=" * 80)
    print("Loading Gold Folio Nominees")
    print("=" * 80)

    if gold_df.empty:

        print("No new records found.")

        return True

    # =====================================================
    # LOAD EXISTING GOLD DATA
    # =====================================================

    try:

        existing = pd.read_sql(

            """
            SELECT

                holding_id,
                seq,
                name,
                relationship,
                percentage,
                dob,
                is_minor,
                guardian_name,
                id_type,
                id_no,
                address,
                created_at

            FROM gold.folio_nominees
            """,

            engine

        )

    except Exception:

        existing = pd.DataFrame()

    # =====================================================
    # REMOVE EXISTING DUPLICATES
    # =====================================================

    if not existing.empty:

        old_keys = set(
            create_row_key(existing)
        )

        new_keys = create_row_key(gold_df)

        gold_df = gold_df.loc[
            ~new_keys.isin(old_keys)
        ]

    if gold_df.empty:

        print("Duplicate nominee records skipped.")

        return True

    # =====================================================
    # INSERT INTO GOLD
    # =====================================================

    try:

        gold_df.to_sql(

            name="folio_nominees",

            schema="gold",

            con=engine,

            if_exists="append",

            index=False,

            method="multi",

            chunksize=5000

        )

        print(f"{len(gold_df)} rows inserted into Gold Folio Nominees.")

        return True

    except Exception:

        print("FAILED LOADING GOLD FOLIO NOMINEES")

        traceback.print_exc(limit=5)

        return False

# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD FOLIO NOMINEES ETL")
    print("=" * 80)


    # =====================================================
    # EXTRACT
    # =====================================================

    silver_df = extract_folio_nominees()


    if silver_df.empty:

        print("No nominee data found.")

        return


    # =====================================================
    # TRANSFORM
    # =====================================================

    gold_df = transform_folio_nominees(
        silver_df
    )


    if gold_df.empty:

        print("No valid nominee records generated.")

        return


    # =====================================================
    # VALIDATION BEFORE LOAD
    # =====================================================

    print("=" * 80)
    print("FINAL GOLD FOLIO NOMINEES VALIDATION")
    print("=" * 80)


    print("\nColumns:")
    print(gold_df.columns.tolist())


    print("\nData Types:")
    print(gold_df.dtypes)


    print("\nNull Count:")
    print(
        gold_df.isnull().sum()
    )


    print("\nDuplicate Check:")
    
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


    print("\nSample Data:")
    print(
        gold_df.head()
    )


    # =====================================================
    # LOAD
    # =====================================================

    status = load_folio_nominees(
        gold_df
    )


    if status:

        print("\n")
        print("=" * 80)
        print("GOLD FOLIO NOMINEES ETL COMPLETED SUCCESSFULLY")
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


        print("\nGold Folio Nominees Row Count")

        print(
            final_count
        )


    else:

        print("\n")
        print("=" * 80)
        print("GOLD FOLIO NOMINEES ETL FAILED")
        print("=" * 80)



# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    main()