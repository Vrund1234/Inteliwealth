"""Post-migration state check for the 2026-08-26 bronze row_hash column,
mirroring tests/test_dedup_migration.py's pattern of asserting live schema
state after a migration runs."""

import pandas as pd

from utils.db import engine

TABLES = [
    "transaction_master_new",
    "investor_master",
    "sip_master_new",
]


def test_row_hash_column_exists_on_all_three_bronze_tables():
    columns = pd.read_sql(
        """
        SELECT table_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = ANY(%(tables)s)
          AND column_name = 'row_hash'
        """,
        engine,
        params={"tables": TABLES},
    )
    found = set(columns["table_name"])
    missing = set(TABLES) - found
    assert not missing, f"row_hash column missing on: {missing}"
    assert (columns["data_type"] == "text").all()


def test_row_hash_is_indexed_on_all_three_bronze_tables():
    indexes = pd.read_sql(
        """
        SELECT tablename, indexdef FROM pg_indexes
        WHERE schemaname = 'bronze' AND tablename = ANY(%(tables)s)
          AND indexdef ILIKE '%%row_hash%%'
        """,
        engine,
        params={"tables": TABLES},
    )
    found = set(indexes["tablename"])
    missing = set(TABLES) - found
    assert not missing, f"row_hash index missing on: {missing}"


def test_row_hash_is_not_null_on_all_three_bronze_tables():
    columns = pd.read_sql(
        """
        SELECT table_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'bronze'
          AND table_name = ANY(%(tables)s)
          AND column_name = 'row_hash'
        """,
        engine,
        params={"tables": TABLES},
    )
    still_nullable = columns.loc[columns["is_nullable"] == "YES", "table_name"].tolist()
    assert not still_nullable, f"row_hash still nullable on: {still_nullable}"
