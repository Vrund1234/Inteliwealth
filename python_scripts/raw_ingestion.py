import csv
import io
import os
import tempfile

import pandas as pd
from dbfread import DBF

from utils.db import engine
from etl_investor_master import process_investor_master
from etl_trans import process_transactions
from etl_sip import process_sip


# =====================================================
# SMART SPLIT
# =====================================================

def smart_split(line, delimiter):

    fields = []

    i = 0
    n = len(line)

    while i <= n:

        if i < n and line[i] == "'":

            j = i + 1

            close = line.find("'", j)

            while close != -1 and not (
                close + 1 == n or line[close + 1] == delimiter
            ):

                close = line.find("'", close + 1)

            if close == -1:

                # Unterminated quote - treat rest of line as the field
                fields.append(line[i + 1:])
                i = n + 1

            else:

                fields.append(line[i + 1:close])
                i = close + 2  # skip closing quote + delimiter

        else:

            next_delim = line.find(delimiter, i)

            if next_delim == -1:

                fields.append(line[i:])
                i = n + 1

            else:

                fields.append(line[i:next_delim])
                i = next_delim + 1

    return fields


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

        raw = file.read()

        # -------------------------------------------------
        # DETECT ENCODING
        # -------------------------------------------------

        encodings = [
            "utf-8-sig",
            "utf-8",
            "utf-16",
            "utf-16le",
            "utf-16be",
            "latin1"
        ]

        text = None
        detected_encoding = None

        for encoding in encodings:

            try:

                text = raw.decode(encoding)
                detected_encoding = encoding

                break

            except UnicodeDecodeError:

                continue

        if text is None:

            raise ValueError(
                "Unable to decode uploaded file."
            )

        print()
        print("Detected encoding:", detected_encoding)

        # -------------------------------------------------
        # REMOVE NULL CHARACTERS
        # -------------------------------------------------

        text = text.replace("\x00", "")

        # -------------------------------------------------
        # NORMALIZE NEWLINES
        # -------------------------------------------------

        text = (
            text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

        # -------------------------------------------------
        # DETECT DELIMITER
        # -------------------------------------------------

        sample = text[:10000]

        try:

            dialect = csv.Sniffer().sniff(
                sample,
                delimiters=",\t;|"
            )

            delimiter = dialect.delimiter

        except csv.Error:

            first_line = text.split("\n")[0]

            if "\t" in first_line:

                delimiter = "\t"

            elif "," in first_line:

                delimiter = ","

            elif ";" in first_line:

                delimiter = ";"

            elif "|" in first_line:

                delimiter = "|"

            else:

                delimiter = ","

        print(
            "Detected delimiter:",
            repr(delimiter)
        )

        # -------------------------------------------------
        # IMPORTANT
        #
        # CAMS/KFIN files may contain values surrounded
        # by single quotes, and those values can contain
        # the delimiter itself.
        #
        # smart_split() handles this case.
        # -------------------------------------------------

        lines = text.split("\n")

        rows = [
            smart_split(line, delimiter)
            for line in lines
            if line != ""
        ]

        if not rows:

            raise ValueError(
                f"No rows found in file: {file.name}"
            )

        # -------------------------------------------------
        # HEADER
        # -------------------------------------------------

        header = rows[0]

        header = [
            str(col)
            .strip()
            .strip("'")
            .strip('"')
            .strip()
            for col in header
        ]

        expected_columns = len(header)

        print()
        print("File:", file.name)
        print("Delimiter:", repr(delimiter))
        print("Header columns:", expected_columns)
        print("Header:")
        print(header)

        # -------------------------------------------------
        # PARSE DATA ROWS
        # -------------------------------------------------

        clean_rows = []

        bad_rows = 0

        for row_number, row in enumerate(
            rows[1:],
            start=2
        ):

            # Ignore completely empty rows

            if not row or all(
                str(value).strip() == ""
                for value in row
            ):

                continue

            actual_columns = len(row)

            # -------------------------------------------------
            # PERFECT ROW
            # -------------------------------------------------

            if actual_columns == expected_columns:

                clean_rows.append(row)

                continue

            # -------------------------------------------------
            # SHORT ROW
            #
            # Missing trailing values are allowed.
            # Fill only the missing trailing fields.
            # -------------------------------------------------

            if actual_columns < expected_columns:

                row = row + (
                    [""] *
                    (expected_columns - actual_columns)
                )

                clean_rows.append(row)

                continue

            # -------------------------------------------------
            # BAD ROW
            #
            # DO NOT truncate it.
            # -------------------------------------------------

            bad_rows += 1

            print(
                f"BAD CSV ROW {row_number}: "
                f"Expected {expected_columns}, "
                f"Found {actual_columns}"
            )

            print(
                "Row preview:",
                row[:20]
            )

            # Skip genuinely malformed rows

        # -------------------------------------------------
        # CREATE DATAFRAME
        # -------------------------------------------------

        df = pd.DataFrame(
            clean_rows,
            columns=header
        )

        print()
        print(
            "Rows successfully parsed:",
            len(df)
        )

        print(
            "Bad rows skipped:",
            bad_rows
        )

        # -------------------------------------------------
        # REMOVE OUTER QUOTES ONLY
        # -------------------------------------------------

        object_cols = df.select_dtypes(
            include="object"
        ).columns

        for col in object_cols:

            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .str.strip("'")
                .str.strip('"')
                .str.strip()
            )

            df[col] = df[col].replace(
                {
                    "nan": "",
                    "None": "",
                    "<NA>": ""
                }
            )

    # =================================================
    # DBF
    # =================================================

    elif name.endswith(".dbf"):

        print()
        print("=" * 80)
        print("READING DBF FILE")
        print("=" * 80)

        print(
            "File:",
            file.name
        )

        file.seek(0)

        raw = file.read()

        temp_path = None

        try:

            # -------------------------------------------------
            # DBF READER NEEDS A FILE
            #
            # Streamlit UploadedFile is an in-memory object,
            # therefore save it temporarily.
            # -------------------------------------------------

            with tempfile.NamedTemporaryFile(
                suffix=".dbf",
                delete=False
            ) as temp_file:

                temp_file.write(raw)

                temp_path = temp_file.name

            print(
                "Temporary DBF file created:",
                temp_path
            )

            # -------------------------------------------------
            # READ DBF
            # -------------------------------------------------

            table = DBF(
                temp_path,
                load=True
            )

            # Convert DBF records to DataFrame

            df = pd.DataFrame(
                iter(table)
            )

            print(
                "DBF rows read:",
                len(df)
            )

            print(
                "DBF columns:",
                len(df.columns)
            )

            print(
                "DBF column names:"
            )

            print(
                df.columns.tolist()
            )

        except Exception as e:

            print()
            print(
                "ERROR READING DBF FILE"
            )

            print(
                "Error type:",
                type(e).__name__
            )

            print(
                "Error:",
                e
            )

            raise

        finally:

            # -------------------------------------------------
            # REMOVE TEMPORARY FILE
            # -------------------------------------------------

            if (
                temp_path
                and os.path.exists(temp_path)
            ):

                try:

                    os.remove(temp_path)

                    print(
                        "Temporary DBF file removed."
                    )

                except Exception as cleanup_error:

                    print(
                        "Warning: unable to remove "
                        "temporary DBF file:",
                        cleanup_error
                    )

        # -------------------------------------------------
        # NORMALIZE DBF VALUES
        #
        # DBF can return:
        # - Decimal
        # - date
        # - datetime
        # - bool
        # - None
        #
        # Convert values to strings so that DBF behaves
        # consistently with the existing CSV/Excel pipeline.
        # -------------------------------------------------

        df = df.fillna("")

        object_cols = df.columns

        for col in object_cols:

            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
            )

            df[col] = df[col].replace(
                {
                    "nan": "",
                    "None": "",
                    "NaT": ""
                }
            )

    # =================================================
    # EXCEL
    # =================================================

    elif name.endswith((".xlsx", ".xls")):

        file.seek(0)

        df = pd.read_excel(
            file,
            dtype=str,
            keep_default_na=False
        )

    # =================================================
    # UNSUPPORTED FILE
    # =================================================

    else:

        raise ValueError(
            f"Unsupported file format: {file.name}"
        )

    # =====================================================
    # CLEAN COLUMN NAMES
    # =====================================================

    df.columns = (
        df.columns
        .astype(str)
        .str.strip()
        .str.strip("'")
        .str.strip('"')
        .str.strip()
    )

    # -----------------------------------------------------
    # REMOVE BLANK HEADER COLUMNS
    # -----------------------------------------------------

    df = df.loc[
        :,
        df.columns != ""
    ]

    # -----------------------------------------------------
    # REMOVE DUPLICATE COLUMNS
    # -----------------------------------------------------

    df = df.loc[
        :,
        ~df.columns.duplicated()
    ]

    # =====================================================
    # DEBUG
    # =====================================================

    print()
    print("=" * 80)
    print("FILE :", file.name)
    print("ROWS READ :", len(df))
    print("TOTAL COLUMNS :", len(df.columns))
    print("UNIQUE COLUMNS :", len(df.columns.unique()))
    print("COLUMN NAMES :")
    print(df.columns.tolist())
    print("=" * 80)

    # =====================================================
    # PERIOD DAY DEBUG
    # =====================================================

    if "PERIOD_DAY" in df.columns:

        print()
        print(
            "========== PERIOD_DAY AFTER FILE READ =========="
        )

        print(
            df["PERIOD_DAY"]
            .head(50)
            .tolist()
        )

        print(
            "==============================================="
        )

    # =====================================================
    # IMPORTANT FIELD DEBUG
    # =====================================================

    debug_columns = [
        "INV_NAME",
        "ADDRESS1",
        "ADDRESS2",
        "ADDRESS3",
        "Investor Name",
        "Address #1",
        "Address #2",
        "Address #3",
        "Product Code",
        "Scheme",
        "Folio",
        "PAN",
        "PAN Number"
    ]

    available_debug_columns = [
        col
        for col in debug_columns
        if col in df.columns
    ]

    if available_debug_columns:

        print()
        print(
            "========== FIELD MAPPING DEBUG =========="
        )

        print(
            df[
                available_debug_columns
            ].head(5).to_string(index=False)
        )

        print(
            "========================================="
        )

    return df


# =====================================================
# EXTRACT AND PUSH
# =====================================================

def extract_and_push(uploaded_files):

    cams_transaction = []
    kfin_transaction = []

    cams_investor = []
    kfin_investor = []

    cams_sip = []
    kfin_sip = []

    # =================================================
    # READ ALL FILES
    # =================================================

    for file in uploaded_files:

        name = file.name.lower()

        print()
        print("=" * 80)
        print("PROCESSING FILE:", file.name)
        print("=" * 80)

        df = read_file(file)

        # =================================================
        # CAMS FILES
        #
        # Support CSV and DBF
        # =================================================

        if name.endswith(
            (
                "r2.csv",
                "r2.dbf",
                "r2.xlsx",
                "r2.xls"
            )
        ):

            cams_transaction.append(df)

        elif name.endswith(
            (
                "r9.csv",
                "r9.dbf",
                "r9.xlsx",
                "r9.xls"
            )
        ):

            cams_investor.append(df)

        elif name.endswith(
            (
                "r49.csv",
                "r49.dbf",
                "r49.xlsx",
                "r49.xls"
            )
        ):

            cams_sip.append(df)

        # =================================================
        # KFIN FILES
        #
        # Filename contains the file identifier.
        # This works for CSV, Excel and DBF.
        # =================================================

        elif "mfsd201" in name:

            kfin_transaction.append(df)

        elif "mfsd211" in name:

            kfin_investor.append(df)

        elif "mfsd243" in name:

            kfin_sip.append(df)

        else:

            print(
                f"Unknown file type: {file.name}"
            )

    # =====================================================
    # TRANSACTIONS
    # =====================================================

    cams_df = (
        pd.concat(
            cams_transaction,
            ignore_index=True
        )
        if cams_transaction
        else None
    )

    kfin_df = (
        pd.concat(
            kfin_transaction,
            ignore_index=True
        )
        if kfin_transaction
        else None
    )

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        process_transactions(
            cams=cams_df,
            kfin=kfin_df
        )

    # =====================================================
    # INVESTOR MASTER
    # =====================================================

    cams_df = (
        pd.concat(
            cams_investor,
            ignore_index=True
        )
        if cams_investor
        else None
    )

    kfin_df = (
        pd.concat(
            kfin_investor,
            ignore_index=True
        )
        if kfin_investor
        else None
    )

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        process_investor_master(
            cams=cams_df,
            kfin=kfin_df
        )

    # =====================================================
    # SIP
    # =====================================================

    cams_df = (
        pd.concat(
            cams_sip,
            ignore_index=True
        )
        if cams_sip
        else None
    )

    kfin_df = (
        pd.concat(
            kfin_sip,
            ignore_index=True
        )
        if kfin_sip
        else None
    )

    sip_preview = None

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        process_sip(
            cams=cams_df,
            kfin=kfin_df,
            cams_source="CAMS",
            kfin_source="KFIN"
        )

        # UI preview only -- capped and newest-first so this never turns
        # into an unbounded full-table bronze read as the table grows.
        sip_preview = pd.read_sql(
            """
            SELECT *
            FROM bronze.sip_master_new
            ORDER BY created_at DESC
            LIMIT 1000
            """,
            con=engine
        )

    # =====================================================
    # RETURN RESULTS
    # =====================================================

    return (
        len(cams_transaction)
        +
        len(kfin_transaction),

        len(cams_investor)
        +
        len(kfin_investor),

        len(cams_sip)
        +
        len(kfin_sip),

        sip_preview
    )