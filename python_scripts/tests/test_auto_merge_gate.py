"""detect() runs unattended inside gold_loader every 15 minutes, and merging
is the one thing it does that a person cannot easily take back. The gate
decides whether a pass merges or only reports, and it defaults to OFF: a
pipeline that starts merging because someone deployed is exactly the surprise
this exists to prevent.

With the gate shut the script behaves as it did before merging existed --
every pair, however strong, goes to gold.client_address_review and waits.
"""

import pandas as pd

import detect_client_address_duplicates as detector
from detect_client_address_duplicates import split_by_tier


def _decided(*tiers):
    return pd.DataFrame(
        [{"client_id": "c", "key_a": "A", "key_b": "B", "tier": t} for t in tiers]
    )


def test_the_gate_is_shut_unless_someone_opens_it():
    """Deploying this code must not start merging on the next cron pass."""
    assert detector.AUTO_MERGE_DEFAULT is False


def test_an_open_gate_merges_the_auto_pairs():
    auto, review = split_by_tier(_decided("AUTO", "REVIEW"), auto_merge=True)

    assert len(auto) == 1
    assert len(review) == 1


def test_a_shut_gate_merges_nothing():
    auto, _ = split_by_tier(_decided("AUTO", "AUTO", "REVIEW"), auto_merge=False)

    assert auto.empty


def test_a_shut_gate_still_queues_every_pair_it_found():
    """Detection is the half that is always safe. Shutting the gate must not
    also hide the pairs -- that would trade a merge for a blind spot."""
    _, review = split_by_tier(_decided("AUTO", "AUTO", "REVIEW"), auto_merge=False)

    assert len(review) == 3


def test_no_candidates_splits_into_nothing():
    auto, review = split_by_tier(_decided(), auto_merge=True)

    assert auto.empty and review.empty
