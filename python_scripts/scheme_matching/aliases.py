"""Configurable name substitutions, loaded from public.scheme_name_alias.

Two alias types, deliberately kept apart:

TOKEN       word-level, global. "GR" -> "GROWTH".
FUND_RENAME phrase-level, AMC-scoped. "LONG TERM EQUITY" -> "ELSS TAX SAVER"
            within FTI only. Applying a rename globally would corrupt other AMCs.

Plan, option and frequency terms are NOT aliases. They are parsed attributes
(see scheme_key.extract_attributes) and must never be added to this table.
"""

import re

import pandas as pd


def load_aliases(master_engine):
    df = pd.read_sql(
        """
        SELECT raw_term, normalized_term, alias_type, amc_code
        FROM public.scheme_name_alias
        WHERE is_active IS TRUE
        """,
        master_engine,
    )
    return df.to_dict(orient="records")


def build_alias_fn(alias_rows):
    """Compile alias rows into a callable(text, amc_code) -> text.

    Renames are applied before tokens so a rename's replacement text is itself
    token-normalized. Longer raw_terms are applied first within each group so a
    short alias cannot pre-empt a longer overlapping one.
    """
    tokens = []
    renames = []

    for row in alias_rows:
        raw = str(row["raw_term"]).upper().strip()
        norm = str(row.get("normalized_term") or "").upper().strip()
        amc = row.get("amc_code")
        amc = str(amc).strip() if amc is not None and not pd.isna(amc) else None
        if not raw:
            continue
        pattern = re.compile(r"\b" + re.escape(raw) + r"\b")
        entry = (pattern, norm, amc, len(raw))
        if row["alias_type"] == "FUND_RENAME":
            renames.append(entry)
        else:
            tokens.append(entry)

    renames.sort(key=lambda e: e[3], reverse=True)
    tokens.sort(key=lambda e: e[3], reverse=True)

    def apply(text, amc_code=None):
        out = str(text).upper()
        for pattern, norm, amc, _ in renames:
            if amc is not None and amc != amc_code:
                continue
            out = pattern.sub(norm, out)
        for pattern, norm, amc, _ in tokens:
            if amc is not None and amc != amc_code:
                continue
            out = pattern.sub(norm, out)
        return re.sub(r"\s+", " ", out).strip()

    return apply
