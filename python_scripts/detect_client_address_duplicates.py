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
import os
import traceback

import pandas as pd
from sqlalchemy import text

from utils.address_tokens import (
    MIN_TOKEN_LENGTH,
    RARE_TOKEN_SHARE,
    STOPWORDS,
    classify_pair,
    house_number,
)
from utils.db import engine


# Trigram similarity above which two addresses are worth a person's attention.
# Measured on the 686 addresses present 2026-09-07: 0.60 -> 42 pairs, 0.75 ->
# 28, 0.85 -> 18, 0.90 -> 2. Almost every real pair sits in 0.75-0.89, and
# below 0.70 genuinely different flats in one building start appearing.
SIMILARITY_THRESHOLD = 0.75


# Whether a pass may merge, or may only report. OFF by default and deliberately
# so: detect() runs unattended inside gold_loader every 15 minutes, and merging
# is the one thing it does that a person cannot easily take back. Deploying
# this code must not start merging on the next cron pass -- someone has to
# decide to open the gate.
#
# Open it with CLIENT_ADDRESS_AUTO_MERGE=1 in .env, or --auto-merge for one run.
#
# Shut, the script behaves as it did before merging existed: every pair goes to
# gold.client_address_review and waits for a person. Detection is the half that
# is always safe, so shutting the gate must never also hide the pairs.
AUTO_MERGE_DEFAULT = os.getenv(
    "CLIENT_ADDRESS_AUTO_MERGE", ""
).strip().lower() in {"1", "true", "yes", "on"}


# Two rules, deliberately unioned. Trigram similarity misses a short address
# whose extension is long ("KAPAD BAZAR DHINOJ" vs "kapad bazar Dhinoj
# Chanasma" scores 0.62), while the prefix rule catches it exactly. A strict
# prefix is also far stronger evidence than a score -- one string literally
# extends the other -- so match_type is recorded and the two are reviewed with
# different confidence.

# One row per (client, address_key): gold.client_address is unique on that
# pair already, but selecting the lines explicitly keeps line1 available for
# the house-number comparison decide_pairs makes.
CANDIDATE_SQL = text(
    """
    WITH live AS (
        SELECT
            a.client_id,
            a.address_key,
            a.line1,
            a.line2,
            a.line3,
            -- This row's OWN geography, dropped from its tokens below.
            COALESCE(a.city,'') || ' ' ||
            COALESCE(a.state,'') || ' ' ||
            COALESCE(a.country,'') AS geography,
            NULLIF(
                REGEXP_REPLACE(COALESCE(a.pincode,''), '[^0-9]', '', 'g'), ''
            ) AS pin
        FROM gold.client_address a
        WHERE a.is_deleted = false
          AND a.address_key <> ''
    ),
    tok AS (
        SELECT DISTINCT
            l.client_id,
            l.address_key,
            UPPER(t.token) AS token
        FROM live l
        CROSS JOIN LATERAL regexp_split_to_table(
            COALESCE(l.line1,'') || ' ' ||
            COALESCE(l.line2,'') || ' ' ||
            COALESCE(l.line3,''),
            '[^A-Za-z0-9]+'
        ) AS t(token)
        WHERE LENGTH(t.token) >= :min_token_length
          AND t.token !~ '^[0-9]+$'
          AND NOT (UPPER(t.token) = ANY(:stopwords))
          -- A city cannot tell two addresses of ONE client apart: both carry
          -- it, and the pincodes below must already match. Taken from the row
          -- rather than a list, so every city works, not only listed ones.
          AND UPPER(t.token) NOT IN (
              SELECT UPPER(g)
              FROM regexp_split_to_table(l.geography, '[^A-Za-z0-9]+') AS g
              WHERE g <> ''
          )
    ),
    -- A token carried by many addresses is a locality, not an identity.
    rare AS (
        SELECT token
        FROM tok
        GROUP BY token
        HAVING COUNT(DISTINCT address_key) <= GREATEST(
            1, CEIL(:rare_share * (SELECT COUNT(*) FROM live))
        )
    ),
    -- Same client, same pincode, sharing rare words. This is the only rule
    -- that reaches two feeds naming different landmarks for one house.
    token_pairs AS (
        SELECT
            ta.client_id,
            ta.address_key AS key_a,
            tb.address_key AS key_b,
            COUNT(*) AS shared_rare
        FROM tok ta
        JOIN tok tb
          ON  tb.client_id   = ta.client_id
         AND  ta.address_key < tb.address_key
         AND  tb.token       = ta.token
        JOIN rare r ON r.token = ta.token
        JOIN live la ON la.client_id = ta.client_id AND la.address_key = ta.address_key
        JOIN live lb ON lb.client_id = tb.client_id AND lb.address_key = tb.address_key
        WHERE la.pin IS NOT NULL AND la.pin = lb.pin
        GROUP BY ta.client_id, ta.address_key, tb.address_key
    ),
    -- The two rules that were already here: trigram similarity and one key
    -- literally extending the other.
    string_pairs AS (
        SELECT
            a.client_id,
            a.address_key AS key_a,
            b.address_key AS key_b
        FROM live a
        JOIN live b
          ON  b.client_id   = a.client_id
         AND  a.address_key < b.address_key
        WHERE similarity(a.address_key, b.address_key) > :threshold
           OR b.address_key LIKE a.address_key || '%%'
    ),
    all_pairs AS (
        SELECT client_id, key_a, key_b FROM token_pairs
        UNION
        SELECT client_id, key_a, key_b FROM string_pairs
    )
    SELECT
        p.client_id,
        cl.pan,
        p.key_a,
        p.key_b,
        la.line1 AS line1_a,
        lb.line1 AS line1_b,
        COALESCE(tp.shared_rare, 0) AS shared_rare,
        similarity(p.key_a, p.key_b) AS similarity
    FROM all_pairs p
    JOIN live la ON la.client_id = p.client_id AND la.address_key = p.key_a
    JOIN live lb ON lb.client_id = p.client_id AND lb.address_key = p.key_b
    LEFT JOIN token_pairs tp
           ON  tp.client_id = p.client_id
          AND  tp.key_a     = p.key_a
          AND  tp.key_b     = p.key_b
    LEFT JOIN gold.clients cl ON cl.id = p.client_id
    """
)


# How many silver rows back each spelling. resolve_clusters keeps the one the
# RTAs actually send most often, so the survivor is the spelling most folios
# will keep arriving as.
EVIDENCE_SQL = text(
    """
    SELECT
        cl.id AS client_id,
        UPPER(REGEXP_REPLACE(
            COALESCE(i.address1,'') || COALESCE(i.address2,'') || COALESCE(i.address3,''),
            '[^A-Za-z0-9]', '', 'g'
        )) AS address_key,
        COUNT(*) AS source_rows
    FROM silver.investor_master i
    JOIN gold.clients cl
      ON cl.pan = LEFT(UPPER(TRIM(CAST(i.pan_no AS TEXT))), 10)
    GROUP BY 1, 2
    """
)


ALIAS_INSERT_SQL = text(
    """
    INSERT INTO gold.client_address_alias
        (client_id, address_key_alias, address_key_canonical, pan, decision_source)
    VALUES
        (:client_id, :address_key_alias, :address_key_canonical, :pan, 'AUTO')
    ON CONFLICT (client_id, address_key_alias) DO NOTHING
    """
)


REVIEW_INSERT_SQL = text(
    """
    INSERT INTO gold.client_address_review
        (client_id, address_key_a, address_key_b, similarity, match_type)
    VALUES
        (:client_id, :key_a, :key_b, :similarity, :match_type)
    ON CONFLICT (client_id, address_key_a, address_key_b) DO NOTHING
    """
)



QUEUE_SIZE_SQL = text(
    "SELECT COUNT(*) FROM gold.client_address_review"
)


def decide_pairs(candidates):

    """Split candidate pairs into the ones to merge and the ones to queue.

    Returns the frame with two columns added:

      tier        AUTO   -- merged unattended, an alias row is written
                  REVIEW -- queued for a person, nothing is merged
      match_type  the evidence, strongest first: PREFIX (one key extends the
                  other) > FUZZY (trigram alone agrees) > TOKEN (shared rare
                  words at one house number, which is all that reaches a pair
                  the other two rules cannot see).

    house_number is compared here rather than in SQL so that utils/address_tokens
    stays the single definition of the rule, the way utils/address_key is for
    identity.
    """

    if candidates is None or candidates.empty:

        out = pd.DataFrame(candidates).copy()

        out["tier"] = []
        out["match_type"] = []

        return out

    out = candidates.copy()

    tiers = []
    match_types = []

    for row in out.itertuples():

        house_a = house_number(row.line1_a)
        house_b = house_number(row.line1_b)

        same_house = house_a is not None and house_a == house_b

        tiers.append(
            classify_pair(
                key_a=row.key_a,
                key_b=row.key_b,
                shared_rare=row.shared_rare,
                same_house_number=same_house,
                similarity=row.similarity,
            )
        )

        if row.key_a.startswith(row.key_b) or row.key_b.startswith(row.key_a):

            match_types.append("PREFIX")

        elif row.similarity > SIMILARITY_THRESHOLD:

            match_types.append("FUZZY")

        else:

            match_types.append("TOKEN")

    out["tier"] = tiers
    out["match_type"] = match_types

    return out


def resolve_clusters(pairs, evidence):

    """Turn AUTO pairs into alias rows, one canonical per cluster.

    Pairs connect transitively -- Aashutosh's three spellings are joined only
    through the middle row -- but apply_address_aliases is a single lookup, not
    a repeated one. An alias chain A->B->C would rewrite A to B and leave B
    live, so every alias here names the surviving key directly.

    The survivor is the spelling the RTAs sent most often (`evidence` counts
    silver.investor_master rows per key), because that is the one most folios
    will keep arriving as. Ties break on the key itself so a re-run cannot flip
    the choice and thrash gold.client_address.
    """

    columns = ["client_id", "address_key_alias", "address_key_canonical"]

    if pairs is None or pairs.empty:

        return pd.DataFrame(columns=columns)

    parent = {}

    def find(node):

        while parent.setdefault(node, node) != node:

            parent[node] = parent[parent[node]]

            node = parent[node]

        return node

    def union(a, b):

        root_a, root_b = find(a), find(b)

        if root_a != root_b:

            parent[root_a] = root_b

    for row in pairs.itertuples():

        union((row.client_id, row.key_a), (row.client_id, row.key_b))

    members = {}

    for node in list(parent):

        members.setdefault(find(node), []).append(node)

    rows = []

    for group in members.values():

        # Most-evidenced key wins; the key itself breaks a tie so the outcome
        # does not depend on the order rows came back in.
        canonical = max(
            group,
            key=lambda node: (evidence.get(node, 0), node[1])
        )

        for node in group:

            if node == canonical:

                continue

            rows.append({
                "client_id": node[0],
                "address_key_alias": node[1],
                "address_key_canonical": canonical[1],
            })

    return pd.DataFrame(rows, columns=columns)


def split_by_tier(decided, auto_merge):

    """Which pairs get merged and which get queued, given the gate.

    With the gate shut nothing is merged and everything is queued -- including
    the pairs strong enough to merge, because a pair nobody can see is worse
    than a pair nobody has merged yet.
    """

    if decided is None or decided.empty:

        empty = pd.DataFrame(decided)

        return empty, empty.copy()

    if not auto_merge:

        return decided.iloc[0:0], decided

    return (
        decided[decided["tier"] == "AUTO"],
        decided[decided["tier"] == "REVIEW"],
    )


def detect(dry_run=False, threshold=SIMILARITY_THRESHOLD,
           auto_merge=None):

    print("=" * 80)
    print("DETECTING SIMILAR CLIENT ADDRESSES")
    print("=" * 80)

    aliases_written = 0
    newly_queued = 0

    try:

        with engine.begin() as connection:

            candidates = pd.read_sql(
                CANDIDATE_SQL,
                connection,
                params={
                    "threshold": threshold,
                    "min_token_length": MIN_TOKEN_LENGTH,
                    "rare_share": RARE_TOKEN_SHARE,
                    "stopwords": sorted(STOPWORDS),
                },
            )

            decided = decide_pairs(candidates)

            auto, review = split_by_tier(
                decided,
                AUTO_MERGE_DEFAULT if auto_merge is None else auto_merge,
            )

            evidence_rows = pd.read_sql(EVIDENCE_SQL, connection)

            evidence = {
                (row.client_id, row.address_key): row.source_rows
                for row in evidence_rows.itertuples()
            }

            aliases = resolve_clusters(
                auto[["client_id", "key_a", "key_b"]],
                evidence,
            )

            pan_by_client = dict(
                zip(decided["client_id"], decided["pan"])
            )

            print("Threshold           :", threshold)
            print("Pairs detected      :", len(decided))

            for name in ("PREFIX", "FUZZY", "TOKEN"):

                print(
                    "  {:16s}:".format(name.lower()),
                    int((decided["match_type"] == name).sum())
                    if not decided.empty else 0,
                )

            print(
                "Auto-merge          :",
                "ON" if (AUTO_MERGE_DEFAULT if auto_merge is None else auto_merge)
                else "OFF (reporting only)",
            )
            print("Merged unattended   :", len(auto))
            print("Queued for review   :", len(review))
            print("Alias rows implied  :", len(aliases))

            if dry_run:

                print("\nDry run: nothing written.")

            else:

                for row in aliases.itertuples():

                    result = connection.execute(
                        ALIAS_INSERT_SQL,
                        {
                            "client_id": row.client_id,
                            "address_key_alias": row.address_key_alias,
                            "address_key_canonical": row.address_key_canonical,
                            "pan": pan_by_client.get(row.client_id),
                        },
                    )

                    aliases_written += result.rowcount or 0

                for row in review.itertuples():

                    result = connection.execute(
                        REVIEW_INSERT_SQL,
                        {
                            "client_id": row.client_id,
                            "key_a": row.key_a,
                            "key_b": row.key_b,
                            "similarity": round(float(row.similarity), 3),
                            "match_type": row.match_type,
                        },
                    )

                    newly_queued += result.rowcount or 0

                print("Alias rows written  :", aliases_written)
                print("Newly queued        :", newly_queued)

            queued = connection.execute(QUEUE_SIZE_SQL).scalar() or 0

            print("Queue size          :", queued)

        return {
            "detected": len(decided),
            "auto": len(auto),
            "review": len(review),
            "aliases_written": aliases_written,
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

    parser.add_argument(
        "--auto-merge",
        action="store_true",
        default=None,
        help=(
            "Apply the AUTO tier as merges. Off unless "
            "CLIENT_ADDRESS_AUTO_MERGE is set; without it every pair is only "
            "queued for review."
        ),
    )

    args = parser.parse_args(argv)

    return detect(
        dry_run=args.dry_run,
        threshold=args.threshold,
        auto_merge=args.auto_merge,
    )


if __name__ == "__main__":

    main()
