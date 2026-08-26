import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values
import pandas as pd

load_dotenv()

HOST = os.getenv("DB_HOST")
PORT = os.getenv("DB_PORT")
USER = os.getenv("DB_USER")
PASSWORD = os.getenv("DB_PASSWORD")

MasterHOST = os.getenv("MASTER_POSTGRES_HOST")
MasterPORT = os.getenv("MASTER_POSTGRES_PORT")
MasterUSER = os.getenv("MASTER_POSTGRES_USER")
MasterPASSWORD = os.getenv("MASTER_POSTGRES_PASSWORD")

PROJECT_DATABASE = os.getenv("DB_NAME")
MASTER_DATABASE = os.getenv("MASTER_POSTGRES_DB")

# NOTE: credentials are URL-encoded (quote_plus) before being interpolated
# into the DSN — an unescaped "@", ":" or "/" in a password otherwise breaks
# URL parsing (e.g. a password like "Test@123" gets misread as a host split).
# Project Database
engine = create_engine(
    f"postgresql+psycopg2://{quote_plus(USER)}:{quote_plus(PASSWORD)}@{HOST}:{PORT}/{PROJECT_DATABASE}",
    pool_pre_ping=True
)

# Master Database
master_engine = create_engine(
    f"postgresql+psycopg2://{quote_plus(MasterUSER)}:{quote_plus(MasterPASSWORD)}@{MasterHOST}:{MasterPORT}/{MASTER_DATABASE}"
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

    Returns the number of rows upserted.
    """
    if df.empty:
        return 0

    targets_given = sum(bool(x) for x in (conflict_columns, conflict_constraint, conflict_index_expr))
    if targets_given != 1:
        raise ValueError(
            "upsert_dataframe: pass exactly one of conflict_columns, "
            "conflict_constraint, or conflict_index_expr"
        )

    columns = list(df.columns)

    # Real column types, for casting the raw VALUES literals below. A bare
    # `INSERT INTO t (...) VALUES (...)` lets Postgres infer each literal's
    # type straight from the target column -- but once VALUES is wrapped in
    # a subquery (needed for the same-batch dedup below), that direct link
    # is gone and an all-NULL or ambiguous column silently infers as `text`,
    # which then fails to satisfy the outer INSERT's real column type
    # ("column ... is of type integer but expression is of type text").
    # Casting explicitly here, once per call, avoids depending on inference
    # at all.
    with engine.begin() as conn:
        # dict(result) -- not dict(result.fetchall()) -- would silently take
        # the wrong path: Result exposes .keys() (column names), so plain
        # dict() treats it as a mapping and tries result["attname"] instead
        # of iterating (name, type) row pairs, raising "'CursorResult'
        # object is not subscriptable".
        col_types = dict(conn.execute(text("""
            SELECT a.attname, format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = :schema AND c.relname = :table
              AND a.attnum > 0 AND NOT a.attisdropped
        """), {"schema": schema, "table": table}).fetchall())
    missing_types = [c for c in columns if c not in col_types]
    if missing_types:
        raise ValueError(
            f"upsert_dataframe: {schema}.{table} has no column(s) {missing_types!r} "
            "to infer a cast type from"
        )

    conflict_set = set(conflict_columns) if conflict_columns else set()
    update_columns = [
        c for c in columns
        if c not in conflict_set
        and c != "created_at"
        and (updated_at_column is None or c != updated_at_column)
    ]

    set_parts = [f'"{c}" = EXCLUDED."{c}"' for c in update_columns]
    if updated_at_column is not None:
        set_parts.append(f'"{updated_at_column}" = now()')
    set_clause = ", ".join(set_parts)

    if conflict_columns:
        target_cols = ", ".join(f'"{c}"' for c in conflict_columns)
        conflict_clause = f"ON CONFLICT ({target_cols})"
        partition_by_expr = target_cols
    elif conflict_constraint:
        conflict_clause = f"ON CONFLICT ON CONSTRAINT {conflict_constraint}"
        with engine.begin() as conn:
            constraint_cols = [
                row[0] for row in conn.execute(text("""
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.table_schema = :schema
                      AND tc.table_name = :table
                      AND tc.constraint_name = :name
                    ORDER BY kcu.ordinal_position
                """), {"schema": schema, "table": table, "name": conflict_constraint})
            ]
        if not constraint_cols:
            raise ValueError(
                f"upsert_dataframe: could not resolve columns for constraint "
                f"{conflict_constraint!r} on {schema}.{table}"
            )
        partition_by_expr = ", ".join(f'"{c}"' for c in constraint_cols)
    else:
        conflict_clause = f"ON CONFLICT ({conflict_index_expr})"
        # Same expression as the ON CONFLICT target itself -- see the
        # pre-filter note below for why this must match exactly.
        partition_by_expr = conflict_index_expr

    col_list = ", ".join(f'"{c}"' for c in columns)
    batch_col_list = ", ".join(f'"{c}"' for c in columns) + ', "__upsert_seq"'
    typed_col_list = (
        ", ".join(f'"{c}"::{col_types[c]}' for c in columns) + ', "__upsert_seq"'
    )

    # VALUES %s (not a per-column :placeholder list) -- psycopg2.extras.
    # execute_values() rewrites that single %s into "(v1,v2,...),(v1,v2,...)"
    # for a whole chunk and sends it as ONE statement. Passing named
    # placeholders through SQLAlchemy's Connection.execute(sql, list_of_dicts)
    # instead (the previous implementation) compiles to the DBAPI's plain
    # cursor.executemany(), which for psycopg2 issues one round trip PER ROW,
    # not per chunk -- measured at ~2,070 rows/sec against silver.
    # transaction_master_new (~62s for ~129k rows) versus ~65,000 rows/sec
    # once batched (see docs/superpowers/plans/2026-08-26-bronze-dedup-performance.md
    # for the comparably-shaped bronze-side fix).
    #
    # PRE-FILTER TO ONE ROW PER CONFLICT KEY: a single multi-row INSERT ...
    # ON CONFLICT DO UPDATE is not allowed to touch the same conflict target
    # twice ("ON CONFLICT DO UPDATE command cannot affect row a second
    # time") -- unlike the old row-by-row executemany(), where each row was
    # its own statement and a same-key repeat simply updated what the
    # previous statement had just inserted. Two incoming rows sharing a
    # conflict key but differing elsewhere (e.g. a bronze resend with a
    # corrected status column, still in the same batch) hit this live the
    # first time this shipped. ROW_NUMBER() here keeps only the LAST row
    # per conflict key (by original batch order, via the synthetic
    # __upsert_seq column) before the INSERT ever reaches ON CONFLICT --
    # same "last one wins" result the old sequential updates produced.
    sql = f"""
        INSERT INTO {schema}.{table} ({col_list})
        SELECT {col_list}
        FROM (
            SELECT {col_list},
                   ROW_NUMBER() OVER (
                       PARTITION BY {partition_by_expr}
                       ORDER BY "__upsert_seq" DESC
                   ) AS "__upsert_rn"
            FROM (
                SELECT {typed_col_list}
                FROM (VALUES %s) AS "__upsert_raw" ({batch_col_list})
            ) AS "__upsert_batch"
        ) AS "__upsert_deduped"
        WHERE "__upsert_rn" = 1
        {conflict_clause}
        DO UPDATE SET {set_clause}
    """

    # astype(object) FIRST: assigning None into a still-datetime64-dtype
    # column doesn't actually store None -- pandas silently coerces it right
    # back to NaT (a pandas gotcha, not a psycopg2 one), so a literal NaT
    # reaches psycopg2 as an unparseable timestamp literal ("invalid input
    # syntax for type timestamp: 'NaT'"). Confirmed live against
    # gold.folio_nominees' dob column during Task 8's functional test.
    # Casting to object first makes the column able to actually hold None.
    records = df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    rows = [tuple(record[c] for c in columns) for record in records]

    total = 0
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
            execute_values(cursor, sql, batch_with_seq, page_size=len(batch_with_seq))
            total += len(batch)

    return total