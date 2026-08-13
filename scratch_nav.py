import sys
import os
sys.path.append('/var/www/html/intelliwealth-layers/python_scripts')
import pandas as pd
from utils.db import engine, restore_engine

def test_nav():
    print("Testing NAV logic...")
    query = "SELECT MIN(nav_date) as min_date, MAX(nav_date) as max_date, COUNT(*) as cnt FROM public.nav_master"
    df = pd.read_sql(query, restore_engine())
    print(df)
    
    q2 = """
        SELECT
            s.rta,
            s.scheme_code AS rta_scheme_code,
            sn.nav_date,
            sn.nav
        FROM gold.scheme_nav sn
        JOIN gold.scheme s
            ON sn.scheme_id = s.id
        WHERE sn.nav_date IS NOT NULL
          AND sn.nav IS NOT NULL
        LIMIT 10;
    """
    df2 = pd.read_sql(q2, restore_engine())
    print(df2)

if __name__ == "__main__":
    test_nav()
