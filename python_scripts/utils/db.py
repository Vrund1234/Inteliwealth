"""
utils/db.py
===========
Central database engine factory for the IntelliWealth ETL pipeline.

Engines
-------
engine        — PROJECT_DB_* vars → raw warehouse (bronze / silver / gold schemas)
master_engine — MASTER_DB_* vars → backend application DB (public.scheme_master)

All credentials are sourced exclusively from environment variables via
config/settings.py.  See python_scripts/.env.example for the full list of
required variable names.

restore_engine()
----------------
Factory that creates and returns a *fresh* SQLAlchemy engine for
PROJECT_DB_NAME.  Use it to recover from a stale/closed connection pool
without restarting the process:

    from utils.db import engine, restore_engine
    engine = restore_engine()   # reassign module-level name if needed
"""

import urllib.parse

import pandas as pd
from sqlalchemy import create_engine

# All credentials come from env vars — raises RuntimeError on first import
# if any variable is missing, naming every missing var in the error message.
from config.settings import (
    MASTER_DB_HOST,
    MASTER_DB_NAME,
    MASTER_DB_PASSWORD,
    MASTER_DB_PORT,
    MASTER_DB_USER,
    PROJECT_DB_HOST,
    PROJECT_DB_NAME,
    PROJECT_DB_PASSWORD,
    PROJECT_DB_PORT,
    PROJECT_DB_USER,
)

# URL-encode passwords so special characters (e.g. @, #, %) don't break the
# connection string.
_project_pw = urllib.parse.quote_plus(PROJECT_DB_PASSWORD)
_master_pw = urllib.parse.quote_plus(MASTER_DB_PASSWORD)

# ---------------------------------------------------------------------------
# Project Database engine  — raw warehouse (bronze / silver / gold schemas)
# ---------------------------------------------------------------------------
engine = create_engine(
    f"postgresql+psycopg2://{PROJECT_DB_USER}:{_project_pw}"
    f"@{PROJECT_DB_HOST}:{PROJECT_DB_PORT}/{PROJECT_DB_NAME}",
    pool_pre_ping=True,
)

# ---------------------------------------------------------------------------
# Master Database engine  — backend app DB, read for public.scheme_master
# pool_pre_ping=True added to match engine's connection hygiene.
# ---------------------------------------------------------------------------
master_engine = create_engine(
    f"postgresql+psycopg2://{MASTER_DB_USER}:{_master_pw}"
    f"@{MASTER_DB_HOST}:{MASTER_DB_PORT}/{MASTER_DB_NAME}",
    pool_pre_ping=True,
)


# ---------------------------------------------------------------------------
# restore_engine
# ---------------------------------------------------------------------------
def restore_engine():
    """
    Create and return a brand-new SQLAlchemy engine for PROJECT_DB_NAME.

    Call this when the module-level ``engine`` has become stale or its
    connection pool needs to be reset (e.g. after a database restart or a
    long-running process relinquishes idle connections).

    Returns
    -------
    sqlalchemy.engine.Engine
        A fresh engine with ``pool_pre_ping=True``.

    Example
    -------
    ::

        from utils.db import restore_engine
        engine = restore_engine()
    """
    return create_engine(
        f"postgresql+psycopg2://{PROJECT_DB_USER}:{_project_pw}"
        f"@{PROJECT_DB_HOST}:{PROJECT_DB_PORT}/{PROJECT_DB_NAME}",
        pool_pre_ping=True,
    )


# ---------------------------------------------------------------------------
# Utility helpers (logic unchanged from original)
# ---------------------------------------------------------------------------

def read_table(schema, table, limit=100):

    try:

        # check available columns
        column_query = f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='{schema}'
            AND table_name='{table}'
        """

        columns = pd.read_sql(
            column_query,
            engine
        )["column_name"].tolist()


        # Dynamic ordering
        if "created_at" in columns:

            order_by = "ORDER BY created_at DESC"

        elif "updated_at" in columns:

            order_by = "ORDER BY updated_at DESC"

        elif "last_synced_at" in columns:

            order_by = "ORDER BY last_synced_at DESC"

        else:

            order_by = ""


        query = f"""
            SELECT *
            FROM {schema}.{table}
            {order_by}
            LIMIT {limit}
        """


        df = pd.read_sql(
            query,
            engine
        )


        # datetime formatting
        for col in [
            "created_at",
            "updated_at",
            "last_synced_at"
        ]:

            if col in df.columns:

                df[col] = pd.to_datetime(
                    df[col],
                    errors="coerce"
                )


                if getattr(df[col].dt, "tz", None) is not None:

                    df[col] = (
                        df[col]
                        .dt
                        .tz_convert("Asia/Kolkata")
                    )


                df[col] = (
                    df[col]
                    .dt
                    .strftime("%Y-%m-%d %H:%M:%S")
                )


        return df


    except Exception as e:

        print(
            f"Error reading {schema}.{table}"
        )

        print(e)

        return pd.DataFrame()