from sqlalchemy import text
from utils.db import engine

from mapping_wbr import WBR_REPORTS


def create_triggers():

    with engine.begin() as conn:

        # Create/Replace function
        conn.execute(text("""
        CREATE OR REPLACE FUNCTION bronze.update_updated_at()
        RETURNS TRIGGER AS
        $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """))

    tables = [
        ("bronze", "transaction_master_new"),
        ("bronze", "investor_master"),
        ("bronze", "sip_master_new"),
        ("silver", "transaction_master_new"),
        ("silver", "investor_master"),
        ("silver", "sip_master_new")
    ]

    # WBR report tables come from the registry, so a
    # report added to mapping_wbr.WBR_REPORTS gets its
    # triggers with no change here.

    for spec in WBR_REPORTS.values():

        tables.append(("bronze", spec["table"]))

        tables.append(("silver", spec["table"]))

    # One transaction per table, so a table that has not
    # been created yet is skipped instead of aborting the
    # triggers for every other table.

    for schema, table in tables:

        exists = False

        with engine.connect() as conn:

            exists = bool(
                conn.execute(
                    text("""
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = :schema
                          AND table_name = :table
                    """),
                    {
                        "schema": schema,
                        "table": table
                    }
                ).scalar()
            )

        if not exists:

            print(
                f"Trigger skipped: {schema}.{table} "
                "does not exist"
            )

            continue

        with engine.begin() as conn:

            conn.execute(text(f"""
                DROP TRIGGER IF EXISTS trg_{table}_updated_at
                ON {schema}.{table};
            """))

            conn.execute(text(f"""
                CREATE TRIGGER trg_{table}_updated_at
                BEFORE UPDATE
                ON {schema}.{table}
                FOR EACH ROW
                EXECUTE FUNCTION bronze.update_updated_at();
            """))
