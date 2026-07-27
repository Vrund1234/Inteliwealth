from sqlalchemy import text
from utils.db import engine


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

        for schema, table in tables:

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