import uuid

from sqlalchemy import text


def upsert_dataframe(engine, df, schema, table, conflict_target_sql,
                      update_set_sql, insert_columns=None):
    """
    Loads `df` into a session-scoped TEMP TABLE (ON COMMIT DROP, so it never
    leaks past this transaction), then runs one
        INSERT ... SELECT ... ON CONFLICT <conflict_target_sql> <action>
    from the temp table into `schema.table`. Returns rows affected.

    `conflict_target_sql`: literal SQL fragment, e.g. "(amc_code)" for a plain/
    formal unique constraint, or the full two-part expression list for a
    nullable-safe expression index (see add_constraints.sql for the exact
    expressions each gold table's index uses).
    `update_set_sql`: literal `SET` fragment (e.g. "name = EXCLUDED.name, ...")
    or None for `DO NOTHING`.
    `insert_columns`: explicit column order; defaults to df.columns.
    """
    if df.empty:
        return 0

    columns = insert_columns or list(df.columns)
    staging_table = f"_stg_{table}_{uuid.uuid4().hex[:12]}"
    col_list = ", ".join(f'"{c}"' for c in columns)

    with engine.begin() as conn:
        conn.execute(
            text(
                f'CREATE TEMP TABLE "{staging_table}" '
                f'(LIKE {schema}.{table} INCLUDING DEFAULTS) ON COMMIT DROP'
            )
        )

        df[columns].to_sql(
            staging_table, con=conn, if_exists="append",
            index=False, method="multi", chunksize=1000,
        )

        action = f"DO UPDATE SET {update_set_sql}" if update_set_sql else "DO NOTHING"
        result = conn.execute(
            text(
                f"""
                INSERT INTO {schema}.{table} ({col_list})
                SELECT {col_list} FROM "{staging_table}"
                ON CONFLICT {conflict_target_sql}
                {action}
                """
            )
        )
        return result.rowcount
