# ============================================================
# raw_ingestion.py
# ============================================================

import csv
import io
import os
import tempfile
import uuid

import pandas as pd
import yaml
from dbfread import DBF

from data_validation.data_validator import process_validation

from utils.db import engine
from etl_investor_master import process_investor_master
from etl_trans import process_transactions
from etl_sip import process_sip


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAPPING_FILE = os.path.join(
    BASE_DIR,
    "data_validation",
    "column_mapping.yaml"
)


# ============================================================
# LOAD MAPPING
# ============================================================

if not os.path.exists(MAPPING_FILE):
    raise FileNotFoundError(
        f"column_mapping.yaml not found:\n{MAPPING_FILE}"
    )

with open(
    MAPPING_FILE,
    "r",
    encoding="utf-8"
) as f:

    COLUMN_MAPPING = yaml.safe_load(f)


if not isinstance(COLUMN_MAPPING, dict):
    raise ValueError(
        "column_mapping.yaml must contain a dictionary."
    )


# ============================================================
# FILE TYPE DETECTION
# ============================================================

def detect_file_type(filename):

    name = os.path.basename(filename).lower()

    if "r49" in name:
        return "R49"

    if "r9" in name:
        return "R9"

    if "r2" in name:
        return "R2"

    if "mfsd243" in name:
        return "MFSD243"

    if "mfsd211" in name:
        return "MFSD211"

    if "mfsd201" in name:
        return "MFSD201"

    return None


# ============================================================
# MASTER TYPE
# ============================================================

def get_master_type(file_type):

    mapping = {

        "R9": "investor_master",
        "MFSD211": "investor_master",

        "R2": "transaction_master",
        "MFSD201": "transaction_master",

        "R49": "sip_master",
        "MFSD243": "sip_master",
    }

    return mapping.get(file_type)


# ============================================================
# EXPECTED COLUMNS
# ============================================================

def get_expected_columns(
    file_type,
    master_type
):

    if master_type not in COLUMN_MAPPING:

        raise ValueError(
            f"Master type '{master_type}' "
            f"not found in column_mapping.yaml"
        )

    master_config = COLUMN_MAPPING[
        master_type
    ]

    if file_type not in master_config:

        raise ValueError(
            f"File type '{file_type}' "
            f"not found under '{master_type}'"
        )

    file_config = master_config[
        file_type
    ]

    if not isinstance(
        file_config,
        dict
    ):

        raise ValueError(
            f"Invalid configuration for "
            f"{master_type}/{file_type}"
        )

    columns = file_config.get("columns")

    if not isinstance(
        columns,
        list
    ):

        raise ValueError(
            f"'columns' must be a list for "
            f"{master_type}/{file_type}"
        )

    if not columns:

        raise ValueError(
            f"No columns configured for "
            f"{master_type}/{file_type}"
        )

    cleaned = []

    for column in columns:

        if column is None:
            column = ""

        column = str(column)

        column = (
            column
            .replace("\x00", "")
            .strip()
        )

        cleaned.append(column)

    return cleaned


# ============================================================
# VALUE CLEANING
# ============================================================

def clean_value(value):

    if value is None:
        return ""

    # --------------------------------------------------------
    # UUID
    # --------------------------------------------------------

    if isinstance(
        value,
        uuid.UUID
    ):

        return str(value)

    # --------------------------------------------------------
    # Bytes
    # --------------------------------------------------------

    if isinstance(
        value,
        bytes
    ):

        value = value.decode(
            "utf-8",
            errors="replace"
        )

    # --------------------------------------------------------
    # Strings
    # --------------------------------------------------------

    if isinstance(
        value,
        str
    ):

        value = (
            value
            .replace("\x00", "")
            .replace("\ufeff", "")
        )

        return value.strip()

    # --------------------------------------------------------
    # Pandas NULL values
    # --------------------------------------------------------

    try:

        if pd.isna(value):

            return ""

    except Exception:

        pass

    return value


# ============================================================
# COMPLETELY NULL / BLANK ROW
#
# A row is considered empty ONLY when every field is empty.
#
# Examples:
#
# ["", "", "", ""]       -> REMOVE
# [None, None, None]     -> REMOVE
# ["", "", "ABC", ""]    -> KEEP
# ["123", "", "", ""]    -> KEEP
# ============================================================

def is_completely_blank_row(row):

    for value in row:

        value = clean_value(value)

        if value is None:
            continue

        if isinstance(
            value,
            str
        ):

            if value.strip() != "":
                return False

        else:

            try:

                if not pd.isna(value):
                    return False

            except Exception:

                return False

    return True


# ============================================================
# REMOVE COMPLETELY BLANK ROWS
#
# IMPORTANT:
# This is used for ALL headerless file formats:
#
# CSV
# Excel
# DBF
#
# No row number is skipped blindly.
# ============================================================

def remove_completely_blank_rows(
    df,
    file_name=""
):

    if df is None:
        return df

    if df.empty:
        return df

    df = df.copy()

    blank_mask = df.apply(
        lambda row:
        is_completely_blank_row(
            row.tolist()
        ),
        axis=1
    )

    removed_count = int(
        blank_mask.sum()
    )

    if removed_count > 0:

        print()
        print(
            f"Removed {removed_count} "
            f"completely NULL/blank row(s)."
        )

        removed_indexes = (
            df.index[
                blank_mask
            ].tolist()
        )

        print(
            "Removed row positions:",
            [
                index + 1
                for index in removed_indexes
            ]
        )

    df = df.loc[
        ~blank_mask
    ].reset_index(
        drop=True
    )

    return df


# ============================================================
# DATAFRAME CLEANING
# ============================================================

def clean_dataframe(df):

    if df is None:
        return df

    df = df.copy()

    # --------------------------------------------------------
    # Clean cells
    # --------------------------------------------------------

    for column in df.columns:

        df[column] = df[column].map(
            clean_value
        )

    # --------------------------------------------------------
    # Replace NaN / None
    # --------------------------------------------------------

    df = df.fillna("")

    # --------------------------------------------------------
    # Clean object columns again
    # --------------------------------------------------------

    for column in df.columns:

        if df[column].dtype == "object":

            df[column] = df[column].map(
                clean_value
            )

    return df


# ============================================================
# ARROW / STREAMLIT SAFE DATAFRAME
# ============================================================

def make_dataframe_arrow_safe(df):

    if df is None:
        return df

    df = df.copy()

    for column in df.columns:

        if df[column].dtype == "object":

            df[column] = df[column].map(
                lambda value:
                    str(value)
                    if isinstance(
                        value,
                        uuid.UUID
                    )
                    else value
            )

    return df


# ============================================================
# NUL CHECK
# ============================================================

def check_for_nul(
    df,
    file_name
):

    if df is None:
        return

    for column in df.columns:

        for index, value in df[column].items():

            if (
                isinstance(
                    value,
                    str
                )
                and
                "\x00" in value
            ):

                raise ValueError(
                    f"NUL CHARACTER FOUND\n\n"
                    f"File: {file_name}\n"
                    f"Column: {column}\n"
                    f"Row: {index + 1}"
                )


# ============================================================
# ASSIGN VIRTUAL HEADERS
#
# HEADERLESS FILES
#
# The source does not provide the business headers.
# Therefore the configured headers are assigned strictly
# according to column POSITION.
#
# Example:
#
# Source column 1 -> AMC_CODE
# Source column 2 -> FOLIO_NO
# Source column 3 -> PRODCODE
# ...
# ============================================================

def assign_virtual_headers(
    df,
    expected_columns,
    file_name
):

    expected_count = len(
        expected_columns
    )

    actual_count = len(
        df.columns
    )

    print()
    print("=" * 80)
    print("VIRTUAL HEADER MAPPING")
    print("=" * 80)

    print(
        "File:",
        file_name
    )

    print(
        "Expected columns:",
        expected_count
    )

    print(
        "Actual columns:",
        actual_count
    )

    if actual_count != expected_count:

        raise ValueError(
            f"HEADERLESS FILE COLUMN COUNT MISMATCH\n\n"
            f"File: {file_name}\n"
            f"Expected: {expected_count}\n"
            f"Found: {actual_count}\n\n"
            f"The first row is DATA, not a header."
        )

    df = df.copy()

    df.columns = expected_columns

    print(
        "Virtual headers assigned successfully."
    )

    print(
        "Headers assigned by column position."
    )

    return df


# ============================================================
# ACTUAL FILE FORMAT DETECTION
# ============================================================

def detect_physical_format(file):

    file.seek(0)

    raw = file.read(16)

    file.seek(0)

    # --------------------------------------------------------
    # XLSX
    # --------------------------------------------------------

    if raw.startswith(
        b"PK\x03\x04"
    ):

        return "xlsx"

    # --------------------------------------------------------
    # XLS
    # --------------------------------------------------------

    if raw.startswith(
        b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
    ):

        return "xls"

    # --------------------------------------------------------
    # DBF
    # --------------------------------------------------------

    if len(raw) >= 1:

        dbf_versions = {
            0x02,
            0x03,
            0x04,
            0x05,
            0x30,
            0x31,
            0x32,
            0x43,
            0x63,
            0x83,
            0x8B,
            0x8E,
            0xCB,
            0xF5,
            0xFB
        }

        if raw[0] in dbf_versions:

            return "dbf"

    return "text"


# ============================================================
# DELIMITER DETECTION
# ============================================================

def detect_delimiter(text):

    sample = text[:50000]

    try:

        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=",\t;|"
        )

        return dialect.delimiter

    except csv.Error:

        lines = text.splitlines()

        first_line = ""

        for line in lines:

            if line.strip():

                first_line = line
                break

        candidates = [
            ",",
            "\t",
            ";",
            "|"
        ]

        best_delimiter = ","
        best_count = -1

        for delimiter in candidates:

            count = first_line.count(
                delimiter
            )

            if count > best_count:

                best_count = count
                best_delimiter = delimiter

        return best_delimiter


# ============================================================
# READ CSV
#
# CSV IS HEADERLESS.
# ============================================================

def read_csv_file(
    file,
    expected_columns
):

    print()
    print("=" * 80)
    print("READING CSV/TXT")
    print("=" * 80)

    file.seek(0)

    raw = file.read()

    if isinstance(
        raw,
        str
    ):

        raw = raw.encode(
            "utf-8"
        )

    if not raw:

        raise ValueError(
            f"File is empty:\n{file.name}"
        )

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin1"
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
            f"Unable to decode text file:\n"
            f"{file.name}"
        )

    print(
        "Encoding:",
        detected_encoding
    )

    text = (
        text
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    delimiter = detect_delimiter(
        text
    )

    print(
        "Delimiter:",
        repr(delimiter)
    )

    expected_count = len(
        expected_columns
    )

    reader = csv.reader(
        io.StringIO(text),
        delimiter=delimiter,
        quotechar='"',
        doublequote=True
    )

    rows = []

    for row_number, row in enumerate(
        reader,
        start=1
    ):

        if not row:

            row = [""]

        row = [
            clean_value(value)
            for value in row
        ]

        actual_count = len(row)

        if actual_count == expected_count:

            rows.append(row)

        elif actual_count < expected_count:

            row.extend(
                [""] *
                (
                    expected_count
                    -
                    actual_count
                )
            )

            rows.append(row)

            print(
                f"Row {row_number}: "
                f"{actual_count} fields -> "
                f"padded to {expected_count}"
            )

        else:

            raise ValueError(
                f"CSV COLUMN COUNT ERROR\n\n"
                f"File: {file.name}\n"
                f"Row: {row_number}\n"
                f"Expected: {expected_count}\n"
                f"Found: {actual_count}\n\n"
                f"First values:\n"
                f"{row[:20]}"
            )

    temp_columns = [
        f"_SOURCE_{i + 1}"
        for i in range(
            expected_count
        )
    ]

    df = pd.DataFrame(
        rows,
        columns=temp_columns
    )

    print(
        "Rows read before blank-row removal:",
        len(df)
    )

    # --------------------------------------------------------
    # Remove ONLY completely blank rows.
    # --------------------------------------------------------

    df = remove_completely_blank_rows(
        df,
        file.name
    )

    print(
        "Rows after blank-row removal:",
        len(df)
    )

    df = assign_virtual_headers(
        df,
        expected_columns,
        file.name
    )

    return df


# ============================================================
# READ EXCEL
#
# Excel is also treated as HEADERLESS.
# ============================================================

def read_excel_file(
    file,
    expected_columns
):

    print()
    print("=" * 80)
    print("READING EXCEL")
    print("=" * 80)

    file.seek(0)

    df = pd.read_excel(
        file,
        header=None,
        dtype=object,
        keep_default_na=False
    )

    expected_count = len(
        expected_columns
    )

    print(
        "Rows read:",
        len(df)
    )

    print(
        "Columns read:",
        len(df.columns)
    )

    # --------------------------------------------------------
    # Remove extra completely blank trailing columns.
    # --------------------------------------------------------

    while len(df.columns) > expected_count:

        last_column = df.iloc[:, -1]

        if (
            last_column
            .astype(str)
            .str.strip()
            .eq("")
            .all()
        ):

            df = df.iloc[:, :-1]

        else:

            break

    # --------------------------------------------------------
    # Pad missing columns.
    # --------------------------------------------------------

    while len(df.columns) < expected_count:

        df[len(df.columns)] = ""

    df.columns = [
        f"_SOURCE_{i + 1}"
        for i in range(
            len(df.columns)
        )
    ]

    # --------------------------------------------------------
    # Remove ONLY completely blank rows.
    #
    # If row 1 is completely blank:
    #     row 1 -> removed
    #     row 2 -> becomes first data row
    #
    # If row 1 contains data:
    #     row 1 -> kept
    # --------------------------------------------------------

    df = remove_completely_blank_rows(
        df,
        file.name
    )

    print(
        "Rows after blank-row removal:",
        len(df)
    )

    df = assign_virtual_headers(
        df,
        expected_columns,
        file.name
    )

    return df


# ============================================================
# DBF HEADER INSPECTION
# ============================================================

def inspect_dbf_header(
    raw
):

    if len(raw) < 32:

        return {
            "valid": False,
            "reason": "File smaller than DBF header."
        }

    version = raw[0]

    num_records = int.from_bytes(
        raw[4:8],
        byteorder="little"
    )

    header_length = int.from_bytes(
        raw[8:10],
        byteorder="little"
    )

    record_length = int.from_bytes(
        raw[10:12],
        byteorder="little"
    )

    file_size = len(raw)

    if header_length < 33:

        return {
            "valid": False,
            "reason": (
                f"Invalid header length: "
                f"{header_length}"
            )
        }

    if header_length > file_size:

        return {
            "valid": False,
            "reason": (
                f"Header length {header_length} "
                f"is larger than file size {file_size}."
            )
        }

    if record_length < 1:

        return {
            "valid": False,
            "reason": (
                f"Invalid record length: "
                f"{record_length}"
            )
        }

    descriptor_bytes = (
        header_length
        -
        33
    )

    if descriptor_bytes % 32 != 0:

        return {
            "valid": False,
            "reason": (
                f"Header length {header_length} "
                f"does not describe complete "
                f"32-byte DBF field descriptors."
            )
        }

    field_count = (
        descriptor_bytes // 32
    )

    if field_count <= 0:

        return {
            "valid": False,
            "reason": "No DBF fields found."
        }

    calculated_record_length = 1

    fields = []

    offset = 32

    for _ in range(field_count):

        descriptor = raw[
            offset:
            offset + 32
        ]

        name_bytes = descriptor[
            0:11
        ]

        field_name = (
            name_bytes
            .split(
                b"\x00",
                1
            )[0]
            .decode(
                "latin1",
                errors="replace"
            )
            .strip()
        )

        field_type = chr(
            descriptor[11]
        )

        field_length = descriptor[16]

        decimal_count = descriptor[17]

        fields.append({
            "name": field_name,
            "type": field_type,
            "length": field_length,
            "decimal": decimal_count
        })

        calculated_record_length += (
            field_length
        )

        offset += 32

    terminator = raw[
        header_length - 1
    ]

    if terminator != 0x0D:

        return {
            "valid": False,
            "reason": (
                f"DBF header terminator is "
                f"0x{terminator:02X}, expected 0x0D."
            ),
            "version": version,
            "records": num_records,
            "header_length": header_length,
            "record_length": record_length,
            "field_count": field_count,
            "calculated_record_length":
                calculated_record_length,
            "fields": fields
        }

    if (
        calculated_record_length
        !=
        record_length
    ):

        return {
            "valid": False,
            "reason": (
                f"Record length mismatch. "
                f"Header says {record_length}, "
                f"fields calculate "
                f"{calculated_record_length}."
            ),
            "version": version,
            "records": num_records,
            "header_length": header_length,
            "record_length": record_length,
            "field_count": field_count,
            "calculated_record_length":
                calculated_record_length,
            "fields": fields
        }

    physical_bytes = max(
        0,
        file_size - header_length
    )

    physical_records = (
        physical_bytes // record_length
    )

    return {
        "valid": True,
        "version": version,
        "records": num_records,
        "header_length": header_length,
        "record_length": record_length,
        "field_count": field_count,
        "calculated_record_length":
            calculated_record_length,
        "physical_bytes":
            physical_bytes,
        "physical_records":
            physical_records,
        "fields": fields
    }


# ============================================================
# CREATE EMPTY DBF DATAFRAME
# ============================================================

def create_empty_dbf_dataframe(
    expected_columns,
    file_name
):

    temp_columns = [
        f"_SOURCE_{i + 1}"
        for i in range(
            len(expected_columns)
        )
    ]

    df = pd.DataFrame(
        columns=temp_columns
    )

    df = assign_virtual_headers(
        df,
        expected_columns,
        file_name
    )

    return df


# ============================================================
# MANUAL DBF READER
# ============================================================

def read_dbf_manual(
    file,
    expected_columns
):

    file.seek(0)

    raw = file.read()

    info = inspect_dbf_header(
        raw
    )

    if not info["valid"]:

        raise ValueError(
            f"INVALID / CORRUPTED DBF FILE\n\n"
            f"File: {file.name}\n"
            f"Reason: {info.get('reason')}"
        )

    fields = info["fields"]

    source_columns = [
        field["name"]
        for field in fields
    ]

    print(
        "Manual DBF fields:",
        len(source_columns)
    )

    print(
        "Expected fields:",
        len(expected_columns)
    )

    if len(source_columns) != len(
        expected_columns
    ):

        raise ValueError(
            f"DBF FIELD COUNT MISMATCH\n\n"
            f"File: {file.name}\n"
            f"Expected: {len(expected_columns)}\n"
            f"Found: {len(source_columns)}\n\n"
            f"DBF fields:\n{source_columns}"
        )

    header_length = info[
        "header_length"
    ]

    record_length = info[
        "record_length"
    ]

    declared_records = info[
        "records"
    ]

    physical_records = info.get(
        "physical_records",
        0
    )

    print()
    print("=" * 80)
    print("DBF RECORD ANALYSIS")
    print("=" * 80)

    print(
        "Records declared in header:",
        declared_records
    )

    print(
        "Physical records available:",
        physical_records
    )

    if physical_records <= 0:

        print(
            "DBF contains zero physical records."
        )

        return create_empty_dbf_dataframe(
            expected_columns,
            file.name
        )

    rows = []

    data_start = header_length

    for record_number in range(
        physical_records
    ):

        start = (
            data_start
            +
            record_number *
            record_length
        )

        end = (
            start
            +
            record_length
        )

        if end > len(raw):

            break

        record = raw[
            start:end
        ]

        if not record:
            continue

        # ----------------------------------------------------
        # Deleted record
        # ----------------------------------------------------

        if record[0] == 0x2A:

            print(
                f"Skipping deleted DBF record "
                f"{record_number + 1}"
            )

            continue

        position = 1

        row = []

        for field in fields:

            length = field["length"]

            value_bytes = record[
                position:
                position + length
            ]

            position += length

            field_type = field["type"]

            if field_type in (
                "C",
                "V"
            ):

                value = value_bytes.decode(
                    "latin1",
                    errors="replace"
                ).strip()

            elif field_type in (
                "D",
                "L",
                "N",
                "F",
                "M"
            ):

                value = value_bytes.decode(
                    "ascii",
                    errors="ignore"
                ).strip()

            else:

                value = value_bytes.decode(
                    "latin1",
                    errors="replace"
                ).strip()

            row.append(
                clean_value(value)
            )

        if len(row) == len(
            expected_columns
        ):

            rows.append(row)

    print(
        "Physical rows extracted:",
        len(rows)
    )

    temp_columns = [
        f"_SOURCE_{i + 1}"
        for i in range(
            len(expected_columns)
        )
    ]

    df = pd.DataFrame(
        rows,
        columns=temp_columns
    )

    # --------------------------------------------------------
    # Remove ONLY completely blank rows.
    # --------------------------------------------------------

    df = remove_completely_blank_rows(
        df,
        file.name
    )

    print(
        "Rows after blank-row removal:",
        len(df)
    )

    df = assign_virtual_headers(
        df,
        expected_columns,
        file.name
    )

    return df


# ============================================================
# READ DBF
#
# IMPORTANT:
#
# The DBF internal field names are NOT used as the
# application's final headings.
#
# We read records by their physical position and then
# assign expected_columns by position.
# ============================================================

def read_dbf_file(
    file,
    expected_columns
):

    print()
    print("=" * 80)
    print("READING DBF")
    print("=" * 80)

    file.seek(0)

    temp_path = None

    try:

        # ----------------------------------------------------
        # Save uploaded file to temporary DBF
        # ----------------------------------------------------

        with tempfile.NamedTemporaryFile(
            suffix=".dbf",
            delete=False
        ) as temp:

            temp.write(
                file.read()
            )

            temp_path = temp.name

        # ----------------------------------------------------
        # Inspect physical DBF
        # ----------------------------------------------------

        file.seek(0)

        raw = file.read()

        info = inspect_dbf_header(
            raw
        )

        if not info["valid"]:

            raise ValueError(
                f"INVALID / CORRUPTED DBF FILE\n\n"
                f"File: {file.name}\n"
                f"Reason: {info.get('reason')}"
            )

        source_columns = [
            field["name"]
            for field in info["fields"]
        ]

        physical_records = info.get(
            "physical_records",
            0
        )

        declared_records = info.get(
            "records",
            0
        )

        print(
            "DBF internal fields:",
            len(source_columns)
        )

        print(
            "Expected virtual columns:",
            len(expected_columns)
        )

        print(
            "DBF declared records:",
            declared_records
        )

        print(
            "Physical DBF records:",
            physical_records
        )

        # ----------------------------------------------------
        # Field count
        # ----------------------------------------------------

        if len(source_columns) != len(
            expected_columns
        ):

            raise ValueError(
                f"DBF FIELD COUNT MISMATCH\n\n"
                f"File: {file.name}\n"
                f"Expected: {len(expected_columns)}\n"
                f"Found: {len(source_columns)}\n\n"
                f"DBF fields:\n{source_columns}"
            )

        # ====================================================
        # EMPTY DBF
        # ====================================================

        if physical_records <= 0:

            print(
                "DBF has valid structure but zero records."
            )

            return create_empty_dbf_dataframe(
                expected_columns,
                file.name
            )

        # ====================================================
        # DBFREAD
        # ====================================================

        try:

            dbf = DBF(
                temp_path,
                load=True,
                char_decode_errors="ignore",
                ignore_missing_memofile=True
            )

            dbf_columns = list(
                dbf.field_names
            )

            print(
                "DBF fields from dbfread:",
                len(dbf_columns)
            )

            if len(dbf_columns) != len(
                expected_columns
            ):

                raise ValueError(
                    f"DBF FIELD COUNT MISMATCH\n\n"
                    f"File: {file.name}\n"
                    f"Expected: {len(expected_columns)}\n"
                    f"Found: {len(dbf_columns)}"
                )

            records = []

            for record in dbf:

                row = []

                # ------------------------------------------------
                # IMPORTANT:
                #
                # Read fields according to DBF physical order.
                # Do NOT use the DBF field names as final headers.
                # ------------------------------------------------

                for source_column in dbf_columns:

                    value = record.get(
                        source_column
                    )

                    row.append(
                        clean_value(value)
                    )

                if len(row) != len(
                    expected_columns
                ):

                    raise ValueError(
                        f"DBF RECORD COLUMN COUNT ERROR\n\n"
                        f"File: {file.name}\n"
                        f"Expected: {len(expected_columns)}\n"
                        f"Found: {len(row)}"
                    )

                records.append(row)

            print(
                "dbfread rows:",
                len(records)
            )

            # ------------------------------------------------
            # Create DataFrame from records.
            # ------------------------------------------------

            temp_columns = [
                f"_SOURCE_{i + 1}"
                for i in range(
                    len(expected_columns)
                )
            ]

            df = pd.DataFrame(
                records,
                columns=temp_columns
            )

            # ------------------------------------------------
            # IMPORTANT:
            #
            # No first-row skip.
            #
            # Only completely blank rows are removed.
            # ------------------------------------------------

            df = remove_completely_blank_rows(
                df,
                file.name
            )

            print(
                "DBF rows after blank-row removal:",
                len(df)
            )

            # ------------------------------------------------
            # Assign virtual business headers.
            # ------------------------------------------------

            df = assign_virtual_headers(
                df,
                expected_columns,
                file.name
            )

            return df

        except Exception as dbf_error:

            print()
            print(
                "dbfread could not read usable records."
            )

            print(
                "dbfread error:",
                repr(dbf_error)
            )

            print(
                "Using manual DBF reader."
            )

            return read_dbf_manual(
                file,
                expected_columns
            )

    finally:

        if (
            temp_path
            and
            os.path.exists(temp_path)
        ):

            try:

                os.remove(
                    temp_path
                )

            except Exception:

                pass


# ============================================================
# READ FILE
# ============================================================

def read_file(file):

    file_name = os.path.basename(
        file.name
    )

    file_type = detect_file_type(
        file_name
    )

    if file_type is None:

        raise ValueError(
            f"Unable to determine file type:\n"
            f"{file_name}"
        )

    master_type = get_master_type(
        file_type
    )

    if master_type is None:

        raise ValueError(
            f"Unable to determine master type "
            f"for {file_type}"
        )

    expected_columns = get_expected_columns(
        file_type,
        master_type
    )

    physical_format = detect_physical_format(
        file
    )

    print()
    print("#" * 80)
    print("READING FILE")
    print("#" * 80)

    print(
        "File:",
        file_name
    )

    print(
        "File type:",
        file_type
    )

    print(
        "Master type:",
        master_type
    )

    print(
        "Expected columns:",
        len(expected_columns)
    )

    print(
        "Physical format:",
        physical_format
    )

    # ========================================================
    # CSV / TEXT
    # ========================================================

    if physical_format == "text":

        lower_name = file_name.lower()

        if not (
            lower_name.endswith(".csv")
            or
            lower_name.endswith(".txt")
        ):

            raise ValueError(
                f"File extension and content do not "
                f"look like a supported text file:\n"
                f"{file_name}"
            )

        df = read_csv_file(
            file,
            expected_columns
        )

    # ========================================================
    # XLSX
    # ========================================================

    elif physical_format == "xlsx":

        df = read_excel_file(
            file,
            expected_columns
        )

    # ========================================================
    # XLS
    # ========================================================

    elif physical_format == "xls":

        df = read_excel_file(
            file,
            expected_columns
        )

    # ========================================================
    # DBF
    # ========================================================

    elif physical_format == "dbf":

        df = read_dbf_file(
            file,
            expected_columns
        )

    else:

        raise ValueError(
            f"Unsupported physical format:\n"
            f"{physical_format}"
        )

    # ========================================================
    # FINAL CLEANING
    # ========================================================

    df = clean_dataframe(
        df
    )

    # --------------------------------------------------------
    # Remove completely blank rows one more time after
    # cleaning, so None / NaN / "" are treated consistently.
    # --------------------------------------------------------

    df = remove_completely_blank_rows(
        df,
        file_name
    )

    check_for_nul(
        df,
        file_name
    )

    # ========================================================
    # FINAL COLUMN CHECK
    # ========================================================

    if len(df.columns) != len(
        expected_columns
    ):

        raise ValueError(
            f"FINAL COLUMN COUNT MISMATCH\n\n"
            f"File: {file_name}\n"
            f"Expected: {len(expected_columns)}\n"
            f"Found: {len(df.columns)}"
        )

    # ========================================================
    # FINAL PREVIEW DATA
    #
    # This is the actual data with the virtual headers.
    # ========================================================

    print()
    print("=" * 80)
    print("DATA PREVIEW")
    print("=" * 80)

    print(
        "Rows:",
        len(df)
    )

    print(
        "Columns:",
        len(df.columns)
    )

    if not df.empty:

        print(
            df.head(10).to_string(
                index=False
            )
        )

    else:

        print(
            "No data rows available."
        )

    # ========================================================
    # VALIDATION
    # ========================================================

    valid_df, error_df = process_validation(
        df=df,
        file_type=file_type,
        master_type=master_type
    )

    # ========================================================
    # CLEAN AFTER VALIDATION
    # ========================================================

    valid_df = clean_dataframe(
        valid_df
    )

    error_df = clean_dataframe(
        error_df
    )

    # --------------------------------------------------------
    # Do not allow completely blank rows into database.
    # --------------------------------------------------------

    valid_df = remove_completely_blank_rows(
        valid_df,
        f"{file_name} - VALID"
    )

    error_df = remove_completely_blank_rows(
        error_df,
        f"{file_name} - ERROR"
    )

    check_for_nul(
        valid_df,
        file_name
    )

    check_for_nul(
        error_df,
        file_name
    )

    # ========================================================
    # ARROW SAFETY
    # ========================================================

    valid_df = make_dataframe_arrow_safe(
        valid_df
    )

    error_df = make_dataframe_arrow_safe(
        error_df
    )

    print()
    print("=" * 80)
    print("VALIDATION RESULT")
    print("=" * 80)

    print(
        "Original:",
        len(df)
    )

    print(
        "Valid:",
        len(valid_df)
    )

    print(
        "Invalid:",
        len(error_df)
    )

    return (
        valid_df,
        error_df
    )


# ============================================================
# SAFE CONCAT
# ============================================================

def combine_dataframes(
    dataframes
):

    if not dataframes:
        return None

    valid_dataframes = [
        df
        for df in dataframes
        if df is not None
        and not df.empty
    ]

    if not valid_dataframes:
        return None

    df = pd.concat(
        valid_dataframes,
        ignore_index=True
    )

    df = clean_dataframe(
        df
    )

    df = remove_completely_blank_rows(
        df
    )

    return df


# ============================================================
# EXTRACT AND PUSH
# ============================================================

def extract_and_push(
    uploaded_files
):

    cams_transaction = []
    kfin_transaction = []

    cams_investor = []
    kfin_investor = []

    cams_sip = []
    kfin_sip = []

    # ========================================================
    # READ ALL FILES
    # ========================================================

    for uploaded_file in uploaded_files:

        print()
        print("#" * 80)

        print(
            "PROCESSING FILE:",
            uploaded_file.name
        )

        print("#" * 80)

        valid_df, error_df = read_file(
            uploaded_file
        )

        file_type = detect_file_type(
            uploaded_file.name
        )

        # ----------------------------------------------------
        # Validation errors
        # ----------------------------------------------------

        if (
            error_df is not None
            and
            not error_df.empty
        ):

            print()
            print(
                "VALIDATION ERRORS:"
            )

            print(
                error_df
                .head(20)
                .to_string(
                    index=False
                )
            )

        # ----------------------------------------------------
        # CAMS
        # ----------------------------------------------------

        if file_type == "R2":

            cams_transaction.append(
                valid_df
            )

        elif file_type == "R9":

            cams_investor.append(
                valid_df
            )

        elif file_type == "R49":

            cams_sip.append(
                valid_df
            )

        # ----------------------------------------------------
        # KFIN
        # ----------------------------------------------------

        elif file_type == "MFSD201":

            kfin_transaction.append(
                valid_df
            )

        elif file_type == "MFSD211":

            kfin_investor.append(
                valid_df
            )

        elif file_type == "MFSD243":

            kfin_sip.append(
                valid_df
            )

        else:

            raise ValueError(
                f"Unknown file type: "
                f"{uploaded_file.name}"
            )

    # ========================================================
    # TRANSACTIONS
    # ========================================================

    cams_df = combine_dataframes(
        cams_transaction
    )

    kfin_df = combine_dataframes(
        kfin_transaction
    )

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        print()
        print(
            "TRANSACTION DATA BEFORE DATABASE INSERT"
        )

        if cams_df is not None:

            print(
                "CAMS transaction rows:",
                len(cams_df)
            )

            print(
                cams_df.head(10).to_string(
                    index=False
                )
            )

        if kfin_df is not None:

            print(
                "KFIN transaction rows:",
                len(kfin_df)
            )

            print(
                kfin_df.head(10).to_string(
                    index=False
                )
            )

        process_transactions(
            cams=cams_df,
            kfin=kfin_df
        )

    # ========================================================
    # INVESTOR MASTER
    # ========================================================

    cams_df = combine_dataframes(
        cams_investor
    )

    kfin_df = combine_dataframes(
        kfin_investor
    )

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        cams_df = (
            clean_dataframe(cams_df)
            if cams_df is not None
            else None
        )

        kfin_df = (
            clean_dataframe(kfin_df)
            if kfin_df is not None
            else None
        )

        if cams_df is not None:

            cams_df = remove_completely_blank_rows(
                cams_df,
                "CAMS INVESTOR"
            )

            check_for_nul(
                cams_df,
                "CAMS INVESTOR"
            )

            print(
                "CAMS INVESTOR ROWS BEFORE DATABASE INSERT:",
                len(cams_df)
            )

            print(
                cams_df.head(10).to_string(
                    index=False
                )
            )

        if kfin_df is not None:

            kfin_df = remove_completely_blank_rows(
                kfin_df,
                "KFIN INVESTOR"
            )

            check_for_nul(
                kfin_df,
                "KFIN INVESTOR"
            )

            print(
                "KFIN INVESTOR ROWS BEFORE DATABASE INSERT:",
                len(kfin_df)
            )

            print(
                kfin_df.head(10).to_string(
                    index=False
                )
            )

        process_investor_master(
            cams=cams_df,
            kfin=kfin_df
        )

    # ========================================================
    # SIP
    # ========================================================

    cams_df = combine_dataframes(
        cams_sip
    )

    kfin_df = combine_dataframes(
        kfin_sip
    )

    sip_preview = None

    if (
        cams_df is not None
        or
        kfin_df is not None
    ):

        cams_df = (
            clean_dataframe(cams_df)
            if cams_df is not None
            else None
        )

        kfin_df = (
            clean_dataframe(kfin_df)
            if kfin_df is not None
            else None
        )

        if cams_df is not None:

            cams_df = remove_completely_blank_rows(
                cams_df,
                "CAMS SIP"
            )

        if kfin_df is not None:

            kfin_df = remove_completely_blank_rows(
                kfin_df,
                "KFIN SIP"
            )

        process_sip(
            cams=cams_df,
            kfin=kfin_df,
            cams_source="CAMS",
            kfin_source="KFIN"
        )

        sip_preview = pd.read_sql(
            """
            SELECT *
            FROM bronze.sip_master_new
            """,
            con=engine
        )

        sip_preview = clean_dataframe(
            sip_preview
        )

        sip_preview = make_dataframe_arrow_safe(
            sip_preview
        )

    # ========================================================
    # RETURN
    # ========================================================

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