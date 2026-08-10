import pandas as pd
import numpy as np





def normalize_key(name):
    """Normalize a single column name / mapping alias.

    Mapping aliases are written in source spelling ("Scheme Name", "CKYC NO"),
    but incoming columns arrive as "Scheme Name" too - both sides must run
    through this or the alias never matches and the column loads NULL.
    """
    return (
        str(name)
        .strip()
        .strip("'")
        .strip('"')
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("#", "")
    )


def clean_columns(df):
    """Normalize a DataFrame's column names, dropping duplicate columns.

    Union of the four copies this replaced: None-safe, non-mutating,
    strips wrapping quotes/whitespace in either order, and keeps the first
    of any columns that collide once normalized.
    """
    if df is None:
        return df

    df = df.copy()

    df.columns = [normalize_key(c) for c in df.columns]

    # Keep first duplicate column if any
    return df.loc[:, ~df.columns.duplicated(keep="first")]


# CAMS writes M/D/YYYY (with a time suffix), KFIN writes D/M/YYYY.
DAYFIRST_BY_SOURCE = {"CAMS": False, "KFIN": True}


def format_dates(df, date_columns, source=None):
    """Parse date_columns to date objects using the RTA's known field order.

    `source` must be "CAMS" or "KFIN" for raw RTA data. Omit it only for rows
    read back out of the database, which are already ISO.

    Never let pandas infer the field order: it picks one format from the first
    non-null value and applies it to the whole column, so a KFIN file starting
    with "05/07/2026" parses every day<=12 value with month and day swapped and
    nulls every day>12 value. format="mixed" parses each value on its own.
    """
    if df is None:
        return df

    df = df.copy()

    dayfirst = DAYFIRST_BY_SOURCE.get(str(source).upper(), False)

    for col in date_columns:

        if col not in df.columns:
            continue

        parsed = pd.to_datetime(
            df[col],
            format="mixed",
            dayfirst=dayfirst,
            errors="coerce"
        ).dt.date

        df[col] = parsed.where(pd.notnull(parsed), None)

    return df
