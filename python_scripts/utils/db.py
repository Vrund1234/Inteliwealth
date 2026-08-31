import os
from urllib.parse import quote_plus

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


# ============================================================
# PROJECT DATABASE
# ============================================================

HOST = os.getenv("DB_HOST", "localhost")
PORT = os.getenv("DB_PORT", "5432")
USER = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD", "")

PROJECT_DATABASE = os.getenv("DB_NAME")


# ============================================================
# MASTER DATABASE
# ============================================================

MASTER_HOST = os.getenv("MASTER_POSTGRES_HOST", "localhost")
MASTER_PORT = os.getenv("MASTER_POSTGRES_PORT", "5432")
MASTER_USER = os.getenv("MASTER_POSTGRES_USER", "postgres")
MASTER_PASSWORD = os.getenv("MASTER_POSTGRES_PASSWORD", "")

MASTER_DATABASE = os.getenv("MASTER_POSTGRES_DB")


# ============================================================
# VALIDATE DATABASE CONFIGURATION
# ============================================================

if not PROJECT_DATABASE:
    raise RuntimeError(
        "DB_NAME is not set in .env. "
        "Expected: DB_NAME=intelliwealth_trial"
    )

if not MASTER_DATABASE:
    raise RuntimeError(
        "MASTER_POSTGRES_DB is not set in .env. "
        "Expected: MASTER_POSTGRES_DB=latest_dump"
    )


print("=" * 80)
print("DATABASE CONFIGURATION")
print("=" * 80)
print(f"Project Database : {PROJECT_DATABASE}")
print(f"Master Database  : {MASTER_DATABASE}")
print(f"Project Host     : {HOST}:{PORT}")
print(f"Master Host      : {MASTER_HOST}:{MASTER_PORT}")
print("=" * 80)


# ============================================================
# PROJECT DATABASE ENGINE
# ============================================================

engine = create_engine(
    (
        f"postgresql+psycopg2://"
        f"{quote_plus(USER)}:"
        f"{quote_plus(PASSWORD)}@"
        f"{HOST}:{PORT}/"
        f"{PROJECT_DATABASE}"
    ),
    pool_pre_ping=True
)


# ============================================================
# MASTER DATABASE ENGINE
# ============================================================

master_engine = create_engine(
    (
        f"postgresql+psycopg2://"
        f"{quote_plus(MASTER_USER)}:"
        f"{quote_plus(MASTER_PASSWORD)}@"
        f"{MASTER_HOST}:{MASTER_PORT}/"
        f"{MASTER_DATABASE}"
    ),
    pool_pre_ping=True
)


# ============================================================
# READ TABLE
# ============================================================

def read_table(schema, table, limit=100):

    try:

        # ----------------------------------------------------
        # Get available columns
        # ----------------------------------------------------

        column_query = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema
              AND table_name = :table
        """

        columns = pd.read_sql(
            text(column_query),
            engine,
            params={
                "schema": schema,
                "table": table
            }
        )["column_name"].tolist()


        # ----------------------------------------------------
        # Dynamic ordering
        # ----------------------------------------------------

        if "created_at" in columns:

            order_by = 'ORDER BY "created_at" DESC'

        elif "updated_at" in columns:

            order_by = 'ORDER BY "updated_at" DESC'

        elif "last_synced_at" in columns:

            order_by = 'ORDER BY "last_synced_at" DESC'

        else:

            order_by = ""


        # ----------------------------------------------------
        # Read table
        # ----------------------------------------------------

        query = f"""
            SELECT *
            FROM "{schema}"."{table}"
            {order_by}
            LIMIT {int(limit)}
        """

        df = pd.read_sql(
            text(query),
            engine
        )


        # ----------------------------------------------------
        # Datetime formatting
        # ----------------------------------------------------

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

                # Convert timezone-aware timestamps
                # to Asia/Kolkata
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

        # ----------------------------------------------------
        # Date formatting (YYYY-MM-DD)
        # ----------------------------------------------------

        date_cols = [
            "dob", "report_date", "rep_date", "folio_date", "trade_date", "traddate",
            "post_date", "postdate", "purdate", "chqdate", "sys_regn_d", "sys_regn_date",
            "reg_date", "from_date", "to_date", "cease_date", "pause_from_date",
            "pause_to_date", "nav_date", "balance_date", "start_date", "end_date",
            "registered_date", "ceased_date", "next_due_date", "txn_date",
            "first_txn_date", "last_txn_date", "nominee_dob", "jh1_dob",
            "jh2_dob", "guardian_dob", "traddate_clean", "crdate", "cr_date",
            "nct_change_date", "agent_code_change_request_date", "ticob_posted_date",
            "ca_initiated_date", "lastupdateddate"
        ]

        for col in date_cols:
            if col in df.columns:
                dt_series = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
                formatted = dt_series.dt.strftime("%Y-%m-%d")
                df[col] = formatted.where(dt_series.notna(), None)

        # ----------------------------------------------------
        # Convert UUID objects to strings for PyArrow/Streamlit
        # ----------------------------------------------------
        import uuid
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].apply(
                    lambda x: str(x) if isinstance(x, uuid.UUID) else x
                )

        return df


    except Exception as e:

        print(
            f"Error reading {schema}.{table}"
        )

        print(e)

        return pd.DataFrame()


# ============================================================
# UPSERT DATAFRAME
# ============================================================

def upsert_dataframe(
    df,
    schema,
    table,
    conflict_columns=None,
    conflict_constraint=None,
    conflict_index_expr=None,
    engine=engine,
    chunksize=500,
    updated_at_column="updated_at",
):

    """
    Upsert a DataFrame into schema.table using:

        INSERT ... ON CONFLICT ... DO UPDATE

    Exactly one of the following must be supplied:

        conflict_columns
        conflict_constraint
        conflict_index_expr

    created_at is never updated on conflict.

    updated_at_column, when supplied, is updated to now().
    """

    # --------------------------------------------------------
    # Empty dataframe
    # --------------------------------------------------------

    if df.empty:
        return 0


    # --------------------------------------------------------
    # Validate conflict target
    # --------------------------------------------------------

    targets_given = sum(
        bool(x)
        for x in (
            conflict_columns,
            conflict_constraint,
            conflict_index_expr
        )
    )

    if targets_given != 1:

        raise ValueError(
            "upsert_dataframe: pass exactly one of "
            "conflict_columns, conflict_constraint, "
            "or conflict_index_expr"
        )


    columns = list(df.columns)


    # ========================================================
    # GET ACTUAL POSTGRES COLUMN TYPES
    # ========================================================

    with engine.begin() as conn:

        result = conn.execute(
            text("""
                SELECT
                    a.attname,
                    format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c
                    ON a.attrelid = c.oid
                JOIN pg_namespace n
                    ON c.relnamespace = n.oid
                WHERE n.nspname = :schema
                  AND c.relname = :table
                  AND a.attnum > 0
                  AND NOT a.attisdropped
            """),
            {
                "schema": schema,
                "table": table
            }
        )

        col_types = dict(result.fetchall())


    # --------------------------------------------------------
    # Make sure all DataFrame columns exist in DB
    # --------------------------------------------------------

    missing_types = [
        c for c in columns
        if c not in col_types
    ]

    if missing_types:

        raise ValueError(
            f"upsert_dataframe: {schema}.{table} "
            f"has no column(s) {missing_types!r} "
            "to infer a cast type from"
        )


    # ========================================================
    # UPDATE COLUMNS
    # ========================================================

    conflict_set = (
        set(conflict_columns)
        if conflict_columns
        else set()
    )

    update_columns = [
        c
        for c in columns
        if c not in conflict_set
        and c != "created_at"
        and (
            updated_at_column is None
            or c != updated_at_column
        )
    ]


    set_parts = [
        f'"{c}" = EXCLUDED."{c}"'
        for c in update_columns
    ]


    if updated_at_column is not None:

        set_parts.append(
            f'"{updated_at_column}" = now()'
        )


    set_clause = ", ".join(set_parts)


    # ========================================================
    # CONFLICT CLAUSE
    # ========================================================

    if conflict_columns:

        target_cols = ", ".join(
            f'"{c}"'
            for c in conflict_columns
        )

        conflict_clause = (
            f"ON CONFLICT ({target_cols})"
        )

        partition_by_expr = target_cols


    elif conflict_constraint:

        conflict_clause = (
            f"ON CONFLICT ON CONSTRAINT "
            f"{conflict_constraint}"
        )


        with engine.begin() as conn:

            constraint_cols = [
                row[0]
                for row in conn.execute(
                    text("""
                        SELECT kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name =
                             kcu.constraint_name
                         AND tc.table_schema =
                             kcu.table_schema
                        WHERE tc.table_schema = :schema
                          AND tc.table_name = :table
                          AND tc.constraint_name = :name
                        ORDER BY kcu.ordinal_position
                    """),
                    {
                        "schema": schema,
                        "table": table,
                        "name": conflict_constraint
                    }
                )
            ]


        if not constraint_cols:

            raise ValueError(
                "upsert_dataframe: could not resolve "
                f"columns for constraint "
                f"{conflict_constraint!r} on "
                f"{schema}.{table}"
            )


        partition_by_expr = ", ".join(
            f'"{c}"'
            for c in constraint_cols
        )


    else:

        conflict_clause = (
            f"ON CONFLICT ({conflict_index_expr})"
        )

        partition_by_expr = conflict_index_expr


    # ========================================================
    # SQL COLUMN LISTS
    # ========================================================

    col_list = ", ".join(
        f'"{c}"'
        for c in columns
    )


    batch_col_list = (
        ", ".join(
            f'"{c}"'
            for c in columns
        )
        + ', "__upsert_seq"'
    )


    typed_col_list = (
        ", ".join(
            f'"{c}"::{col_types[c]}'
            for c in columns
        )
        + ', "__upsert_seq"'
    )


    # ========================================================
    # UPSERT SQL
    # ========================================================

    sql = f"""
        INSERT INTO "{schema}"."{table}"
        ({col_list})

        SELECT {col_list}

        FROM (
            SELECT
                {col_list},

                ROW_NUMBER() OVER (
                    PARTITION BY {partition_by_expr}
                    ORDER BY "__upsert_seq" DESC
                ) AS "__upsert_rn"

            FROM (
                SELECT {typed_col_list}

                FROM (
                    VALUES %s
                ) AS "__upsert_raw"
                ({batch_col_list})

            ) AS "__upsert_batch"

        ) AS "__upsert_deduped"

        WHERE "__upsert_rn" = 1

        {conflict_clause}

        DO UPDATE SET
            {set_clause}
    """


    # ========================================================
    # CLEAN DATAFRAME VALUES
    # ========================================================

    records = (
        df
        .astype(object)
        .where(pd.notnull(df), None)
        .to_dict(orient="records")
    )


    rows = [
        tuple(
            record[c]
            for c in columns
        )
        for record in records
    ]


    # ========================================================
    # INSERT IN CHUNKS
    # ========================================================

    total = 0


    with engine.begin() as conn:

        cursor = conn.connection.cursor()


        for i in range(
            0,
            len(rows),
            chunksize
        ):

            batch = rows[
                i:i + chunksize
            ]


            batch_with_seq = [
                row + (seq,)
                for seq, row in enumerate(batch)
            ]


            execute_values(
                cursor,
                sql,
                batch_with_seq,
                page_size=len(batch_with_seq)
            )


            total += len(batch)


    return total