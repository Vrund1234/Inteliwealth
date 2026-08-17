"""Apply approved NAV_NAME_MATCH review decisions to bronze.scheme_mapping.

The last step of the flow that starts with map_unmatched_nav_name.py, and the
only one that writes a mapping. Reads rows a human marked APPROVED and applies
them; everything else in bronze.scheme_mapping_review is left untouched.

Two guards define the behaviour:

  * Only reviewer_decision = 'APPROVED' moves. Pending and REJECTED rows stay
    where they are, so re-running is safe and idempotent.
  * A scheme that already carries an amfi_scheme_code is skipped, never
    overwritten. Approvals can sit in the queue for days, and scheme_mapping.py
    runs in that window; if another rule resolved the scheme meanwhile it did
    so at higher confidence (STRUCT_EXACT 98, NAV_MATCH 97, this rule 96), and
    a stale approval must yield to it.

    venv/bin/python promote_approved_mappings.py [--dry-run]
"""

import argparse

import pandas as pd
from sqlalchemy import text

from scheme_matching.scheme_id import derive_scheme_id
from utils.db import engine, master_engine

# Both fallback tiers promote through here. The confidence written is the one
# the reviewer saw on the row, not a constant, so a NAME_KEY_MATCH approval is
# not silently recorded as the stronger NAV-corroborated evidence.
# STRUCT_EXACT is included so the ENGINE's own ambiguity queue is actionable.
# When two AMFI schemes share a key the engine writes both as candidates and
# maps neither; without this, a reviewer's answer sat in the table with no
# effect and the scheme stayed PENDING_REVIEW indefinitely.
RULE_NAMES = ("NAV_NAME_MATCH", "NAME_KEY_MATCH", "NAV_FUZZY_MATCH",
              "STRUCT_EXACT")
CONFIDENCE = 96


def load_approved(conn_engine):
    """Approved rows joined to the mapping row they would change.

    current_amfi_scheme_code comes from the join so the caller can tell an
    applicable approval from a stale one without a second query.
    """
    return pd.read_sql(
        text(
            """
            SELECT r.rta,
                   r.rta_scheme_code,
                   r.candidate_amfi_code,
                   r.candidate_amfi_name,
                   r.rule_name,
                   r.candidate_score,
                   r.reviewed_by,
                   r.reviewed_at,
                   sm.rta_amc_code,
                   sm.amfi_scheme_code AS current_amfi_scheme_code
            FROM bronze.scheme_mapping_review r
            JOIN bronze.scheme_mapping sm
              ON sm.rta = r.rta AND sm.rta_scheme_code = r.rta_scheme_code
            WHERE r.rule_name = ANY(:rules)
              AND r.reviewer_decision = 'APPROVED'
            ORDER BY r.rta, r.rta_scheme_code
            """
        ),
        conn_engine,
        params={"rules": list(RULE_NAMES)},
    )


def load_amfi_amc_codes(conn_engine, amfi_codes):
    """amfi_scheme_code -> amc_code, for building scheme_id.

    Only amfi_scheme_master carries an AMC code; schemes resolved out of
    scheme_master have none, and those fall back to the RTA's own AMC code so
    scheme_id still matches what scheme_mapping.py would have written.
    """
    if not amfi_codes:
        return {}
    df = pd.read_sql(
        text(
            """
            SELECT amfi_scheme_code::text AS code, amc_code
            FROM public.amfi_scheme_master
            WHERE amfi_scheme_code = ANY(:codes)
            """
        ),
        conn_engine,
        params={"codes": [str(c) for c in amfi_codes]},
    )
    return dict(zip(df["code"], df["amc_code"]))


def promote_approved(conn_engine, dry_run=False):
    """Apply every applicable approval. Returns a summary dict."""
    approved = load_approved(conn_engine)
    if approved.empty:
        return {"approved": 0, "promoted": 0, "skipped_already_mapped": 0,
                "skipped_ambiguous": 0, "applied": []}

    # Approving several candidates for one scheme is not a decision, it is the
    # ambiguity restated. Promoting any of them would let whichever row is
    # applied last silently win, so the scheme is left for a real answer.
    per_scheme = approved.groupby(["rta", "rta_scheme_code"])["candidate_amfi_code"]
    contested = {
        key for key, distinct in per_scheme.nunique().items() if distinct > 1
    }
    ambiguous_mask = approved.apply(
        lambda row: (row["rta"], row["rta_scheme_code"]) in contested, axis=1
    )

    already_mapped = approved["current_amfi_scheme_code"].notna()
    applicable = approved[~already_mapped & ~ambiguous_mask]

    amc_by_code = load_amfi_amc_codes(
        master_engine, applicable["candidate_amfi_code"].dropna().unique().tolist()
    )

    def _null_safe(value):
        """pandas NaT/NaN -> None.

        reviewed_at is nullable, and psycopg2 renders NaT as the literal string
        'NaT', which a timestamp column rejects. The column only becomes
        datetime64 (and so only yields NaT rather than None) once at least one
        row carries a real timestamp, so this stays invisible until the first
        genuine approval sits alongside one that was approved without a date.
        """
        return None if value is None or pd.isna(value) else value

    updates, applied = [], []
    for row in applicable.itertuples():
        amfi_code = str(row.candidate_amfi_code)
        amc_code = amc_by_code.get(amfi_code) or row.rta_amc_code
        updates.append({
            "rta": row.rta,
            "rta_scheme_code": row.rta_scheme_code,
            "amfi_scheme_code": amfi_code,
            "scheme_id": derive_scheme_id(amc_code, amfi_code),
            "confidence": int(row.candidate_score or CONFIDENCE),
            "source": row.rule_name,
            "verified_by": _null_safe(row.reviewed_by),
            "verified_at": _null_safe(row.reviewed_at),
        })
        applied.append((row.rta, row.rta_scheme_code, amfi_code,
                        row.candidate_amfi_name))

    if updates and not dry_run:
        with conn_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE bronze.scheme_mapping
                       SET amfi_scheme_code   = :amfi_scheme_code,
                           scheme_id          = :scheme_id,
                           mapping_source     = :source,
                           mapping_confidence = :confidence,
                           mapping_status     = 'MATCHED',
                           verified_by        = :verified_by,
                           verified_at        = :verified_at,
                           updated_at         = CURRENT_TIMESTAMP
                     WHERE rta = :rta
                       AND rta_scheme_code = :rta_scheme_code
                       AND amfi_scheme_code IS NULL
                    """
                ),
                updates,
            )

    return {
        "approved": len(approved),
        "promoted": len(updates),
        "skipped_already_mapped": int(already_mapped.sum()),
        "skipped_ambiguous": len(contested),
        "applied": applied,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    print("=" * 80)
    print("PROMOTE APPROVED FALLBACK MAPPINGS"
          + ("  [DRY RUN]" if args.dry_run else ""))
    print("=" * 80)

    result = promote_approved(engine, dry_run=args.dry_run)

    for rta, code, amfi, name in result["applied"]:
        verb = "would map" if args.dry_run else "mapped"
        print(f"  {verb}  {rta}/{code:9s} -> {amfi:>7s}  {str(name)[:50]}")

    print("-" * 80)
    print(f"Approved rows found      : {result['approved']}")
    print(f"Promoted                 : {result['promoted']}")
    print(f"Skipped (already mapped) : {result['skipped_already_mapped']}")
    if result["skipped_ambiguous"]:
        print(f"Skipped (several candidates approved for one scheme) : "
              f"{result['skipped_ambiguous']}")
    print("=" * 80)
    if args.dry_run:
        print("Dry run: bronze.scheme_mapping was NOT modified.")


if __name__ == "__main__":
    main()
