import csv
import os
import tempfile

import pandas as pd
from dbfread import DBF


# =====================================================
# DATE COLUMNS
# =====================================================

DATE_COLUMNS = [
    "traddate",
    "postdate",
    "rep_date",
    "ticob_posted_date",
    "sys_regn_date",
    "ca_initiated_date",
    "crdate",
    "purdate",
    "chqdate",
    "nav_date",
    "nct_change_date",
    "agent_code_change_request_date",
    "reg_date",
]


# =====================================================
# DATE PARSER
# =====================================================

def parse_source_date(value, source=None):
    """
    Centralized source-date parser.

    The two RTAs do NOT share a date format, and the component order is
    decided by `source`:

        CAMS    M/D/YYYY   3/20/2019   -> 2019-03-20
        KFIN    D/M/YYYY   21/05/2019  -> 2019-05-21

    That is not a guess. In 10072026104746_216882305R2.csv the CAMS
    TRADDATE column has 90,536 values whose FIRST component never once
    exceeds 12 while the second exceeds it 55,812 times -- the first
    component is the month. KFin's MFSD243 RegistrationDate is the mirror
    image: 364 first components above 12, none in the second.

    `source=None` keeps the historical day-first reading, for callers that
    do not know their RTA. Any caller handling CAMS data MUST pass it, or
    every date with a day above the 12th is read as an impossible month and
    coerced to None, and every date below it is silently transposed.

    ISO input is returned as-is regardless of source:

        2026-07-28              -> 2026-07-28

    A trailing time is ignored:

        28-07-2026 14:30:00     -> 2026-07-28   (KFIN)
        3/20/2019 12:00:00 AM   -> 2019-03-20   (CAMS)

    No ambiguous-date guessing is performed: once `source` fixes the
    component order, an out-of-range month is an error, not a prompt to try
    the other order.
    """

    from datetime import datetime, date
    import re

    if value is None:
        return None

    # =================================================
    # PANDAS TIMESTAMP
    # =================================================

    if isinstance(value, pd.Timestamp):

        if pd.isna(value):
            return None

        return value.to_pydatetime().date()

    # =================================================
    # PYTHON DATETIME
    # =================================================

    if isinstance(value, datetime):
        return value.date()

    # =================================================
    # PYTHON DATE
    # =================================================

    if isinstance(value, date):
        return value

    # =================================================
    # PANDAS NULL
    # =================================================

    try:

        if pd.isna(value):
            return None

    except (TypeError, ValueError):

        pass

    # =================================================
    # CLEAN VALUE
    # =================================================

    value = (
        str(value)
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("'", "")
        .replace('"', "")
        .strip()
    )

    # =================================================
    # NULL VALUES
    # =================================================

    if value.lower() in {
        "",
        "nan",
        "none",
        "<na>",
        "nat",
        "null",
    }:
        return None

    # =================================================
    # ISO DATE
    #
    # YYYY-MM-DD
    # YYYY-MM-DD HH:MM:SS
    # YYYY-MM-DDTHH:MM:SS
    # =================================================

    iso_match = re.match(
        r"^(\d{4})-(\d{1,2})-(\d{1,2})",
        value
    )

    if iso_match:

        try:

            return date(
                int(iso_match.group(1)),
                int(iso_match.group(2)),
                int(iso_match.group(3)),
            )

        except ValueError:

            return None

    # =================================================
    # SOURCE FORMAT
    #
    #     CAMS = M/D/YYYY
    #     KFIN = D/M/YYYY
    #
    # Separator can be:
    #     -
    #     /
    # =================================================

    match = re.search(
        r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})",
        value
    )

    if not match:

        return None

    first = int(
        match.group(1)
    )

    second = int(
        match.group(2)
    )

    year = int(
        match.group(3)
    )

    if str(source).strip().upper() == "CAMS":

        month, day = first, second

    else:

        day, month = first, second

    # =================================================
    # STRICT VALIDATION
    #
    # The order is already fixed by `source` above, so an
    # out-of-range component is a bad value -- never a
    # reason to retry with the components the other way
    # round.
    # =================================================

    if not 1 <= day <= 31:

        print(
            f"WARNING: Invalid day in source date: "
            f"'{value}'"
        )

        return None

    if not 1 <= month <= 12:

        print(
            f"WARNING: Invalid month in source date: "
            f"'{value}'"
        )

        return None

    # =================================================
    # CREATE DATE
    # =================================================

    try:

        return date(
            year,
            month,
            day
        )

    except ValueError:

        print(
            f"WARNING: Invalid calendar date: "
            f"'{value}'"
        )

        return None


# =====================================================
# FORMAT DATES
# =====================================================

def format_dates(df, source=None):

    if df is None:
        return df

    df = df.copy()

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        original = df[col].copy()

        parsed = original.apply(
            lambda v: parse_source_date(v, source)
        )

        df[col] = parsed.apply(
            lambda d:
            d.strftime("%Y-%m-%d")
            if d is not None
            else None
        )

        invalid_mask = (
            original.notna()
            & original.astype(str).str.strip().ne("")
            & df[col].isna()
        )

        if invalid_mask.any():

            print(
                f"WARNING: {col} has "
                f"{invalid_mask.sum()} "
                f"unparsed date values."
            )

            print(
                "Unparsed values:",
                original.loc[
                    invalid_mask
                ].head(10).tolist()
            )

    return df


# =====================================================
# VALIDATE DATE COLUMNS
# =====================================================

def validate_date_columns(df, stage):
    """
    Validate populated date fields.

    Valid values are either:

        Python date/datetime

    or:

        YYYY-MM-DD string
    """

    import re
    from datetime import date

    print("=" * 80)
    print(f"DATE VALIDATION - {stage}")

    errors = []

    for col in DATE_COLUMNS:

        if col not in df.columns:
            continue

        non_null = df[col].notna()

        invalid = df.loc[
            non_null
            & ~df[col].apply(
                lambda x:
                isinstance(x, date)
                or (
                    isinstance(x, str)
                    and bool(
                        re.match(
                            r"^\d{4}-\d{2}-\d{2}$",
                            x
                        )
                    )
                )
            )
        ]

        if len(invalid) > 0:

            errors.append(
                f"{col}: "
                f"{len(invalid)} invalid date values"
            )

        print(
            f"{col}: "
            f"non-null={non_null.sum()}, "
            f"null={df[col].isna().sum()}"
        )

        if non_null.any():

            print(
                df.loc[
                    non_null,
                    col
                ].head(5).tolist()
            )

    if errors:

        print("DATE VALIDATION ERRORS:")

        for error in errors:
            print(" -", error)

        raise ValueError(
            f"Date validation failed during "
            f"{stage}: "
            + "; ".join(errors)
        )

    print("Date validation passed.")
    print("=" * 80)


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

            close = line.find(
                "'",
                j
            )

            while (
                close != -1
                and not (
                    close + 1 == n
                    or line[close + 1] == delimiter
                )
            ):

                close = line.find(
                    "'",
                    close + 1
                )

            if close == -1:

                # Unterminated quote.
                fields.append(
                    line[i + 1:]
                )

                i = n + 1

            else:

                fields.append(
                    line[i + 1:close]
                )

                i = close + 2

        else:

            next_delim = line.find(
                delimiter,
                i
            )

            if next_delim == -1:

                fields.append(
                    line[i:]
                )

                i = n + 1

            else:

                fields.append(
                    line[i:next_delim]
                )

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

    if name.endswith(
        (".csv", ".txt")
    ):

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
            "latin1",
        ]

        text = None
        detected_encoding = None

        for encoding in encodings:

            try:

                text = raw.decode(
                    encoding
                )

                detected_encoding = encoding

                break

            except UnicodeDecodeError:

                continue

        if text is None:

            raise ValueError(
                "Unable to decode uploaded file."
            )

        print()
        print(
            "Detected encoding:",
            detected_encoding
        )

        # -------------------------------------------------
        # REMOVE NULL CHARACTERS
        # -------------------------------------------------

        text = text.replace(
            "\x00",
            ""
        )

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

            first_line = text.split(
                "\n"
            )[0]

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
        # SMART PARSE
        # -------------------------------------------------

        lines = text.split("\n")

        rows = [
            smart_split(
                line,
                delimiter
            )
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
        print(
            "File:",
            file.name
        )

        print(
            "Delimiter:",
            repr(delimiter)
        )

        print(
            "Header columns:",
            expected_columns
        )

        print("Header:")
        print(header)

        # -------------------------------------------------
        # DATA ROWS
        # -------------------------------------------------

        clean_rows = []

        bad_rows = 0

        for row_number, row in enumerate(
            rows[1:],
            start=2
        ):

            if (
                not row
                or all(
                    str(value).strip() == ""
                    for value in row
                )
            ):

                continue

            actual_columns = len(row)

            # Perfect row

            if actual_columns == expected_columns:

                clean_rows.append(row)

                continue

            # Short row:
            # fill missing trailing fields

            if actual_columns < expected_columns:

                row = row + (
                    [""] *
                    (
                        expected_columns
                        - actual_columns
                    )
                )

                clean_rows.append(row)

                continue

            # Bad row:
            # do NOT truncate

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

        # -------------------------------------------------
        # DATAFRAME
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
        # REMOVE OUTER QUOTES
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
                    "<NA>": "",
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
            # DBF
            # -------------------------------------------------

            table = DBF(
                temp_path,
                load=True
            )

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

            if (
                temp_path
                and os.path.exists(temp_path)
            ):

                try:

                    os.remove(
                        temp_path
                    )

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
        # -------------------------------------------------

        df = df.fillna("")

        for col in df.columns:

            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
            )

            df[col] = df[col].replace(
                {
                    "nan": "",
                    "None": "",
                    "NaT": "",
                }
            )

    # =================================================
    # EXCEL
    # =================================================

    elif name.endswith(
        (".xlsx", ".xls")
    ):

        file.seek(0)

        df = pd.read_excel(
            file,
            dtype=str,
            keep_default_na=False
        )

    # =================================================
    # UNSUPPORTED
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
    # REMOVE BLANK HEADERS
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
    print(
        "FILE :",
        file.name
    )
    print(
        "ROWS READ :",
        len(df)
    )
    print(
        "TOTAL COLUMNS :",
        len(df.columns)
    )
    print(
        "UNIQUE COLUMNS :",
        len(df.columns.unique())
    )
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
        "PAN Number",
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
            ]
            .head(5)
            .to_string(index=False)
        )

        print(
            "========================================="
        )

    return df


# =====================================================
# EXTRACT AND PUSH
# =====================================================

def extract_and_push(uploaded_files):

    # =================================================
    # IMPORTANT:
    #
    # DO NOT IMPORT ETL MODULES AT THE TOP OF THIS FILE.
    #
    # raw_ingestion -> etl_trans -> raw_ingestion
    #
    # creates a circular import.
    #
    # These imports happen only when the function is
    # called, after raw_ingestion.py has completely loaded.
    # =================================================

    from etl_trans import process_transactions
    from etl_investor_master import process_investor_master
    from etl_sip import process_sip

    # Import database engine here as well if desired.
    # This keeps raw_ingestion independent during import.
    from utils.db import engine

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
        print(
            "PROCESSING FILE:",
            file.name
        )
        print("=" * 80)

        df = read_file(file)

        # =================================================
        # CAMS TRANSACTION
        # =================================================

        if name.endswith(
            (
                "r2.csv",
                "r2.dbf",
                "r2.xlsx",
                "r2.xls",
            )
        ):

            cams_transaction.append(df)

        # =================================================
        # CAMS INVESTOR
        # =================================================

        elif name.endswith(
            (
                "r9.csv",
                "r9.dbf",
                "r9.xlsx",
                "r9.xls",
            )
        ):

            cams_investor.append(df)

        # =================================================
        # CAMS SIP
        # =================================================

        elif name.endswith(
            (
                "r49.csv",
                "r49.dbf",
                "r49.xlsx",
                "r49.xls",
            )
        ):

            cams_sip.append(df)

        # =================================================
        # KFIN TRANSACTION
        # =================================================

        elif (
            "mfsd201" in name
            or "mfsd307" in name
        ):

            kfin_transaction.append(df)

        # =================================================
        # KFIN INVESTOR
        # =================================================

        elif (
            "mfsd211" in name
            or "mfsd311" in name
        ):

            kfin_investor.append(df)

        # =================================================
        # KFIN SIP
        # =================================================

        elif (
            "mfsd243" in name
            or "mfsd313" in name
        ):

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
        or kfin_df is not None
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
        or kfin_df is not None
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
        or kfin_df is not None
    ):

        process_sip(
            cams=cams_df,
            kfin=kfin_df,
            cams_source="CAMS",
            kfin_source="KFIN"
        )

        # -------------------------------------------------
        # UI preview only
        # -------------------------------------------------

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
        + len(kfin_transaction),

        len(cams_investor)
        + len(kfin_investor),

        len(cams_sip)
        + len(kfin_sip),

        sip_preview
    )
