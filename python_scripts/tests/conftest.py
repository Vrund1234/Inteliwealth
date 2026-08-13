import sys
from pathlib import Path

import pandas as pd
import pytest

PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG_ROOT))

from utils.db import engine  # noqa: E402

BASELINE_CSV = Path(__file__).parent / "baseline_mappings.csv"


@pytest.fixture(scope="session")
def baseline_df():
    """The 223 mappings that existed before Phase 2. These must never change."""
    return pd.read_csv(BASELINE_CSV, dtype=str)


@pytest.fixture(scope="session")
def current_df():
    return current_mappings()


def current_mappings():
    """Live mappings from bronze.scheme_mapping, same shape as the baseline."""
    return pd.read_sql(
        """
        SELECT rta, rta_scheme_code, amfi_scheme_code
        FROM bronze.scheme_mapping
        WHERE amfi_scheme_code IS NOT NULL
        ORDER BY rta, rta_scheme_code
        """,
        engine,
        dtype=str,
    )
