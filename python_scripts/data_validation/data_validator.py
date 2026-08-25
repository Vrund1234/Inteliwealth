import re
from datetime import datetime, date

import pandas as pd
import yaml

import os

# ============================================================
# LOAD YAML CONFIGURATION
# ============================================================

MAPPING_FILE = os.path.join(
    os.path.dirname(__file__),
    "column_mapping.yaml"
)

with open(
    MAPPING_FILE,
    "r",
    encoding="utf-8"
) as file:

    COLUMN_MAPPING = yaml.safe_load(file)


# ============================================================
# BASIC HELPERS
# ============================================================

def is_blank(value):
    """
    Check whether a value is blank/null.
    """

    if pd.isna(value):
        return True

    return str(value).strip() == ""


def clean_value(value):
    """
    Convert a value into a clean string for validation.
    """

    if pd.isna(value):
        return ""

    return str(value).strip()


# ============================================================
# GENERIC VALIDATION FUNCTIONS
# ============================================================

def validate_required(value):
    """
    Value must be present.
    """

    if is_blank(value):
        return False, "Value is required"

    return True, None


def validate_digits(value):
    """
    Value must contain digits only.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if not value.isdigit():
        return False, "Value must contain digits only"

    return True, None


def validate_length(value, expected_length):
    """
    Value must have exactly expected_length characters.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if len(value) != int(expected_length):
        return (
            False,
            f"Value must contain exactly {expected_length} characters"
        )

    return True, None


def validate_min_length(value, minimum):
    """
    Value must have at least minimum characters.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if len(value) < int(minimum):
        return (
            False,
            f"Value must contain at least {minimum} characters"
        )

    return True, None


def validate_max_length(value, maximum):
    """
    Value must not exceed maximum characters.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if len(value) > int(maximum):
        return (
            False,
            f"Value must not exceed {maximum} characters"
        )

    return True, None


# ============================================================
# NUMERIC VALIDATION
# ============================================================

def validate_numeric(value):
    """
    Value must be numeric.

    Supports:
        100
        100.50
        -100
        -100.50
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    try:
        float(value)
        return True, None

    except (ValueError, TypeError):
        return False, "Value must be numeric"


def validate_non_negative(value):
    """
    Numeric value cannot be negative.
    """

    if is_blank(value):
        return True, None

    try:
        number = float(clean_value(value))

        if number < 0:
            return False, "Value cannot be negative"

        return True, None

    except (ValueError, TypeError):
        return False, "Value must be numeric"


def validate_percentage(value):
    """
    Percentage should be between 0 and 100.
    """

    if is_blank(value):
        return True, None

    try:
        number = float(clean_value(value))

        if number < 0 or number > 100:
            return False, "Percentage must be between 0 and 100"

        return True, None

    except (ValueError, TypeError):
        return False, "Percentage must be numeric"


# ============================================================
# MOBILE / PHONE
# ============================================================

def validate_phone(value):
    """
    Validate Indian-style 10 digit mobile/phone number.

    Currently:
        - exactly 10 digits
        - first digit must be 6-9
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if not value.isdigit():
        return False, "Phone number must contain digits only"

    if len(value) != 10:
        return False, "Phone number must contain exactly 10 digits"

    if value[0] not in "6789":
        return False, "Phone number must start with 6, 7, 8 or 9"

    return True, None


# ============================================================
# PINCODE
# ============================================================

def validate_pincode(value):
    """
    Indian PIN code validation.

    Exactly 6 digits.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    if not value.isdigit():
        return False, "Pincode must contain digits only"

    if len(value) != 6:
        return False, "Pincode must contain exactly 6 digits"

    if value[0] == "0":
        return False, "Pincode cannot start with 0"

    return True, None


# ============================================================
# PAN
# ============================================================

def validate_pan(value):
    """
    PAN format:

    AAAAA9999A

    Example:
        ABCDE1234F
    """

    if is_blank(value):
        return True, None

    value = clean_value(value).upper()

    pattern = r"^[A-Z]{5}[0-9]{4}[A-Z]$"

    if not re.fullmatch(pattern, value):
        return False, "Invalid PAN format"

    return True, None


# ============================================================
# EMAIL
# ============================================================

def validate_email(value):
    """
    Basic email format validation.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    pattern = (
        r"^[A-Za-z0-9._%+-]+@"
        r"[A-Za-z0-9.-]+\."
        r"[A-Za-z]{2,}$"
    )

    if not re.fullmatch(pattern, value):
        return False, "Invalid email format"

    return True, None


# ============================================================
# DATE
# ============================================================

def validate_date(value):
    """
    Validate whether a value can be interpreted as a date.

    This intentionally accepts different source date formats
    because CAMS and KFIN can provide dates differently.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    try:
        parsed = pd.to_datetime(
            value,
            errors="raise",
            dayfirst=False
        )

        if pd.isna(parsed):
            return False, "Invalid date"

        return True, None

    except Exception:
        return False, "Invalid date"


# ============================================================
# DATE NOT IN FUTURE
# ============================================================

def validate_not_future(value):
    """
    Date cannot be greater than today's date.
    """

    if is_blank(value):
        return True, None

    try:
        parsed = pd.to_datetime(
            value,
            errors="raise"
        )

        if parsed.date() > date.today():
            return False, "Date cannot be in the future"

        return True, None

    except Exception:
        return False, "Invalid date"


# ============================================================
# ALLOWED VALUES
# ============================================================

def validate_allowed_values(value, allowed_values):
    """
    Value must exist in the configured allowed values.
    """

    if is_blank(value):
        return True, None

    value = clean_value(value)

    allowed = [
        str(item).strip()
        for item in allowed_values
    ]

    if value not in allowed:
        return (
            False,
            f"Value must be one of: {', '.join(allowed)}"
        )

    return True, None


# ============================================================
# RULE EXECUTOR
# ============================================================

def apply_rule(value, rule):
    """
    Execute one validation rule.

    Examples:

        required

        digits

        length:
            10

        allowed_values:
            - A
            - B
    """

    # --------------------------------------------------------
    # Simple string rules
    # --------------------------------------------------------

    if isinstance(rule, str):

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

        return False, f"Unknown validation rule: {rule}"

    # --------------------------------------------------------
    # Dictionary rules
    # --------------------------------------------------------

    if isinstance(rule, dict):

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

        return False, f"Unknown validation configuration: {rule}"

    return False, f"Invalid validation rule: {rule}"


# ============================================================
# GET VALIDATION CONFIGURATION
# ============================================================

def get_validation_rules(file_type, master_type):
    """
    Get validation rules from column_mapping.yaml.

    Example:

        get_validation_rules("R9", "investor_master")
    """

    try:

        config = COLUMN_MAPPING[
            master_type
        ][
            file_type
        ]

        return config.get("validation", {})

    except KeyError:

        raise ValueError(
            f"No configuration found for "
            f"{master_type} / {file_type}"
        )


# ============================================================
# VALIDATE ONE ROW
# ============================================================

def validate_row(
    row,
    validation_rules,
    row_number
):
    """
    Validate one dataframe row.

    Returns a list of errors.
    """

    errors = []

    for column, rules in validation_rules.items():

        # ----------------------------------------------------
        # Column does not exist
        # ----------------------------------------------------

        if column not in row.index:

            errors.append({
                "row_number": row_number,
                "column_name": column,
                "value": None,
                "error_message": "Column not found"
            })

            continue

        value = row[column]

        # ----------------------------------------------------
        # Apply all rules for this column
        # ----------------------------------------------------

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
                    "error_message": error_message
                })

    return errors


# ============================================================
# VALIDATE DATAFRAME
# ============================================================

def validate_data(
    df,
    file_type,
    master_type
):
    """
    Validate the complete dataframe.

    Returns:

        valid_df
        error_df

    Valid rows can later be inserted into Bronze.

    Invalid rows/errors can later be inserted into
    the database error table.
    """

    validation_rules = get_validation_rules(
        file_type,
        master_type
    )

    # --------------------------------------------------------
    # No validation rules
    # --------------------------------------------------------

    if not validation_rules:

        return df.copy(), pd.DataFrame()

    all_errors = []

    # --------------------------------------------------------
    # Validate every row
    # --------------------------------------------------------

    for index, row in df.iterrows():

        row_number = index + 1

        row_errors = validate_row(
            row=row,
            validation_rules=validation_rules,
            row_number=row_number
        )

        all_errors.extend(row_errors)

    # --------------------------------------------------------
    # No errors
    # --------------------------------------------------------

    if not all_errors:

        return df.copy(), pd.DataFrame()

    # --------------------------------------------------------
    # Error dataframe
    # --------------------------------------------------------

    error_df = pd.DataFrame(all_errors)

    # --------------------------------------------------------
    # Identify rows that contain errors
    # --------------------------------------------------------

    invalid_row_numbers = set(
        error_df["row_number"].tolist()
    )

    valid_mask = [
        (index + 1) not in invalid_row_numbers
        for index in range(len(df))
    ]

    valid_df = df.loc[
        valid_mask
    ].copy()

    # --------------------------------------------------------
    # Invalid rows are NOT sent to Bronze
    #
    # Their detailed errors are in error_df.
    # --------------------------------------------------------

    return valid_df, error_df


# ============================================================
# CONVENIENCE FUNCTION
# ============================================================

def process_validation(
    df,
    file_type,
    master_type
):
    """
    Main entry point.

    Example:

        valid_df, error_df = process_validation(
            df,
            "R9",
            "investor_master"
        )
    """

    return validate_data(
        df=df,
        file_type=file_type,
        master_type=master_type
    )