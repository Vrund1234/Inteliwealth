import pandas as pd
import hashlib 
import uuid
from sqlalchemy import create_engine, text
from utils.db import engine

# =====================================================
# DATABASE CONNECTION
# =====================================================

# engine = create_engine(
#     "postgresql+psycopg2://postgres:postgres123@localhost:5432/tr_project"
# )


# =====================================================
# EXTRACT FOLIO NOMINEES DATA
# =====================================================

def extract_folio_nominees():

    print("=" * 80)
    print("Extracting data for Gold Folio Nominees")
    print("=" * 80)

    query = """
        SELECT *
        FROM silver.investor_master
    """

    df = pd.read_sql(query, engine)

    print("\nExtraction Completed")
    print("-" * 80)
    print(f"Rows fetched    : {len(df)}")
    print(f"Columns fetched : {len(df.columns)}")

    if not df.empty:
        print("\nSample Data")
        print("-" * 80)
        print(df.head())

    return df

# =====================================================
# TRANSFORM FOLIO NOMINEES
# =====================================================

def transform_folio_nominees(df):

    print("=" * 80)
    print("Transforming Gold Folio Nominees")
    print("=" * 80)

    gold_rows = []

    nominee_configs = [
        (1, "nominee1"),
        (2, "nominee2"),
        (3, "nominee3")
    ]

    for _, row in df.iterrows():

        for seq, prefix in nominee_configs:

            nominee_name = row.get(f"{prefix}_name")

            # Convert blank nominee names to None instead of skipping the row
            if pd.isna(nominee_name) or str(nominee_name).strip() == "":
                nominee_name = None
            else:
                nominee_name = str(nominee_name).strip()

            # Convert percentage to numeric
            # Convert percentage to numeric only if nominee exists
            # Convert percentage to numeric only if nominee exists
            if nominee_name is None:
                percentage = None
            else:
                percentage = row.get(f"{prefix}_percentage")

                try:
                    percentage = float(
                        str(percentage).replace("%", "").strip()
                    )
                except (ValueError, TypeError):
                    percentage = None


            # Generate holding_id
            # Generate holding_id
            rta = str(row["source"]).strip()
            folio_no = str(row["folio_no"]).strip()

            holding_id = str(
                uuid.UUID(
                    hashlib.md5(
                        f"{rta}|{folio_no}".encode()
                    ).hexdigest()
                )
            )

            gold_rows.append({

                "holding_id": holding_id,

                "seq": seq,

                "name": nominee_name,

                "relationship": (
                    None
                    if nominee_name is None
                    else (
                        str(row.get(f"{prefix}_relation")).strip()
                        if pd.notna(row.get(f"{prefix}_relation"))
                        else None
                    )
                ),

                "percentage": percentage,

                "dob": None,

                "is_minor": None,

                "guardian_name": None,

                "id_type": None,

                "id_no": None,

                "address": None,

                # ARN mapping from investor master
                "arn": (
                    str(row.get("broker_code")).strip()
                    if pd.notna(row.get("broker_code"))
                    else None
                ),

                "sub_arn": (
                    str(row.get("subbroker")).strip()
                    if pd.notna(row.get("subbroker"))
                    else None
                )

            })
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

    print("\nTransformation Completed")
    print("-" * 80)
    print(f"Silver rows              : {len(df)}")
    print(f"Gold nominee rows        : {len(gold_df)}")

    print("\nGold Folio Nominees Preview")
    print("-" * 80)
    print(gold_df.head())
    print("\nNominee Distribution")
    print("-" * 80)
    print(gold_df.groupby("seq").size().reset_index(name="count"))

    gold_df["arn"] = (
        gold_df["arn"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", None)
    )

    gold_df["sub_arn"] = (
        gold_df["sub_arn"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", None)
    )

    return gold_df

# =====================================================
# LOAD GOLD FOLIO NOMINEES
# =====================================================

def load_folio_nominees(df):

    print("=" * 80)
    print("Loading Gold Folio Nominees")
    print("=" * 80)

    with engine.begin() as conn:

        # Clear existing data
        conn.execute(text("TRUNCATE TABLE gold.folio_nominees"))

        # Load transformed data
        df.to_sql(
            name="folio_nominees",
            schema="gold",
            con=conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=1000
        )

    print("\nLoading Completed")
    print("-" * 80)
    print(f"Rows inserted : {len(df)}")

    # Preview
    preview = pd.read_sql(
        """
        SELECT *
        FROM gold.folio_nominees
        LIMIT 10
        """,
        engine
    )

    print("\nGold Folio Nominees Preview")
    print("-" * 80)
    print(preview)

    # Row count
    count = pd.read_sql(
        """
        SELECT COUNT(*) AS total_rows
        FROM gold.folio_nominees
        """,
        engine
    )

    print("\nGold Row Count")
    print("-" * 80)
    print(count.iloc[0]["total_rows"])

# =====================================================
# MAIN
# =====================================================

def main():

    print("=" * 80)
    print("STARTING GOLD FOLIO NOMINEES ETL")
    print("=" * 80)

    # Extract
    silver_df = extract_folio_nominees()

    # Transform
    gold_df = transform_folio_nominees(silver_df)

    # Load
    load_folio_nominees(gold_df)

    print("\n" + "=" * 80)
    print("GOLD FOLIO NOMINEES ETL COMPLETED SUCCESSFULLY")
    print("=" * 80)


# =====================================================
# RUN SCRIPT
# =====================================================

if __name__ == "__main__":
    main()