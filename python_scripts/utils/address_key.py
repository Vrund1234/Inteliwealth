"""The identity of a physical address, stable across RTA feed formatting.

Two rows of gold.client_address are the same address when this key matches.
It is the single definition: etl_gold_client_address.py dedups on it, and
gold.client_address.address_key is a GENERATED column computing the identical
string in SQL, so the database can enforce what the ETL intends.

WHAT IS IN THE KEY, AND WHY NOTHING ELSE IS
===========================================

Only line1+line2+line3, stripped of every non-alphanumeric character and
upper-cased. Measured against the 1186 rows present on 2026-09-07:

  mobile_no        contact data, not address identity. The same house arrives
                   as "+919824013413" from CAMS and "9824013413" from KFIN.
                   Including it took the table from 705 to 1061 distinct rows.

  state, country   inconsistently filled per feed. PAN AGZPP1978M had four
                   byte-identical rows differing only in whether `country`
                   said "India" or was blank.

  city             garbled between feeds, and adds nothing the lines do not
                   already carry: "USA" vs "USA, USA", "TAIPEI" vs
                   "TAIPEI, TW", "Ahmadabad" vs "AHMEDABAD". Every group it
                   split apart was one address.

  area, whatsapp   never populated (0 of 1186 rows).
  address_type     one distinct value ('CURRENT').

  pincode          THE IMPORTANT ONE. A key may only hold fields that are
                   always present and never enriched, and pincode fails that:
                   20 rows carry it blank or all-zeros, and a later feed
                   supplying it would change the key and insert a duplicate
                   rather than conflict with the row already there. It is an
                   enriched field instead -- load_client_address COALESCEs it
                   onto the existing row. Both merges it would have blocked
                   were verified to be the same address with the pincode
                   simply missing on one side.

The three lines are concatenated with NO separator on purpose. Different RTAs
split the same text across address1/2/3 at different points, so a separator
makes a line-boundary shift look like a different address:

    "9 KAIRVI BUNGLOWS" | "NEAR SARTHI HOTEL" | "BODAKDEV AHMEDABAD"
    "9 KAIRVI BUNGLOWS    NEAR SARTHI HOTEL" | "BODAKDEV AHMEDABAD"

Keeping separators left 696 distinct rows; dropping them leaves 686.

WHAT THIS KEY DOES NOT CATCH
============================

Word-level differences, because exact matching cannot see them:

    "...RETI BUNDER"      vs  "...RETI BUNDER ROAD"
    "...SANSKRUT BUNGL"   vs  "...SANSKRUT BUNGLOWS"

24 such pairs remain. They go to gold.client_address_review for a human --
never to this function, and never behind the unique index. A fuzzy rule will
eventually merge two addresses that genuinely differ, and a constraint that
does that is worse than the duplicates it removes.
"""

import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

# The SQL that gold.client_address.address_key is GENERATED from. Kept here so
# the two definitions are read together; test_address_key.py asserts parity
# against a live database when one is reachable.
ADDRESS_KEY_SQL = (
    "UPPER(REGEXP_REPLACE("
    "COALESCE(line1,'')||COALESCE(line2,'')||COALESCE(line3,''),"
    "'[^A-Za-z0-9]','','g'))"
)


def address_key(line1, line2, line3):
    """The normalised identity of one address. Never returns None.

    NULL/NaN lines count as empty, matching COALESCE(...,'') in the SQL.
    """
    parts = []
    for value in (line1, line2, line3):
        # pandas gives NaN/NA for a missing cell; both are != themselves or
        # not str, and str(NaN) would put the literal "nan" into the key.
        if value is None or not isinstance(value, str):
            try:
                import pandas as pd

                if value is None or pd.isna(value):
                    value = ""
                else:
                    value = str(value)
            except (ImportError, ValueError, TypeError):
                value = "" if value is None else str(value)
        parts.append(value)
    return _NON_ALNUM.sub("", "".join(parts)).upper()


def address_key_series(df, line1="line1", line2="line2", line3="line3"):
    """Vectorised address_key over a DataFrame. Column names are overridable
    because the ETL calls this before renaming address1/2/3 to line1/2/3.
    """
    joined = (
        df[line1].fillna("").astype(str)
        + df[line2].fillna("").astype(str)
        + df[line3].fillna("").astype(str)
    )
    return joined.str.replace(_NON_ALNUM, "", regex=True).str.upper()
