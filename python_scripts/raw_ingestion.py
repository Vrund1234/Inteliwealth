import csv
import io
import pandas as pd
from utils.db import engine
from etl_investor_master import process_investor_master
from etl_trans import process_transactions
from etl_sip import process_sip

# =====================================================
# READ FILE
# =====================================================

def read_file(file):

    name = file.name.lower()

    # =================================================
    # CSV / TXT
    # =================================================
    if name.endswith((".csv", ".txt")):

        file.seek(0)

        # try:
        #     text = file.read().decode("utf-8")
        # except UnicodeDecodeError:
        #     file.seek(0)
        #     text = file.read().decode("latin1")


        raw = file.read()

        for encoding in ("utf-8", "utf-16", "utf-16le", "utf-16be", "latin1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("Unable to decode uploaded file.")

        # FIX WINDOWS NEWLINE ISSUE
        # text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Remove NULL characters
        text = text.replace("\x00", "")

        # Normalize newlines
        text = text.replace("\r\n", "\n").replace("\r", "\n")


        # =================================================
        # PARSE CSV SAFELY
        # =================================================

        # Detect delimiter from the first non-empty line
        lines = [line for line in text.split("\n") if line.strip()]

        if not lines:
            return pd.DataFrame()

        first_line = lines[0]

        # CAMS CSV files are comma separated
        if "," in first_line:
            delimiter = ","
        elif "\t" in first_line:
            delimiter = "\t"
        elif ";" in first_line:
            delimiter = ";"
        else:
            delimiter = ","

        print(f"Detected delimiter: {repr(delimiter)}")

        # IMPORTANT:
        # Let csv.reader handle quoted values.
        # Do NOT manually remove quotes after parsing.
        reader = csv.reader(
            io.StringIO(text),
            delimiter=delimiter,
            quotechar='"',
            doublequote=True,
            skipinitialspace=False
        )

        rows = list(reader)

        if not rows:
            return pd.DataFrame()

        # =================================================
        # HEADER
        # =================================================

        header = [
            h.strip().strip("'").strip('"')
            for h in rows[0]
        ]

        expected_columns = len(header)

        print(f"Expected columns: {expected_columns}")

        # =================================================
        # DATA ROWS
        # =================================================

        clean_rows = []
        bad_rows = 0

        for row_number, row in enumerate(rows[1:], start=2):

            # Ignore completely empty rows
            if not any(str(x).strip() for x in row):
                continue

            # -------------------------------------------------
            # Correct number of columns
            # -------------------------------------------------

            if len(row) == expected_columns:

                clean_rows.append([
                    str(x).strip()
                    for x in row
                ])

            # -------------------------------------------------
            # Missing columns
            # -------------------------------------------------

            elif len(row) < expected_columns:

                print(
                    f"WARNING: Row {row_number} has fewer columns | "
                    f"Expected={expected_columns}, Found={len(row)}"
                )

                row = row + [""] * (expected_columns - len(row))

                clean_rows.append([
                    str(x).strip()
                    for x in row
                ])

                bad_rows += 1

            # -------------------------------------------------
            # Extra columns
            # -------------------------------------------------

            else:

                print(
                    f"WARNING: Row {row_number} has EXTRA columns | "
                    f"Expected={expected_columns}, Found={len(row)}"
                )

                print("Raw parsed row:")
                print(row)

                # DO NOT blindly truncate here.
                bad_rows += 1

                # Keep the row only if we can safely identify
                # that the extra fields are empty.
                extra_values = row[expected_columns:]

                if all(str(x).strip() == "" for x in extra_values):

                    clean_rows.append([
                        str(x).strip()
                        for x in row[:expected_columns]
                    ])

                else:

                    print(
                        f"SKIPPING malformed row {row_number} "
                        f"because it contains non-empty extra values."
                    )

        # =================================================
        # CREATE DATAFRAME
        # =================================================

        df = pd.DataFrame(
            clean_rows,
            columns=header
        )

        if file.name.lower().endswith("r9.csv"):

            print("\n" + "=" * 100)
            print("CAMS R9 GST / FOLIO DEBUG")
            print("=" * 100)

            cols_to_check = [
                c for c in [
                    "GST_STATE_CODE",
                    "FOLIO_OLD"
                ]
                if c in df.columns
            ]

            print(df[cols_to_check].head(50).to_string(index=False))

            print("\nRows where GST_STATE_CODE is B:")
            print(
                df[df["GST_STATE_CODE"].astype(str).str.strip() == "B"]
                [cols_to_check]
                .head(50)
                .to_string(index=False)
            )

            print("\nRows where FOLIO_OLD is 24:")
            print(
                df[df["FOLIO_OLD"].astype(str).str.strip() == "24"]
                [cols_to_check]
                .head(50)
                .to_string(index=False)
            )

            print("=" * 100)

        print(
            f"CSV rows parsed: {len(df)} | "
            f"Malformed rows: {bad_rows}"
        )

    # =================================================
    # EXCEL
    # =================================================
    else:

        file.seek(0)

        df = pd.read_excel(
            file,
            dtype=str,
            keep_default_na=False
        )

    if "Product Code" in df.columns:
        print(df["Product Code"].head())
    print("KFIN Columns:")
    print(df.columns.tolist())
    # =====================================================
    # CLEAN COLUMN NAMES
    # =====================================================

    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
    )

     # Remove blank header columns
    df = df.loc[:, df.columns != ""]

    # Remove duplicate columns
    df = df.loc[:, ~df.columns.duplicated()]

    print(f"\nProcessing file: {file.name}")

    print("Shape:", df.shape)
    print("Columns:", len(df.columns))
    print("Unique columns:", len(df.columns.unique()))

    duplicates = df.columns[df.columns.duplicated()]
    print("Duplicate columns:", duplicates.tolist())

   

    # =====================================================
    # CLEAN VALUES
    # =====================================================

    # object_cols = df.select_dtypes(include="object").columns

    # if len(object_cols):

    #     df[object_cols] = (
    #         df[object_cols]
    #         .astype(str)
    #         .apply(
    #             lambda s: s.str.replace("'", "", regex=False)
    #                     .str.replace('"', "", regex=False)
    #                     .str.strip()
    #         )
    #         .replace({
    #             "nan": "",
    #             "None": "",
    #             "<NA>": ""
    #         })
    #     )
    
    object_cols = df.select_dtypes(include="object").columns.unique()

    for col in object_cols:
        df[col] = (
            df[col]
            .astype(str)
            #.str.replace("'", "", regex=False)
            #.str.replace('"', "", regex=False)
            .str.strip()
            .replace({
                "nan": "",
                "None": "",
                "<NA>": ""
            })
        )

    # =====================================================
    # DEBUG
    # =====================================================

    print("\n" + "=" * 80)
    print("FILE :", file.name)
    print("ROWS READ :", len(df))
    print("TOTAL COLUMNS :", len(df.columns))
    print("COLUMN NAMES :")
    print(df.columns.tolist())
    print("=" * 80 + "\n")
    if "PERIOD_DAY" in df.columns:
        print("\n========== PERIOD_DAY AFTER FILE READ ==========")
        print(df["PERIOD_DAY"].head(50).tolist())
        print("===============================================\n")

    return df


def extract_and_push(uploaded_files):

    cams_transaction = []
    kfin_transaction = []
    cams_investor = []
    kfin_investor = []
    cams_sip = []
    kfin_sip = []

    for file in uploaded_files:

        name = file.name.lower()
        df = read_file(file)

        # ===========================
        # CAMS Files
        # ===========================

        if name.endswith("r2.csv"):
            cams_transaction.append(df)

        elif name.endswith("r9.csv"):
            cams_investor.append(df)

        elif name.endswith("r49.csv"):
            cams_sip.append(df)

        # ===========================
        # KFIN Files
        # ===========================

        elif "mfsd201" in name:
            kfin_transaction.append(df)

        elif "mfsd211" in name:
            kfin_investor.append(df)

        elif "mfsd243" in name:
            kfin_sip.append(df)

        else:
            print(f"Unknown file type: {file.name}")

    # =====================================================
    # Transactions
    # =====================================================

    cams_df = (
        pd.concat(cams_transaction, ignore_index=True)
        if cams_transaction else None
    )

    kfin_df = (
        pd.concat(kfin_transaction, ignore_index=True)
        if kfin_transaction else None
    )

    if cams_df is not None or kfin_df is not None:

        process_transactions(
            cams=cams_df,
            kfin=kfin_df
        )
        print("=" * 80)
        print("TRANSACTION DATA BEFORE ETL")
        
        if cams_df is not None:
            print("CAMS")
            print(cams_df.shape)
            print(cams_df.columns.tolist())

        if kfin_df is not None:
            print("KFIN")
            print(kfin_df.shape)
            print(kfin_df.columns.tolist())
        print("=" * 80)

    # Investors
    cams_df = (
        pd.concat(cams_investor, ignore_index=True)
        if cams_investor else None
    )

    kfin_df = (
        pd.concat(kfin_investor, ignore_index=True)
        if kfin_investor else None
    )

    if cams_df is not None or kfin_df is not None:
        process_investor_master(
            cams=cams_df,
            kfin=kfin_df
        )

    # SIP

    cams_df = (
        pd.concat(cams_sip, ignore_index=True)
        if cams_sip else None
    )

    kfin_df = (
        pd.concat(kfin_sip, ignore_index=True)
        if kfin_sip else None
    )

    sip_preview = None

    if cams_df is not None or kfin_df is not None:

        process_sip(
            cams=cams_df,
            kfin=kfin_df,
            cams_source="CAMS",
            kfin_source="KFIN"
        )

        sip_preview = pd.read_sql(
            "SELECT * FROM bronze.sip_master_new",
            con=engine
        )

    return (
        len(cams_transaction) + len(kfin_transaction),
        len(cams_investor) + len(kfin_investor),
        len(cams_sip) + len(kfin_sip),
        sip_preview
    )