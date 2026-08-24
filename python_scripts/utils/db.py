import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
import pandas as pd

load_dotenv()

HOST = os.getenv("DB_HOST")
PORT = os.getenv("DB_PORT")
USER = os.getenv("DB_USER")
PASSWORD = os.getenv("DB_PASSWORD")

PROJECT_DATABASE = os.getenv("PROJECT_DATABASE")
MASTER_DATABASE = os.getenv("MASTER_DATABASE")

# Project Database
engine = create_engine(
    f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{PROJECT_DATABASE}",
    pool_pre_ping=True
)

# Master Database
master_engine = create_engine(
    f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{MASTER_DATABASE}"
)

# Restore Database
#restore_engine = create_engine(
#    f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{RESTORE_DATABASE}"
#)


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