# ============================================================
# data_validator.py
#
# Robust validation / recovery layer for CAMS + KFintech files
#
# Supports:
#   1. Headered files
#   2. Headerless files
#   3. Reordered headers
#   4. Renamed / slightly changed headers
#   5. Completely blank first rows
#   6. Misplaced strongly-typed values
#   7. Row-level validation errors
#
# IMPORTANT
# ------------------------------------------------------------
# process_validation() keeps the original 2-value return:
#
#       valid_df, error_df
#
# This keeps raw_ingestion.py compatible.
#
# error_df additionally contains the COMPLETE offending row
# so that raw_ingestion.py / Streamlit can later display it.
# ============================================================

import os
import re
from datetime import date, datetime
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
import yaml


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

MAPPING_FILE = os.path.join(
    BASE_DIR,
    "column_mapping.yaml"
)


# ============================================================
# LOAD YAML
# ============================================================

if not os.path.exists(MAPPING_FILE):
    raise FileNotFoundError(
        f"column_mapping.yaml not found:\n{MAPPING_FILE}"
    )

with open(
    MAPPING_FILE,
    "r",
    encoding="utf-8"
) as file:

    COLUMN_MAPPING = yaml.safe_load(file)


if not isinstance(COLUMN_MAPPING, dict):
    raise ValueError(
        "column_mapping.yaml must contain a dictionary."
    )


# ============================================================
# CONSTANTS
# ============================================================

HEADER_DETECTION_THRESHOLD = 0.60

COLUMN_NAME_MATCH_THRESHOLD = 0.55

VALUE_TYPE_RECOVERY_THRESHOLD = 0.60

MIN_NON_BLANK_FOR_TYPE_INFERENCE = 1

MAX_DATE = pd.Timestamp.today().normalize()


# ============================================================
# BASIC HELPERS
# ============================================================

def is_blank(value):
    """
    Safely determine whether a value is blank/null.
    """

    if value is None:
        return True

    if isinstance(value, str):
        value = value.replace("\x00", "").strip()

        if value == "":
            return True

        if value.upper() in {
            "NULL",
            "NONE",
            "NAN",
            "NA",
            "N/A",
            "<NA>"
        }:
            return True

    try:

        result = pd.isna(value)

        if isinstance(result, (bool, np.bool_)):
            return bool(result)

    except Exception:
        pass

    return False


def clean_value(value):
    """
    Convert values into safe validation values.
    """

    if is_blank(value):
        return ""

    if isinstance(value, bytes):

        value = value.decode(
            "utf-8",
            errors="replace"
        )

    if isinstance(value, str):

        value = (
            value
            .replace("\x00", "")
            .replace("\ufeff", "")
            .strip()
        )

        return value

    if isinstance(value, pd.Timestamp):

        return value

    if isinstance(value, datetime):

        return pd.Timestamp(value)

    if isinstance(value, date):

        return pd.Timestamp(value)

    return value


def clean_dataframe(df):
    """
    Clean dataframe without changing its structure.
    """

    if df is None:
        return df

    result = df.copy()

    for column in result.columns:

        result[column] = result[column].map(
            clean_value
        )

    return result


# ============================================================
# COMPLETELY BLANK ROW
# ============================================================

def is_completely_blank_row(row):
    """
    True only when every cell in the row is blank.
    """

    if row is None:
        return True

    for value in row:

        if not is_blank(value):
            return False

    return True


def remove_leading_blank_rows(df):
    """
    Remove ONLY leading rows that are completely blank.

    This is important because a single NULL first row should not
    become an artificial record.

    We do NOT remove arbitrary blank rows from the middle.
    """

    if df is None or df.empty:
        return df

    result = df.copy()

    while not result.empty:

        first_row = result.iloc[0]

        if not is_completely_blank_row(first_row):
            break

        result = result.iloc[1:].reset_index(drop=True)

    return result


# ============================================================
# COLUMN NAME NORMALIZATION
# ============================================================

def normalize_column_name(name):
    """
    Convert column names into a comparable representation.

    Examples:

        PAN NO       -> PAN_NO
        pan_no       -> PAN_NO
        Pan-No       -> PAN_NO
        PAN.NO       -> PAN_NO
        PANNUMBER    -> PANNUMBER
    """

    if name is None:
        return ""

    name = str(name)

    name = (
        name
        .replace("\x00", "")
        .replace("\ufeff", "")
        .strip()
        .upper()
    )

    name = re.sub(
        r"[^A-Z0-9]+",
        "_",
        name
    )

    name = re.sub(
        r"_+",
        "_",
        name
    )

    return name.strip("_")


# ============================================================
# COLUMN TOKENS
# ============================================================

def column_tokens(name):
    """
    Return normalized semantic tokens.

    Also supports common abbreviations used in RTA files.
    """

    normalized = normalize_column_name(name)

    if not normalized:
        return set()

    tokens = set(
        token
        for token in normalized.split("_")
        if token
    )

    # Common RTA abbreviations
    aliases = {
        "MOB": "MOBILE",
        "MOBILE_NO": "MOBILE",
        "MOBILE_NUMBER": "MOBILE",
        "PH": "PHONE",
        "PH_NO": "PHONE",
        "PHONE_NO": "PHONE",
        "PINCODE": "PIN",
        "PIN_CODE": "PIN",
        "PANCARD": "PAN",
        "PAN_NUMBER": "PAN",
        "PANNO": "PAN",
        "DOB": "DATE",
        "DT": "DATE",
        "EMAILID": "EMAIL",
        "EMAIL_ID": "EMAIL",
    }

    expanded = set()

    for token in tokens:

        expanded.add(
            aliases.get(
                token,
                token
            )
        )

    return expanded


# ============================================================
# COLUMN NAME SIMILARITY
# ============================================================

def column_name_similarity(
    source_name,
    target_name
):
    """
    Combined token + string similarity.
    """

    source_normalized = normalize_column_name(
        source_name
    )

    target_normalized = normalize_column_name(
        target_name
    )

    if not source_normalized or not target_normalized:
        return 0.0

    if source_normalized == target_normalized:
        return 1.0

    source_tokens = column_tokens(
        source_name
    )

    target_tokens = column_tokens(
        target_name
    )

    if source_tokens and target_tokens:

        intersection = len(
            source_tokens.intersection(
                target_tokens
            )
        )

        union = len(
            source_tokens.union(
                target_tokens
            )
        )

        token_score = (
            intersection / union
            if union
            else 0.0
        )

    else:
        token_score = 0.0

    string_score = SequenceMatcher(
        None,
        source_normalized,
        target_normalized
    ).ratio()

    return (
        token_score * 0.60
        +
        string_score * 0.40
    )


# ============================================================
# EXPECTED COLUMNS FROM YAML
# ============================================================

def get_expected_columns(
    file_type,
    master_type
):
    """
    Get configured target/source positional columns.

    These columns are also the fallback positional mapping for
    headerless files.
    """

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

    columns = file_config.get(
        "columns"
    )

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

        column = (
            str(column)
            .replace("\x00", "")
            .strip()
        )

        cleaned.append(column)

    return cleaned


# ============================================================
# VALIDATION RULES
# ============================================================

def get_validation_rules(
    file_type,
    master_type
):
    """
    Read validation rules from YAML.

    Missing validation configuration is allowed.
    """

    if master_type not in COLUMN_MAPPING:
        return {}

    master_config = COLUMN_MAPPING[
        master_type
    ]

    if file_type not in master_config:
        return {}

    file_config = master_config[
        file_type
    ]

    if not isinstance(
        file_config,
        dict
    ):
        return {}

    rules = file_config.get(
        "validation",
        {}
    )

    if not isinstance(
        rules,
        dict
    ):
        return {}

    return rules


# ============================================================
# TYPE DETECTION
# ============================================================

def detect_field_type(column_name):

    name = normalize_column_name(
        column_name
    )

    # --------------------------------------------------------
    # PAN
    # --------------------------------------------------------

    if (
        "JOINT1" in name
        and "PAN" in name
    ):
        return "joint1_pan"

    if (
        "JOINT2" in name
        and "PAN" in name
    ):
        return "joint2_pan"

    if (
        "GUARD" in name
        and "PAN" in name
    ):
        return "guardian_pan"

    if (
        name in {
            "PAN",
            "PAN_NO",
            "PAN_NUMBER",
            "PAN_NUM"
        }
        or (
            "PAN" in name
            and "JOINT" not in name
            and "GUARD" not in name
        )
    ):
        return "pan"

    # --------------------------------------------------------
    # EMAIL
    # --------------------------------------------------------

    if "EMAIL" in name:
        return "email"

    # --------------------------------------------------------
    # PHONE / MOBILE
    # --------------------------------------------------------

    if (
        "PHONE" in name
        or "MOBILE" in name
        or "TELEPHONE" in name
        or name.startswith("PH_")
    ):
        return "phone"

    # --------------------------------------------------------
    # PINCODE
    # --------------------------------------------------------

    if (
        "PIN" in name
        or "POSTAL" in name
        or "ZIP" in name
    ):
        return "pincode"

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    if (
        "DATE" in name
        or "DOB" in name
        or name.endswith("_DT")
        or name.endswith("_DATE")
    ):
        return "date"

    # --------------------------------------------------------
    # PERCENTAGE
    # --------------------------------------------------------

    if (
        "PERCENT" in name
        or "PERC" in name
        or "PCT" in name
        or "PERCENTAGE" in name
    ):
        return "percentage"

    # --------------------------------------------------------
    # NUMERIC
    # --------------------------------------------------------

    numeric_keywords = (
        "AMOUNT",
        "VALUE",
        "UNITS",
        "UNIT",
        "BALANCE",
        "BAL",
        "NAV",
        "PRICE",
        "RATE",
        "QTY",
        "QUANTITY",
        "PERCENT"
    )

    if any(
        keyword in name
        for keyword in numeric_keywords
    ):
        return "numeric"

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    return "text"


# ============================================================
# PAN
# ============================================================

PAN_PATTERN = re.compile(
    r"^[A-Z]{5}[0-9]{4}[A-Z]$"
)


def is_pan(value):

    if is_blank(value):
        return False

    value = clean_value(
        value
    )

    value = str(
        value
    ).strip().upper()

    return bool(
        PAN_PATTERN.fullmatch(
            value
        )
    )


def validate_pan(value):

    if is_blank(value):
        return True, None

    if not is_pan(value):

        return (
            False,
            "Invalid PAN format"
        )

    return True, None


# ============================================================
# PHONE
# ============================================================

def normalize_phone(value):

    if is_blank(value):
        return ""

    value = clean_value(
        value
    )

    value = str(
        value
    ).strip()

    normalized = (
        value
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    if normalized.startswith("+91"):
        normalized = normalized[3:]

    elif (
        normalized.startswith("91")
        and len(normalized) == 12
    ):
        normalized = normalized[2:]

    return normalized


def looks_like_phone(value):

    normalized = normalize_phone(
        value
    )

    if not normalized:
        return False

    return (
        len(normalized) == 10
        and normalized.isdigit()
        and normalized[0] in "6789"
    )


def validate_phone(value):

    if is_blank(value):
        return True, None

    normalized = normalize_phone(
        value
    )

    if not normalized.isdigit():

        return (
            False,
            "Phone number must contain digits only"
        )

    if len(normalized) != 10:

        return (
            False,
            "Phone number must contain exactly 10 digits"
        )

    if normalized[0] not in "6789":

        return (
            False,
            "Phone number must start with 6, 7, 8 or 9"
        )

    return True, None


# ============================================================
# PINCODE
# ============================================================

def looks_like_pincode(value):

    if is_blank(value):
        return False

    value = clean_value(
        value
    )

    value = str(
        value
    ).strip()

    return (
        len(value) == 6
        and value.isdigit()
        and value[0] != "0"
    )


def validate_pincode(value):

    if is_blank(value):
        return True, None

    value = clean_value(
        value
    )

    value = str(
        value
    ).strip()

    if not value.isdigit():

        return (
            False,
            "Pincode must contain digits only"
        )

    if len(value) != 6:

        return (
            False,
            "Pincode must contain exactly 6 digits"
        )

    if value[0] == "0":

        return (
            False,
            "Pincode cannot start with 0"
        )

    return True, None


# ============================================================
# EMAIL
# ============================================================

EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@"
    r"[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def looks_like_email(value):

    if is_blank(value):
        return False

    return bool(
        EMAIL_PATTERN.fullmatch(
            str(
                clean_value(value)
            )
        )
    )


def validate_email(value):

    if is_blank(value):
        return True, None

    if not looks_like_email(value):

        return (
            False,
            "Invalid email format"
        )

    return True, None


# ============================================================
# DATE
# ============================================================

def parse_date(value):

    if is_blank(value):
        return None

    if isinstance(
        value,
        pd.Timestamp
    ):
        return value

    if isinstance(
        value,
        datetime
    ):
        return pd.Timestamp(value)

    if isinstance(
        value,
        date
    ):
        return pd.Timestamp(value)

    try:

        parsed = pd.to_datetime(
            value,
            errors="coerce",
            dayfirst=False
        )

        if pd.isna(parsed):
            return None

        return parsed

    except Exception:
        return None


def looks_like_date(value):

    return (
        parse_date(value)
        is not None
    )


def validate_date(value):

    if is_blank(value):
        return True, None

    parsed = parse_date(
        value
    )

    if parsed is None:

        return (
            False,
            "Invalid date"
        )

    return True, None


def validate_not_future(value):

    if is_blank(value):
        return True, None

    parsed = parse_date(
        value
    )

    if parsed is None:

        return (
            False,
            "Invalid date"
        )

    if parsed.normalize() > MAX_DATE:

        return (
            False,
            "Date cannot be in the future"
        )

    return True, None


# ============================================================
# NUMERIC
# ============================================================

def numeric_value(value):

    if is_blank(value):
        return None

    if isinstance(
        value,
        bool
    ):
        return None

    try:

        text_value = str(
            clean_value(value)
        )

        text_value = (
            text_value
            .replace(",", "")
            .replace("₹", "")
            .replace("%", "")
            .strip()
        )

        if text_value == "":
            return None

        return float(
            text_value
        )

    except Exception:
        return None


def looks_numeric(value):

    return (
        numeric_value(value)
        is not None
    )


def validate_numeric(value):

    if is_blank(value):
        return True, None

    if numeric_value(value) is None:

        return (
            False,
            "Value must be numeric"
        )

    return True, None


def validate_non_negative(value):

    if is_blank(value):
        return True, None

    number = numeric_value(
        value
    )

    if number is None:

        return (
            False,
            "Value must be numeric"
        )

    if number < 0:

        return (
            False,
            "Value cannot be negative"
        )

    return True, None


def validate_percentage(value):

    if is_blank(value):
        return True, None

    number = numeric_value(
        value
    )

    if number is None:

        return (
            False,
            "Percentage must be numeric"
        )

    if number < 0 or number > 100:

        return (
            False,
            "Percentage must be between 0 and 100"
        )

    return True, None


# ============================================================
# DIGITS
# ============================================================

def validate_digits(value):

    if is_blank(value):
        return True, None

    value = str(
        clean_value(value)
    )

    if not value.isdigit():

        return (
            False,
            "Value must contain digits only"
        )

    return True, None


# ============================================================
# LENGTH
# ============================================================

def validate_length(
    value,
    expected_length
):

    if is_blank(value):
        return True, None

    value = str(
        clean_value(value)
    )

    if len(value) != int(
        expected_length
    ):

        return (
            False,
            f"Value must contain exactly "
            f"{expected_length} characters"
        )

    return True, None


def validate_min_length(
    value,
    minimum
):

    if is_blank(value):
        return True, None

    value = str(
        clean_value(value)
    )

    if len(value) < int(
        minimum
    ):

        return (
            False,
            f"Value must contain at least "
            f"{minimum} characters"
        )

    return True, None


def validate_max_length(
    value,
    maximum
):

    if is_blank(value):
        return True, None

    value = str(
        clean_value(value)
    )

    if len(value) > int(
        maximum
    ):

        return (
            False,
            f"Value must not exceed "
            f"{maximum} characters"
        )

    return True, None


# ============================================================
# REQUIRED
# ============================================================

def validate_required(value):

    if is_blank(value):

        return (
            False,
            "Value is required"
        )

    return True, None


# ============================================================
# ALLOWED VALUES
# ============================================================

def validate_allowed_values(
    value,
    allowed_values
):

    if is_blank(value):
        return True, None

    value = str(
        clean_value(value)
    ).strip()

    allowed = [
        str(item).strip()
        for item in allowed_values
    ]

    # Case-insensitive comparison
    allowed_upper = {
        item.upper()
        for item in allowed
    }

    if value.upper() not in allowed_upper:

        return (
            False,
            "Value must be one of: "
            +
            ", ".join(allowed)
        )

    return True, None


# ============================================================
# VALUE MATCHES TYPE
# ============================================================

def value_matches_field_type(
    value,
    field_type
):

    if is_blank(value):
        return False

    if field_type == "pan":
        return is_pan(value)

    if field_type in {
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    }:
        return is_pan(value)

    if field_type == "phone":
        return looks_like_phone(value)

    if field_type == "pincode":
        return looks_like_pincode(value)

    if field_type == "email":
        return looks_like_email(value)

    if field_type == "date":
        return looks_like_date(value)

    if field_type == "numeric":
        return looks_numeric(value)

    return False


# ============================================================
# COLUMN DATA TYPE SCORE
# ============================================================

def score_column_for_type(
    df,
    column,
    target_type
):

    if column not in df.columns:
        return 0.0

    non_blank = 0
    matches = 0

    for value in df[column]:

        if is_blank(value):
            continue

        non_blank += 1

        if value_matches_field_type(
            value,
            target_type
        ):
            matches += 1

    if non_blank == 0:
        return 0.0

    return matches / non_blank


# ============================================================
# TARGET TYPE FOR EXPECTED COLUMN
# ============================================================

def expected_column_type(
    column_name,
    validation_rules=None
):

    field_type = detect_field_type(
        column_name
    )

    if field_type != "text":
        return field_type

    if validation_rules:

        rules = validation_rules.get(
            column_name,
            []
        )

        if isinstance(
            rules,
            str
        ):
            rules = [rules]

        for rule in rules:

            if isinstance(
                rule,
                str
            ):

                if rule in {
                    "pan",
                    "email",
                    "phone",
                    "pincode",
                    "date",
                    "numeric",
                    "percentage"
                }:
                    return rule

    return "text"


# ============================================================
# HEADER-LIKE VALUE
# ============================================================

def value_looks_like_column_name(
    value,
    expected_columns
):

    if is_blank(value):
        return False

    normalized = normalize_column_name(
        value
    )

    if not normalized:
        return False

    best_score = 0.0

    for expected in expected_columns:

        score = column_name_similarity(
            normalized,
            expected
        )

        best_score = max(
            best_score,
            score
        )

    return (
        best_score
        >= COLUMN_NAME_MATCH_THRESHOLD
    )


# ============================================================
# DETECT WHETHER FIRST ROW IS A HEADER
# ============================================================

def detect_header_row(
    df,
    expected_columns
):
    """
    Determine whether the first DATA row is actually a header.

    This is necessary because raw_ingestion can deliberately read
    the first row as data for headerless files.

    We only remove the row when there is strong evidence.
    """

    if df is None or df.empty:
        return False

    first_row = df.iloc[0]

    non_blank_values = [
        value
        for value in first_row
        if not is_blank(value)
    ]

    if not non_blank_values:
        return False

    matches = 0

    for value in non_blank_values:

        if value_looks_like_column_name(
            value,
            expected_columns
        ):
            matches += 1

    ratio = (
        matches / len(non_blank_values)
        if non_blank_values
        else 0
    )

    return (
        ratio
        >= HEADER_DETECTION_THRESHOLD
    )


# ============================================================
# HEADER MAPPING
# ============================================================

def map_header_columns(
    actual_headers,
    expected_columns
):
    """
    Map actual header names to expected columns.

    Exact matches are preferred.

    Fuzzy matches are only accepted when confidence is strong.

    Each expected column can only be assigned once.
    """

    actual_headers = list(
        actual_headers
    )

    expected_columns = list(
        expected_columns
    )

    assignments = {}

    used_expected = set()

    # --------------------------------------------------------
    # PASS 1: exact normalized match
    # --------------------------------------------------------

    for actual in actual_headers:

        actual_normalized = (
            normalize_column_name(
                actual
            )
        )

        exact_candidates = []

        for expected in expected_columns:

            if expected in used_expected:
                continue

            expected_normalized = (
                normalize_column_name(
                    expected
                )
            )

            if (
                actual_normalized
                and
                actual_normalized
                == expected_normalized
            ):

                exact_candidates.append(
                    expected
                )

        if len(exact_candidates) == 1:

            expected = exact_candidates[0]

            assignments[actual] = expected

            used_expected.add(
                expected
            )

    # --------------------------------------------------------
    # PASS 2: strong fuzzy match
    # --------------------------------------------------------

    remaining_actual = [
        column
        for column in actual_headers
        if column not in assignments
    ]

    remaining_expected = [
        column
        for column in expected_columns
        if column not in used_expected
    ]

    candidates = []

    for actual in remaining_actual:

        for expected in remaining_expected:

            score = column_name_similarity(
                actual,
                expected
            )

            if score >= COLUMN_NAME_MATCH_THRESHOLD:

                candidates.append(
                    (
                        score,
                        actual,
                        expected
                    )
                )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    for score, actual, expected in candidates:

        if actual in assignments:
            continue

        if expected in used_expected:
            continue

        assignments[actual] = expected

        used_expected.add(
            expected
        )

    return assignments


# ============================================================
# REORDER / REPAIR HEADERED DATA
# ============================================================

def repair_headered_dataframe(
    df,
    expected_columns
):
    """
    Handle a dataframe where the first row may actually contain
    headers.

    The function supports:

        Headered + correct order
        Headered + changed order
        Headered + changed names
    """

    if df is None or df.empty:
        return df, False

    result = df.copy()

    if not detect_header_row(
        result,
        expected_columns
    ):
        return result, False

    header_row = [
        clean_value(value)
        for value in result.iloc[0].tolist()
    ]

    assignments = map_header_columns(
        header_row,
        expected_columns
    )

    # --------------------------------------------------------
    # We need sufficient confidence before changing structure.
    # --------------------------------------------------------

    mapped_count = len(
        assignments
    )

    expected_count = len(
        expected_columns
    )

    if expected_count == 0:
        return result, False

    mapping_ratio = (
        mapped_count / expected_count
    )

    if mapping_ratio < 0.50:
        # Not enough evidence.
        # Treat first row as actual data.
        return result, False

    # --------------------------------------------------------
    # Remove header row.
    # --------------------------------------------------------

    data = result.iloc[1:].copy()

    # --------------------------------------------------------
    # Rename known headers.
    #
    # Unmatched columns receive positional fallback names only
    # when the number of columns is identical.
    # --------------------------------------------------------

    new_columns = []

    for index, actual in enumerate(
        header_row
    ):

        if actual in assignments:

            new_columns.append(
                assignments[actual]
            )

        elif (
            index < len(expected_columns)
        ):

            # Positional fallback for unmatched renamed header.
            new_columns.append(
                expected_columns[index]
            )

        else:

            new_columns.append(
                f"UNMAPPED_COLUMN_{index + 1}"
            )

    # --------------------------------------------------------
    # Duplicate column protection
    # --------------------------------------------------------

    seen = {}

    safe_columns = []

    for column in new_columns:

        if column not in seen:

            seen[column] = 0
            safe_columns.append(
                column
            )

        else:

            seen[column] += 1

            safe_columns.append(
                f"{column}__DUP{seen[column]}"
            )

    data.columns = safe_columns

    # --------------------------------------------------------
    # If mapping covered the complete expected structure,
    # reorder it into expected order.
    # --------------------------------------------------------

    if (
        all(
            column in data.columns
            for column in expected_columns
        )
    ):

        ordered = (
            expected_columns
            +
            [
                column
                for column in data.columns
                if column not in expected_columns
            ]
        )

        data = data[
            ordered
        ]

    data = data.reset_index(
        drop=True
    )

    return data, True


# ============================================================
# POSITIONAL HEADERLESS MAPPING
# ============================================================

def assign_positional_columns(
    df,
    expected_columns
):
    """
    Headerless fallback.

    The first physical field is mapped to the first configured
    column, second field to second configured column, etc.

    No data row is discarded.
    """

    if df is None:
        return df

    result = df.copy()

    actual_count = len(
        result.columns
    )

    expected_count = len(
        expected_columns
    )

    if actual_count != expected_count:

        raise ValueError(
            "HEADERLESS FILE COLUMN COUNT MISMATCH\n\n"
            f"Expected columns : {expected_count}\n"
            f"Found columns    : {actual_count}\n\n"
            "The file cannot safely be positionally mapped."
        )

    result.columns = list(
        expected_columns
    )

    return result


# ============================================================
# HANDLE HEADER / HEADERLESS DATAFRAME
# ============================================================

def normalize_input_structure(
    df,
    file_type,
    master_type
):
    """
    Main structural recovery function.

    Order:

        1. Clean
        2. Remove completely blank leading rows
        3. Detect accidental header row
        4. If header exists -> map/reorder
        5. Otherwise -> positional mapping
    """

    if df is None:
        return df

    expected_columns = get_expected_columns(
        file_type,
        master_type
    )

    result = clean_dataframe(
        df
    )

    # --------------------------------------------------------
    # Remove only completely blank leading rows.
    # --------------------------------------------------------

    result = remove_leading_blank_rows(
        result
    )

    if result is None or result.empty:
        return result

    # --------------------------------------------------------
    # If columns already exactly match configured names,
    # preserve them.
    # --------------------------------------------------------

    actual_normalized = [
        normalize_column_name(column)
        for column in result.columns
    ]

    expected_normalized = [
        normalize_column_name(column)
        for column in expected_columns
    ]

    if actual_normalized == expected_normalized:

        return result

    # --------------------------------------------------------
    # Try to detect a header accidentally read as data.
    # --------------------------------------------------------

    repaired, was_header = (
        repair_headered_dataframe(
            result,
            expected_columns
        )
    )

    if was_header:

        return repaired

    # --------------------------------------------------------
    # If the dataframe has actual meaningful header names,
    # try mapping them.
    #
    # This supports reordered / renamed columns even when the
    # header was already used by pandas.
    # --------------------------------------------------------

    header_assignments = map_header_columns(
        result.columns,
        expected_columns
    )

    if len(
        header_assignments
    ) >= max(
        1,
        int(
            len(expected_columns)
            * 0.60
        )
    ):

        renamed = result.rename(
            columns=header_assignments
        )

        # If enough expected columns are known, reorder them.
        ordered = [
            column
            for column in expected_columns
            if column in renamed.columns
        ]

        ordered += [
            column
            for column in renamed.columns
            if column not in ordered
        ]

        renamed = renamed[
            ordered
        ]

        return renamed

    # --------------------------------------------------------
    # Headerless fallback.
    #
    # Important:
    # if columns are generic 0,1,2...
    # or unknown names, the data itself is preserved.
    # --------------------------------------------------------

    generic_columns = True

    for column in result.columns:

        normalized = normalize_column_name(
            column
        )

        if normalized not in {
            "",
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9"
        }:

            # pandas may produce "Unnamed: 0"
            if not normalized.startswith(
                "UNNAMED"
            ):
                generic_columns = False
                break

    if generic_columns:

        return assign_positional_columns(
            result,
            expected_columns
        )

    # --------------------------------------------------------
    # Last safe fallback:
    #
    # if the count matches, use configured positional order.
    # This is specifically for headerless source files.
    # --------------------------------------------------------

    if len(
        result.columns
    ) == len(
        expected_columns
    ):

        return assign_positional_columns(
            result,
            expected_columns
        )

    return result


# ============================================================
# PROTECTED PAN LOGIC
# ============================================================

def is_protected_pan_column(
    column
):

    field_type = detect_field_type(
        column
    )

    return field_type in {
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    }


def is_safe_investor_pan_source(
    source_column,
    target_column
):

    source_type = detect_field_type(
        source_column
    )

    target_type = detect_field_type(
        target_column
    )

    if target_type != "pan":
        return False

    if source_type in {
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    }:
        return False

    return True


# ============================================================
# BEST SOURCE COLUMN
# ============================================================

def find_best_source_column(
    df,
    target_column,
    target_type
):

    candidates = []

    for column in df.columns:

        if column == target_column:
            continue

        # ----------------------------------------------------
        # PAN protection
        # ----------------------------------------------------

        if target_type == "pan":

            if not is_safe_investor_pan_source(
                column,
                target_column
            ):
                continue

        type_score = score_column_for_type(
            df,
            column,
            target_type
        )

        if type_score < VALUE_TYPE_RECOVERY_THRESHOLD:
            continue

        name_score = column_name_similarity(
            column,
            target_column
        )

        combined = (
            type_score * 0.75
            +
            name_score * 0.25
        )

        candidates.append(
            (
                combined,
                type_score,
                name_score,
                column
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2]
        ),
        reverse=True
    )

    return candidates[0][3]


# ============================================================
# RECOVER STRONGLY TYPED VALUES
# ============================================================

def recover_typed_columns(
    df,
    file_type,
    master_type
):
    """
    Recover misplaced values when the data type gives us
    strong evidence.

    IMPORTANT:

    We do NOT move generic text values because that can silently
    corrupt names, addresses, scheme names, etc.

    We only recover strongly identifiable types.
    """

    if df is None or df.empty:
        return df

    result = df.copy()

    recoverable_types = {
        "pan",
        "email",
        "phone",
        "pincode",
        "date",
        "numeric"
    }

    for target_column in list(
        result.columns
    ):

        target_type = detect_field_type(
            target_column
        )

        if target_type not in recoverable_types:
            continue

        current_score = score_column_for_type(
            result,
            target_column,
            target_type
        )

        # Already mostly correct.
        if current_score >= 0.50:
            continue

        source_column = find_best_source_column(
            result,
            target_column,
            target_type
        )

        if source_column is None:
            continue

        # ----------------------------------------------------
        # Prevent source == target.
        # ----------------------------------------------------

        if source_column == target_column:
            continue

        # ----------------------------------------------------
        # Recover row by row.
        # ----------------------------------------------------

        for index in result.index:

            current_value = result.at[
                index,
                target_column
            ]

            source_value = result.at[
                index,
                source_column
            ]

            # Never overwrite an already valid value.
            if value_matches_field_type(
                current_value,
                target_type
            ):
                continue

            if is_blank(
                source_value
            ):
                continue

            if not value_matches_field_type(
                source_value,
                target_type
            ):
                continue

            # ------------------------------------------------
            # PAN safety.
            # ------------------------------------------------

            if target_type == "pan":

                if not is_safe_investor_pan_source(
                    source_column,
                    target_column
                ):
                    continue

            result.at[
                index,
                target_column
            ] = source_value

            # ------------------------------------------------
            # Clear the source only when it is not a protected
            # PAN column.
            # ------------------------------------------------

            if not is_protected_pan_column(
                source_column
            ):

                result.at[
                    index,
                    source_column
                ] = ""

    return result


# ============================================================
# INVESTOR PAN RECOVERY
# ============================================================

def get_pan_columns(
    columns
):

    result = {
        "pan": [],
        "joint1_pan": [],
        "joint2_pan": [],
        "guardian_pan": []
    }

    for column in columns:

        field_type = detect_field_type(
            column
        )

        if field_type in result:

            result[
                field_type
            ].append(
                column
            )

    return result


def recover_investor_pan(
    df
):

    if df is None or df.empty:
        return df

    result = df.copy()

    pan_columns = get_pan_columns(
        result.columns
    )

    investor_pan_columns = pan_columns[
        "pan"
    ]

    if not investor_pan_columns:
        return result

    investor_pan_column = (
        investor_pan_columns[0]
    )

    protected_columns = set(
        pan_columns["joint1_pan"]
        +
        pan_columns["joint2_pan"]
        +
        pan_columns["guardian_pan"]
    )

    # --------------------------------------------------------
    # Find candidate PAN columns.
    # --------------------------------------------------------

    candidates = []

    for column in result.columns:

        if column == investor_pan_column:
            continue

        if column in protected_columns:
            continue

        non_blank = 0
        matches = 0

        for value in result[column]:

            if is_blank(value):
                continue

            non_blank += 1

            if is_pan(value):
                matches += 1

        if matches == 0:
            continue

        ratio = (
            matches / non_blank
            if non_blank
            else 0
        )

        candidates.append(
            (
                ratio,
                matches,
                column
            )
        )

    if not candidates:
        return result

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1]
        ),
        reverse=True
    )

    best_candidate = None

    for ratio, matches, column in candidates:

        if ratio >= 0.50:

            best_candidate = column
            break

    if best_candidate is None:
        return result

    # --------------------------------------------------------
    # Fill only missing/invalid investor PAN values.
    # --------------------------------------------------------

    for index in result.index:

        current = result.at[
            index,
            investor_pan_column
        ]

        candidate = result.at[
            index,
            best_candidate
        ]

        if is_pan(current):
            continue

        if not is_pan(candidate):
            continue

        result.at[
            index,
            investor_pan_column
        ] = candidate

        result.at[
            index,
            best_candidate
        ] = ""

    return result


# ============================================================
# INVESTOR RECOVERY
# ============================================================

def recover_investor_columns(
    df,
    file_type,
    master_type
):

    if (
        master_type
        != "investor_master"
    ):
        return df

    if df is None or df.empty:
        return df

    print()
    print("=" * 80)
    print("INVESTOR COLUMN / VALUE RECOVERY")
    print("=" * 80)

    result = recover_typed_columns(
        df,
        file_type,
        master_type
    )

    result = recover_investor_pan(
        result
    )

    return result


# ============================================================
# GENERIC RULE EXECUTOR
# ============================================================

def apply_rule(
    value,
    rule
):

    # --------------------------------------------------------
    # STRING RULE
    # --------------------------------------------------------

    if isinstance(
        rule,
        str
    ):

        if rule == "required":
            return validate_required(value)

        if rule == "digits":
            return validate_digits(value)

        if rule == "numeric":
            return validate_numeric(value)

        if rule == "non_negative":
            return validate_non_negative(value)

        if rule == "phone":
            return validate_phone(value)

        if rule == "pincode":
            return validate_pincode(value)

        if rule == "pan":
            return validate_pan(value)

        if rule == "email":
            return validate_email(value)

        if rule == "date":
            return validate_date(value)

        if rule == "not_future":
            return validate_not_future(value)

        if rule == "percentage":
            return validate_percentage(value)

        return (
            False,
            f"Unknown validation rule: {rule}"
        )

    # --------------------------------------------------------
    # DICTIONARY RULE
    # --------------------------------------------------------

    if isinstance(
        rule,
        dict
    ):

        if "length" in rule:

            return validate_length(
                value,
                rule["length"]
            )

        if "min_length" in rule:

            return validate_min_length(
                value,
                rule["min_length"]
            )

        if "max_length" in rule:

            return validate_max_length(
                value,
                rule["max_length"]
            )

        if "allowed_values" in rule:

            return validate_allowed_values(
                value,
                rule["allowed_values"]
            )

        # Support:
        #
        # {type: pan}
        # {type: numeric}
        #
        if "type" in rule:

            return apply_rule(
                value,
                rule["type"]
            )

        return (
            False,
            f"Unknown validation configuration: {rule}"
        )

    return (
        False,
        f"Invalid validation rule: {rule}"
    )


# ============================================================
# FIELD TYPE VALIDATION
#
# Additional safety validation is performed even if the YAML
# does not explicitly specify a rule.
# ============================================================

def validate_detected_field_type(
    value,
    column
):

    field_type = detect_field_type(
        column
    )

    if is_blank(value):
        return True, None

    # --------------------------------------------------------
    # Important PAN columns
    # --------------------------------------------------------

    if field_type == "pan":

        if not is_pan(value):

            return (
                False,
                "Invalid PAN format"
            )

        return True, None

    if field_type in {
        "joint1_pan",
        "joint2_pan",
        "guardian_pan"
    }:

        if not is_pan(value):

            return (
                False,
                "Invalid PAN format"
            )

        return True, None

    # --------------------------------------------------------
    # Email
    # --------------------------------------------------------

    if field_type == "email":

        return validate_email(
            value
        )

    # --------------------------------------------------------
    # Phone
    # --------------------------------------------------------

    if field_type == "phone":

        return validate_phone(
            value
        )

    # --------------------------------------------------------
    # Pincode
    # --------------------------------------------------------

    if field_type == "pincode":

        return validate_pincode(
            value
        )

    # --------------------------------------------------------
    # Numeric
    # --------------------------------------------------------

    if field_type == "numeric":

        return validate_numeric(
            value
        )

    return True, None


# ============================================================
# ROW VALIDATION
# ============================================================

def validate_row(
    row,
    validation_rules,
    row_number,
    expected_columns=None
):

    errors = []

    # --------------------------------------------------------
    # YAML RULES
    # --------------------------------------------------------

    for column, rules in validation_rules.items():

        if column not in row.index:

            errors.append({
                "row_number": row_number,
                "column_name": column,
                "value": None,
                "error_type": "STRUCTURE",
                "error_message": "Column not found"
            })

            continue

        value = row[column]

        if isinstance(
            rules,
            str
        ):
            rules = [rules]

        if not isinstance(
            rules,
            list
        ):
            rules = [rules]

        for rule in rules:

            valid, error_message = apply_rule(
                value,
                rule
            )

            if not valid:

                errors.append({
                    "row_number": row_number,
                    "column_name": column,
                    "value": value,
                    "error_type": "VALIDATION",
                    "error_message": error_message
                })

    # --------------------------------------------------------
    # AUTOMATIC FIELD-TYPE VALIDATION
    #
    # Only strongly identifiable fields.
    # --------------------------------------------------------

    columns_to_check = (
        expected_columns
        if expected_columns
        else list(row.index)
    )

    for column in columns_to_check:

        if column not in row.index:
            continue

        # Do not duplicate YAML rule failures.
        already_has_error = any(
            error["column_name"] == column
            for error in errors
        )

        if already_has_error:
            continue

        value = row[column]

        valid, error_message = (
            validate_detected_field_type(
                value,
                column
            )
        )

        if not valid:

            errors.append({
                "row_number": row_number,
                "column_name": column,
                "value": value,
                "error_type": "TYPE",
                "error_message": error_message
            })

    return errors


# ============================================================
# DUPLICATE VALIDATION
# ============================================================

def find_duplicate_rows(
    df
):

    if df is None or df.empty:
        return set()

    duplicate_mask = df.duplicated(
        keep=False
    )

    return set(
        df.index[
            duplicate_mask
        ].tolist()
    )


# ============================================================
# BUILD COMPLETE ERROR ROW
# ============================================================

def build_error_dataframe(
    df,
    errors
):

    if not errors:
        return pd.DataFrame()

    # --------------------------------------------------------
    # Group validation messages by row.
    # --------------------------------------------------------

    grouped = {}

    for error in errors:

        row_number = error[
            "row_number"
        ]

        grouped.setdefault(
            row_number,
            []
        ).append(
            error
        )

    error_rows = []

    for row_number, row_errors in grouped.items():

        zero_based_index = (
            row_number - 1
        )

        if (
            zero_based_index < 0
            or
            zero_based_index >= len(df)
        ):
            continue

        source_row = df.iloc[
            zero_based_index
        ]

        # ----------------------------------------------------
        # Main error information
        # ----------------------------------------------------

        error_columns = sorted(
            set(
                str(
                    error["column_name"]
                )
                for error in row_errors
            )
        )

        error_messages = []

        for error in row_errors:

            error_messages.append(
                f"{error['column_name']}: "
                f"{error['error_message']}"
            )

        record = {
            "row_number": row_number,
            "error_type": " | ".join(
                sorted(
                    set(
                        error["error_type"]
                        for error in row_errors
                    )
                )
            ),
            "error_columns": " | ".join(
                error_columns
            ),
            "error_message": " | ".join(
                error_messages
            )
        }

        # ----------------------------------------------------
        # COMPLETE ORIGINAL ROW
        #
        # Prefix with source_ so that it never clashes with
        # validation metadata.
        # ----------------------------------------------------

        for column in df.columns:

            safe_column = str(
                column
            )

            record[
                f"source_{safe_column}"
            ] = source_row[
                column
            ]

        error_rows.append(
            record
        )

    return pd.DataFrame(
        error_rows
    )


# ============================================================
# STRUCTURAL ERRORS
# ============================================================

def validate_structure(
    df,
    expected_columns
):

    errors = []

    if df is None:

        errors.append({
            "row_number": 0,
            "column_name": "",
            "value": None,
            "error_type": "STRUCTURE",
            "error_message": "Input dataframe is None"
        })

        return errors

    actual_count = len(
        df.columns
    )

    expected_count = len(
        expected_columns
    )

    if actual_count != expected_count:

        errors.append({
            "row_number": 0,
            "column_name": "",
            "value": None,
            "error_type": "STRUCTURE",
            "error_message": (
                "Column count mismatch. "
                f"Expected {expected_count}, "
                f"found {actual_count}"
            )
        })

    # --------------------------------------------------------
    # Duplicate column names
    # --------------------------------------------------------

    normalized_names = [
        normalize_column_name(column)
        for column in df.columns
    ]

    duplicates = {
        name
        for name in normalized_names
        if name
        and normalized_names.count(name) > 1
    }

    if duplicates:

        errors.append({
            "row_number": 0,
            "column_name": "",
            "value": None,
            "error_type": "STRUCTURE",
            "error_message": (
                "Duplicate column names detected: "
                +
                ", ".join(
                    sorted(duplicates)
                )
            )
        })

    return errors


# ============================================================
# ALL-NULL COLUMNS
# ============================================================

def find_all_null_columns(
    df
):

    if df is None or df.empty:
        return []

    result = []

    for column in df.columns:

        if all(
            is_blank(value)
            for value in df[column]
        ):

            result.append(
                column
            )

    return result


# ============================================================
# VALIDATE DATAFRAME
# ============================================================

def validate_data(
    df,
    file_type,
    master_type
):
    """
    Complete validation pipeline.

    Returns:

        valid_df
        error_df

    The error dataframe contains the complete source row for
    every invalid record.
    """

    if df is None:

        return (
            pd.DataFrame(),
            pd.DataFrame()
        )

    print()
    print("=" * 80)
    print("STARTING DATA VALIDATION")
    print("=" * 80)

    print(
        "File type:",
        file_type
    )

    print(
        "Master type:",
        master_type
    )

    print(
        "Input rows:",
        len(df)
    )

    print(
        "Input columns:",
        len(df.columns)
    )

    # ========================================================
    # STEP 1 : CLEAN
    # ========================================================

    working_df = clean_dataframe(
        df
    )

    # ========================================================
    # STEP 2 : STRUCTURAL RECOVERY
    # ========================================================

    try:

        working_df = normalize_input_structure(
            working_df,
            file_type,
            master_type
        )

    except Exception as exc:

        print(
            "Structure recovery warning:",
            str(exc)
        )

        # Do not destroy the source dataframe.
        working_df = clean_dataframe(
            df
        )

    # ========================================================
    # STEP 3 : REMOVE LEADING COMPLETELY BLANK ROWS
    # ========================================================

    working_df = remove_leading_blank_rows(
        working_df
    )

    if working_df is None:

        return (
            pd.DataFrame(),
            pd.DataFrame()
        )

    # ========================================================
    # STEP 4 : EXPECTED COLUMNS
    # ========================================================

    try:

        expected_columns = get_expected_columns(
            file_type,
            master_type
        )

    except Exception:

        expected_columns = list(
            working_df.columns
        )

    # ========================================================
    # STEP 5 : INVESTOR-SPECIFIC RECOVERY
    # ========================================================

    working_df = recover_investor_columns(
        working_df,
        file_type,
        master_type
    )

    # ========================================================
    # STEP 6 : VALIDATION RULES
    # ========================================================

    validation_rules = get_validation_rules(
        file_type,
        master_type
    )

    # ========================================================
    # STEP 7 : STRUCTURE VALIDATION
    # ========================================================

    structural_errors = validate_structure(
        working_df,
        expected_columns
    )

    # ========================================================
    # STEP 8 : ALL-NULL COLUMNS
    # ========================================================
    #
    # An all-null optional column is NOT automatically an error.
    #
    # We only report it as informational metadata later.
    # This prevents legitimate files from being rejected merely
    # because an optional field has no data.
    # ========================================================

    all_null_columns = find_all_null_columns(
        working_df
    )

    if all_null_columns:

        print(
            "All-null columns:",
            ", ".join(
                str(column)
                for column in all_null_columns
            )
        )

    # ========================================================
    # STEP 9 : ROW VALIDATION
    # ========================================================

    all_errors = list(
        structural_errors
    )

    for index, row in working_df.iterrows():

        row_number = index + 1

        row_errors = validate_row(
            row=row,
            validation_rules=validation_rules,
            row_number=row_number,
            expected_columns=expected_columns
        )

        all_errors.extend(
            row_errors
        )

    # ========================================================
    # STEP 10 : DUPLICATE ROW CHECK
    # ========================================================

    duplicate_indexes = find_duplicate_rows(
        working_df
    )

    for index in duplicate_indexes:

        row_number = index + 1

        all_errors.append({
            "row_number": row_number,
            "column_name": "",
            "value": None,
            "error_type": "DUPLICATE",
            "error_message": (
                "Duplicate complete source row"
            )
        })

    # ========================================================
    # STEP 11 : NO ERRORS
    # ========================================================

    if not all_errors:

        print()
        print("=" * 80)
        print("VALIDATION PASSED")
        print("=" * 80)

        print(
            "Rows:",
            len(working_df)
        )

        print(
            "Columns:",
            len(working_df.columns)
        )

        return (
            working_df.reset_index(
                drop=True
            ),
            pd.DataFrame()
        )

    # ========================================================
    # STEP 12 : BUILD COMPLETE ERROR DATAFRAME
    # ========================================================

    error_df = build_error_dataframe(
        working_df,
        all_errors
    )

    # ========================================================
    # STEP 13 : INVALID ROW NUMBERS
    # ========================================================

    invalid_row_numbers = set()

    for error in all_errors:

        row_number = error.get(
            "row_number"
        )

        if (
            row_number is not None
            and row_number > 0
        ):

            invalid_row_numbers.add(
                row_number
            )

    # ========================================================
    # STEP 14 : REMOVE INVALID ROWS FROM VALID DATA
    # ========================================================

    valid_indexes = []

    for index in working_df.index:

        row_number = index + 1

        if row_number not in invalid_row_numbers:

            valid_indexes.append(
                index
            )

    valid_df = working_df.loc[
        valid_indexes
    ].copy()

    valid_df = valid_df.reset_index(
        drop=True
    )

    # ========================================================
    # STEP 15 : CLEAN ERROR DATAFRAME
    # ========================================================

    error_df = clean_dataframe(
        error_df
    )

    # ========================================================
    # FINAL LOG
    # ========================================================

    print()
    print("=" * 80)
    print("VALIDATION RESULT")
    print("=" * 80)

    print(
        "Original rows:",
        len(working_df)
    )

    print(
        "Valid rows:",
        len(valid_df)
    )

    print(
        "Invalid rows:",
        len(invalid_row_numbers)
    )

    print(
        "Error records:",
        len(error_df)
    )

    if not error_df.empty:

        print()
        print("Validation error summary:")

        print(
            error_df[
                [
                    "row_number",
                    "error_type",
                    "error_columns",
                    "error_message"
                ]
            ]
            .head(20)
            .to_string(
                index=False
            )
        )

    return (
        valid_df,
        error_df
    )


# ============================================================
# CONVENIENCE FUNCTION
# ============================================================

def process_validation(
    df,
    file_type,
    master_type
):

    return validate_data(
        df=df,
        file_type=file_type,
        master_type=master_type
    )