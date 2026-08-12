"""Reference-data loaders for the scheme matching engine."""

import uuid

import pandas as pd
from sqlalchemy import text


def load_amc_map(master_engine):
    """RTA AMC code -> AMFI AMC code.

    amfi_amc_code is derived from amc_code by validating it against the AMFI
    master. Codes absent from amfi_scheme_master get NULL, so they can never
    produce a match — which is correct since those AMCs have no AMFI schemes.
    """
    return pd.read_sql(
        """
        SELECT
            r.rta,
            r.amc_code AS rta_amc_code,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM public.amfi_scheme_master a
                    WHERE a.amc_code = r.amc_code
                )
                THEN r.amc_code
                ELSE NULL
            END AS amfi_amc_code,
            r.amc_slug
        FROM public.rta_amc_code r
        WHERE r.is_deleted IS NOT TRUE
        """,
        master_engine,
        dtype=str,
    )


def load_overrides(engine):
    """(rta, rta_scheme_code) -> amfi_scheme_code, where None means NOT_IN_AMFI."""
    df = pd.read_sql(
        """
        SELECT rta, rta_scheme_code, amfi_scheme_code
        FROM bronze.scheme_mapping_override
        WHERE is_active IS TRUE
        """,
        engine,
    )
    return {
        (r.rta, r.rta_scheme_code): (
            None if pd.isna(r.amfi_scheme_code) else str(r.amfi_scheme_code)
        )
        for r in df.itertuples()
    }


def write_audit(engine, audit_rows):
    """Replace the audit table with this run's full evaluation history."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE bronze.scheme_mapping_audit"))
        if not audit_rows:
            return
        for row in audit_rows:
            row.setdefault("audit_id", str(uuid.uuid4()))
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_audit
                    (audit_id, rta, rta_scheme_code, rule_name,
                     execution_outcome, confidence_score, candidate_scheme_id)
                VALUES
                    (:audit_id, :rta, :rta_scheme_code, :rule_name,
                     :execution_outcome, :confidence_score, :candidate_scheme_id)
                """
            ),
            audit_rows,
        )


def write_review(engine, review_rows):
    """Replace pending review candidates, preserving rows already decided."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM bronze.scheme_mapping_review "
                "WHERE reviewer_decision IS NULL"
            )
        )
        if not review_rows:
            return
        for row in review_rows:
            row.setdefault("review_id", str(uuid.uuid4()))
        conn.execute(
            text(
                """
                INSERT INTO bronze.scheme_mapping_review
                    (review_id, rta, rta_scheme_code, rta_scheme_name,
                     candidate_rank, candidate_amfi_code, candidate_amfi_name,
                     candidate_score, rule_name)
                VALUES
                    (:review_id, :rta, :rta_scheme_code, :rta_scheme_name,
                     :candidate_rank, :candidate_amfi_code, :candidate_amfi_name,
                     :candidate_score, :rule_name)
                ON CONFLICT (rta, rta_scheme_code, candidate_rank) DO NOTHING
                """
            ),
            review_rows,
        )
