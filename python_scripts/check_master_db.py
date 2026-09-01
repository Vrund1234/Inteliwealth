"""
Connectivity check for the MASTER database (intelli-wealth-backend's own DB).

Standalone on purpose: it reads MASTER_POSTGRES_* straight from .env rather
than importing utils.db, so it still runs when the PROJECT database config is
missing or broken -- the exact situation you are usually in when you reach for
this script.

Exit code 0 = reachable, 1 = not reachable (safe to use in a cron/CI guard).

    python check_master_db.py
"""

import os
import sys
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

MASTER_HOST = os.getenv("MASTER_POSTGRES_HOST", "localhost")
MASTER_PORT = os.getenv("MASTER_POSTGRES_PORT", "5432")
MASTER_USER = os.getenv("MASTER_POSTGRES_USER", "postgres")
MASTER_PASSWORD = os.getenv("MASTER_POSTGRES_PASSWORD", "")
MASTER_DATABASE = os.getenv("MASTER_POSTGRES_DB")


print("=" * 80)
print("MASTER DATABASE CONNECTIVITY CHECK")
print("=" * 80)
print(f"Host     : {MASTER_HOST}:{MASTER_PORT}")
print(f"Database : {MASTER_DATABASE}")
print(f"User     : {MASTER_USER}")
print(f"Password : {'(set)' if MASTER_PASSWORD else '(EMPTY)'}")
print("=" * 80)


if not MASTER_DATABASE:
    print("\nFAIL  MASTER_POSTGRES_DB is not set in .env")
    sys.exit(1)


# ============================================================
# CONNECT
# ============================================================

engine = create_engine(
    (
        f"postgresql+psycopg2://"
        f"{quote_plus(MASTER_USER)}:"
        f"{quote_plus(MASTER_PASSWORD)}@"
        f"{MASTER_HOST}:{MASTER_PORT}/"
        f"{MASTER_DATABASE}"
    ),
    pool_pre_ping=True,
    # Fail fast instead of hanging on a firewalled host -- the default has no
    # timeout at all, which turns an unreachable server into a stuck cron job.
    connect_args={"connect_timeout": 10},
)


try:

    with engine.connect() as conn:

        # ----------------------------------------------------
        # Identity / version
        # ----------------------------------------------------

        row = conn.execute(
            text("""
                SELECT current_database(),
                       current_user,
                       version(),
                       pg_size_pretty(
                           pg_database_size(current_database())
                       )
            """)
        ).one()

        print(f"\nOK    Connected")
        print(f"      Database : {row[0]}")
        print(f"      User     : {row[1]}")
        print(f"      Version  : {row[2].split(',')[0]}")
        print(f"      Size     : {row[3]}")


        # ----------------------------------------------------
        # Read privilege -- connecting proves nothing about
        # whether the role can actually SELECT anything.
        # ----------------------------------------------------

        tables = conn.execute(
            text("""
                SELECT n.nspname, count(*)
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname NOT LIKE 'pg_%'
                  AND n.nspname <> 'information_schema'
                GROUP BY 1
                ORDER BY 1
            """)
        ).fetchall()

        if tables:
            print(f"\n      Tables by schema:")
            for schema, count in tables:
                print(f"        {schema:<20} {count}")
        else:
            print(f"\n      WARNING: no tables visible to this role")

except Exception as exc:

    print(f"\nFAIL  {type(exc).__name__}")
    print(f"      {exc}")
    sys.exit(1)


print("\n" + "=" * 80)
print("MASTER DATABASE IS ACCESSIBLE")
print("=" * 80)
sys.exit(0)
