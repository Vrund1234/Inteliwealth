"""The identity of a bank account, stable across RTA feed formatting.

Companion to utils/address_key.py, and the same contract: this is the single
definition, and gold.client_bank.account_key is a GENERATED column computing
the identical string in SQL so the database enforces what the ETL intends.

WHAT IS IN THE KEY
==================

The account number alone, stripped of every non-alphanumeric character,
upper-cased, and with LEADING ZEROS REMOVED. Leading zeros are the whole
reason this function exists -- measured on the 726 rows present 2026-09-07,
they are the only cause of duplication in gold.client_bank:

    ACUP2222222   00691060000052     vs  0691060000052
    ACUP2222222   0491010000421      vs  00491010000421
    ACUP2222222   00491060001464     vs  0491060001464
    ACUP2222222   30409457813        vs  00000030409457813

Four rows, 726 -> 722. Small next to client_address's 41%, but the same
defect: one account survives once per RTA spelling.

Banks pad account numbers to a fixed width and each RTA pads differently, so
the zeros carry no information. A number that is ALL zeros normalises to ''
and is treated as absent rather than as a shared key -- there are none today,
but a placeholder account must never merge two clients' rows.

WHAT IS DELIBERATELY EXCLUDED
=============================

    ifsc          22 of 726 blank, and it demonstrably ARRIVES LATER: the
                  ACUPS2047M pair above is the same account with the IFSC
                  present on one row and missing on the other. A key holding
                  a field that arrives later inserts a duplicate instead of
                  matching the row already there -- the same trap pincode set
                  for addresses.

    bank_branch   150 of 726 blank.
    micr          NULL on all 726 rows.

    bank_name     case and suffix variants for one bank -- "HDFC Bank Ltd"
                  vs "HDFC BANK LTD" on rows that are otherwise the same
                  account.

    account_type  un-normalised passthrough: "CA" and "Current" both appear
                  for current accounts, "NRE"/"NRO"/"SB" elsewhere.

    bank_city     "AHMEDABAD" vs "Ahmedabad" on the same account.

All of them are enriched by load_client_bank instead, so a later feed's IFSC
fills a blank rather than creating a row.

WHY THE ACCOUNT NUMBER IS ENOUGH ON ITS OWN
===========================================

It is the bank's own identifier and it is never blank here (0 of 726 rows).
Scoped to one client_id -- which is how the UNIQUE index is built -- it needs
no corroboration from bank_name or IFSC. Adding either only re-splits rows
that differ by spelling: keying on account+IFSC gives 723 rather than 722,
and the extra row is the ACUPS2047M pair whose IFSC is simply missing.
"""

import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

# The SQL that gold.client_bank.account_key is GENERATED from. Kept beside the
# Python so the two are read together; test_account_key.py asserts parity
# against a live database when one is reachable.
ACCOUNT_KEY_SQL = (
    "COALESCE(NULLIF(LTRIM(UPPER(REGEXP_REPLACE("
    "COALESCE(account_number,''),'[^A-Za-z0-9]','','g')),'0'),''),'')"
)


def account_key(account_number):
    """The normalised identity of one bank account. Never returns None.

    NULL/NaN counts as empty, matching COALESCE(...,'') in the SQL.
    """
    if account_number is None or not isinstance(account_number, str):
        try:
            import pandas as pd

            if account_number is None or pd.isna(account_number):
                account_number = ""
            else:
                account_number = str(account_number)
        except (ImportError, ValueError, TypeError):
            account_number = "" if account_number is None else str(account_number)

    return _NON_ALNUM.sub("", account_number).upper().lstrip("0")


def account_key_series(series):
    """Vectorised account_key over a Series."""
    return (
        series.fillna("")
        .astype(str)
        .str.replace(_NON_ALNUM, "", regex=True)
        .str.upper()
        .str.lstrip("0")
    )
