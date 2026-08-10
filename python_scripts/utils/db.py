import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import pandas as pd

load_dotenv()


def _env(var):
    value = os.getenv(var)
    if not value:
        raise RuntimeError(
            f"{var} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def _create_engine(prefix):
    """Build an engine from <PREFIX>_DB_HOST / _PORT / _USER / _PASSWORD / _NAME."""

    user = _env(f"{prefix}_DB_USER")
    password = _env(f"{prefix}_DB_PASSWORD")
    host = _env(f"{prefix}_DB_HOST")
    port = _env(f"{prefix}_DB_PORT")
    name = _env(f"{prefix}_DB_NAME")

    return create_engine(
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}",
        pool_pre_ping=True
    )


# Project Database: raw warehouse - bronze / silver / gold schemas
engine = _create_engine("PROJECT")

# Master Database: backend application DB - read for public.scheme_master
master_engine = _create_engine("MASTER")


# Safely quote SQL identifiers (schema / table / column names)
quote = engine.dialect.identifier_preparer.quote


def read_table(schema, table, limit=100):
    """Preview the newest rows of a table. Returns an empty frame on any failure."""

    try:

        # Resolve columns - also validates that schema.table actually exists
        columns = pd.read_sql(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = :table
            """),
            engine,
            params={"schema": schema, "table": table}
        )["column_name"].tolist()


        if not columns:

            print(f"No such table: {schema}.{table}")

            return pd.DataFrame()


        # Dynamic ordering
        for candidate in ("created_at", "updated_at", "last_synced_at"):

            if candidate in columns:

                order_by = f"ORDER BY {quote(candidate)} DESC"

                break

        else:

            order_by = ""


        # Identifiers cannot be bound parameters - quote them instead.
        # Existence was confirmed above, so this cannot reach an arbitrary table.
        query = text(f"""
            SELECT *
            FROM {quote(schema)}.{quote(table)}
            {order_by}
            LIMIT :limit
        """)


        df = pd.read_sql(
            query,
            engine,
            params={"limit": int(limit)}
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
