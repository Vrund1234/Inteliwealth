import os
from contextlib import contextmanager
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
                dt_series = pd.to_datetime(df[col], errors="coerce", format="ISO8601")
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


# Set by collect_upserts() to a list, and left as None otherwise. When it is
# a list, every upsert_dataframe() call appends its own count breakdown to it.
# This is how gold_loader attributes an insert/update split to one gold entity
# without any of the eight gold modules having to return it -- their
# signatures and return values (True/False) stay exactly as they are.
_upsert_collector = None


@contextmanager
def collect_upserts():
    """Capture the count breakdown of every upsert_dataframe() call in the block.

    Yields a list that each call appends
    {"schema", "table", "attempted", "affected", "inserted", "updated"} to.

    Nesting is supported and the previous collector is always restored on the
    way out, including on an exception -- an armed collector left behind would
    accumulate for the lifetime of the Streamlit process.
    """
    global _upsert_collector
    previous = _upsert_collector
    collected = []
    _upsert_collector = collected
    try:
        yield collected
    finally:
        _upsert_collector = previous


def _record_upsert(schema, table, attempted, affected, inserted):
    result = {
        "attempted": attempted,
        "affected": affected,
        "inserted": inserted,
        "updated": affected - inserted,
    }
    if _upsert_collector is not None:
        _upsert_collector.append({"schema": schema, "table": table, **result})
    return result


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
    Upsert a DataFrame into schema.table via INSERT ... ON CONFLICT ... DO UPDATE.

    Replaces the repo-wide df.to_sql(if_exists="append") pattern, which has
    no protection against re-inserting a row that already exists under the
    table's natural key -- the root cause of the gold.transactions /
    gold.sip duplication found on 2026-08-24/25.

    Pass exactly one of:
      conflict_columns:    list[str] -- for a unique constraint on plain
                            columns, e.g. ["rta", "rta_txn_no", "folio_number"].
      conflict_constraint: str -- a table CONSTRAINT's name, for a unique
                            constraint built on plain columns that you'd
                            rather target by name than by repeating its
                            column list.
      conflict_index_expr: str -- the literal ON CONFLICT (...) target,
                            REQUIRED when the unique index includes an
                            expression (e.g. the COALESCE-normalized SIP
                            reg-no key) rather than only plain columns.
                            Postgres cannot promote an expression-based
                            index to a table CONSTRAINT at all ("Cannot
                            create a primary key or unique constraint using
                            such an index" -- hit live on
                            uq_silver_sip_natural_key/uq_gold_sip_natural_key),
                            so conflict_constraint can never name one; the
                            raw expression is the only way to target it.
                            Pass exactly what appears inside the index's own
                            parentheses, e.g.
                            '"rta", "folio_number", (COALESCE(NULLIF(sip_reg_no, \\'\\'), \\'\\'))'.

    DO UPDATE always wins over DO NOTHING here on purpose: DO NOTHING would
    silently drop a legitimate correction (e.g. a transaction's trxnstat
    changing on a resend with the same natural key).

    Every column present in `df` is refreshed from EXCLUDED on conflict,
    except the conflict columns themselves (conflict_constraint/
    conflict_index_expr don't name specific df columns to exclude, so every
    df column is refreshed in those two modes -- harmless, since a column
    that's part of the conflict target has the same value in EXCLUDED as
    it already does in the row being matched) and `created_at`, which is
    always excluded from the SET clause so a row's first-seen timestamp
    survives any number of re-processing runs -- every gold/silver loader
    sets `created_at` to "now" before calling this function, and several
    extraction functions (e.g. `get_last_gold_timestamp()` in
    etl_gold_transaction.py) read `MAX(created_at)` as an incremental
    watermark, so refreshing it on conflict would silently corrupt that
    watermark on every re-processed row.

    `updated_at_column`: if the target table actually has this column, it is
    always set to now() on conflict, whether or not it's a column in `df`.
    Pass `updated_at_column=None` for a table that has no such column at all
    -- e.g. every gold table in this project except `gold.holdings` (which
    uses `last_synced_at` instead: pass `updated_at_column="last_synced_at"`
    there). Getting this wrong raises `psycopg2.errors.UndefinedColumn`
    the first time a real conflict occurs (a fresh INSERT with no existing
    conflicting row never touches the SET clause, so the bug stays dormant
    until re-processing a natural key that's already in the table -- hit
    live on gold.holdings during Task 8's functional test).

    Returns {"attempted", "affected", "inserted", "updated"}:

      attempted -- rows sent to Postgres, i.e. the sum of the chunk lengths.
                   This is what this function used to return on its own, kept
                   so the previous number stays available and the change is
                   auditable. It is an UPPER BOUND: the in-chunk
                   ROW_NUMBER() ... WHERE __upsert_rn = 1 pre-filter below
                   collapses same-key rows that `attempted` still counts.
      affected  -- rows the statement actually inserted or updated.
      inserted  -- of those, the ones that did not already exist.
      updated   -- of those, the ones that took the ON CONFLICT DO UPDATE path.

    The split comes from RETURNING (xmax = 0): Postgres sets xmax to 0 on a
    freshly inserted tuple and to the locking/updating xid on one reached
    through the conflict path. xmax can also be non-zero for a tuple locked by
    a CONCURRENT transaction, so the reading is exact only when this is the
    sole writer -- the etl_pipeline runner holds a session advisory lock for
    its whole run to make that true. A simultaneous Streamlit session can skew
    it; that is accepted, because these numbers are observability, not control
    flow.
    """
    if df.empty:
        return _record_upsert(schema, table, 0, 0, 0)

    targets_given = sum(bool(x) for x in (conflict_columns, conflict_constraint, conflict_index_expr))
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
        DO UPDATE SET {set_clause}
        RETURNING (xmax = 0) AS "inserted"
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
    inserted = 0
    affected = 0
    with engine.begin() as conn:

        cursor = conn.connection.cursor()
        for i in range(0, len(rows), chunksize):
            batch = rows[i:i + chunksize]
            # __upsert_seq is the row's position within THIS chunk -- dedup
            # only ever needs to be resolved within one INSERT statement, so
            # it doesn't need to be unique across chunks, just increasing
            # within one.
            batch_with_seq = [row + (seq,) for seq, row in enumerate(batch)]
            # page_size=len(batch): execute_values' own default page_size
            # (100) would silently re-fragment an already-chunked batch back
            # into multiple round trips -- one execute_values() call per
            # chunk is the whole point.
            # fetch=True returns the RETURNING rows for the WHOLE chunk in one
            # list, so the split accumulates across chunk boundaries.
            returned = execute_values(
                cursor, sql, batch_with_seq, page_size=len(batch_with_seq), fetch=True
            )
            total += len(batch)
            affected += len(returned)
            inserted += sum(1 for row in returned if row[0])

    return _record_upsert(schema, table, total, affected, inserted)