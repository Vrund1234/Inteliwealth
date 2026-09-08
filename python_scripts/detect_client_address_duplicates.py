"""Queue same-address pairs that exact matching cannot merge.

gold.client_address carries a UNIQUE index over (client_id, address_key), so
two rows that normalise to the same string can no longer coexist. What the
index cannot reach is a word-level difference -- one RTA truncating or
abbreviating text another spells out:

    "...RETI BUNDER"      vs  "...RETI BUNDER ROAD"
    "...SANSKRUT BUNGL"   vs  "...SANSKRUT BUNGLOWS"

These are the same address and a person can see it, but no exact rule proves
it. Putting a fuzzy rule behind the unique index would eventually merge two
addresses that genuinely differ, which is worse than the duplicates it
removes -- so they queue in gold.client_address_review instead.

gold.client_address_review PUBLISHES the detection; it does not record what
anyone decided. The reviewers are app users -- organization, distributor,
relationship manager -- and the app's gold connection is read-only by
construction (app/modules/gold_sync/gold_db.py), so a decision column here
would be one nothing could fill. The decision lives in the app's own
client_address_review table, where organization_id, users.id and RBAC exist.

That means this script re-detects the same pairs on every run. ON CONFLICT
DO NOTHING makes a repeat a no-op and the table stays at its natural size,
so a stable queue size across runs is the expected reading, not a stuck one.

Usage:
    venv/bin/python detect_client_address_duplicates.py --dry-run
    venv/bin/python detect_client_address_duplicates.py
"""

import argparse
import traceback

from sqlalchemy import text

from utils.db import engine


# Trigram similarity above which two addresses are worth a person's attention.
# Measured on the 686 addresses present 2026-09-07: 0.60 -> 42 pairs, 0.75 ->
# 28, 0.85 -> 18, 0.90 -> 2. Almost every real pair sits in 0.75-0.89, and
# below 0.70 genuinely different flats in one building start appearing.
SIMILARITY_THRESHOLD = 0.75


# Two rules, deliberately unioned. Trigram similarity misses a short address
# whose extension is long ("KAPAD BAZAR DHINOJ" vs "kapad bazar Dhinoj
# Chanasma" scores 0.62), while the prefix rule catches it exactly. A strict
# prefix is also far stronger evidence than a score -- one string literally
# extends the other -- so match_type is recorded and the two are reviewed with
# different confidence.
DETECT_SQL = text(
    """
    WITH live AS (
        SELECT DISTINCT client_id, address_key
        FROM gold.client_address
        WHERE is_deleted = false
          AND address_key <> ''
    ),
    pairs AS (
        SELECT
            a.client_id,
            a.address_key AS key_a,
            b.address_key AS key_b,
            similarity(a.address_key, b.address_key) AS sim,
            (b.address_key LIKE a.address_key || '%%') AS is_prefix
        FROM live a
        JOIN live b
          ON b.client_id = a.client_id
         -- a < b keeps one row per pair and, with the CHECK on the table,
         -- makes the stored pair independent of which side matched first.
         AND a.address_key < b.address_key
        WHERE similarity(a.address_key, b.address_key) > :threshold
           OR b.address_key LIKE a.address_key || '%%'
    )
    INSERT INTO gold.client_address_review
        (client_id, address_key_a, address_key_b, similarity, match_type)
    SELECT
        client_id, key_a, key_b, ROUND(sim::numeric, 3),
        CASE WHEN is_prefix THEN 'PREFIX' ELSE 'FUZZY' END
    FROM pairs
    ON CONFLICT (client_id, address_key_a, address_key_b) DO NOTHING
    """
)


PREVIEW_SQL = text(
    """
    WITH live AS (
        SELECT DISTINCT client_id, address_key
        FROM gold.client_address
        WHERE is_deleted = false AND address_key <> ''
    ),
    pairs AS (
        SELECT a.client_id, a.address_key AS key_a, b.address_key AS key_b,
               similarity(a.address_key, b.address_key) AS sim,
               (b.address_key LIKE a.address_key || '%%') AS is_prefix
        FROM live a
        JOIN live b ON b.client_id = a.client_id AND a.address_key < b.address_key
        WHERE similarity(a.address_key, b.address_key) > :threshold
           OR b.address_key LIKE a.address_key || '%%'
    )
    SELECT
        COUNT(*) AS detected,
        COUNT(*) FILTER (WHERE is_prefix) AS prefix_pairs,
        COUNT(*) FILTER (WHERE NOT is_prefix) AS fuzzy_pairs,
        COUNT(*) FILTER (WHERE r.review_id IS NOT NULL) AS already_queued
    FROM pairs p
    LEFT JOIN gold.client_address_review r
           ON r.client_id = p.client_id
          AND r.address_key_a = p.key_a
          AND r.address_key_b = p.key_b
    """
)


QUEUE_SIZE_SQL = text(
    "SELECT COUNT(*) FROM gold.client_address_review"
)


def detect(dry_run=False, threshold=SIMILARITY_THRESHOLD):

    print("=" * 80)
    print("DETECTING SIMILAR CLIENT ADDRESSES")
    print("=" * 80)

    try:

        with engine.begin() as connection:

            preview = connection.execute(
                PREVIEW_SQL, {"threshold": threshold}
            ).mappings().one()

            print("Threshold           :", threshold)
            print("Pairs detected      :", preview["detected"])
            print("  strict prefix     :", preview["prefix_pairs"])
            print("  fuzzy only        :", preview["fuzzy_pairs"])
            print("Already queued      :", preview["already_queued"])

            newly_queued = 0

            if dry_run:

                print("\nDry run: nothing written.")

            else:

                result = connection.execute(
                    DETECT_SQL, {"threshold": threshold}
                )

                newly_queued = result.rowcount or 0

                print("Newly queued        :", newly_queued)

            queued = connection.execute(QUEUE_SIZE_SQL).scalar() or 0

            print("Queue size          :", queued)

        return {
            "detected": preview["detected"],
            "prefix_pairs": preview["prefix_pairs"],
            "fuzzy_pairs": preview["fuzzy_pairs"],
            "newly_queued": newly_queued,
            "queued": queued,
        }

    except Exception as e:

        print("CLIENT ADDRESS DETECTION FAILED")

        print(e)

        traceback.print_exc(limit=5)

        return None


def main(argv=None):

    parser = argparse.ArgumentParser(
        prog="detect_client_address_duplicates"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be queued without writing anything."
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=SIMILARITY_THRESHOLD,
        help=f"Trigram similarity cutoff (default {SIMILARITY_THRESHOLD})."
    )

    args = parser.parse_args(argv)

    return detect(dry_run=args.dry_run, threshold=args.threshold)


if __name__ == "__main__":

    main()
