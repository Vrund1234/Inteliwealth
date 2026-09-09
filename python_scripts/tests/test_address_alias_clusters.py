"""AUTO pairs are pairs, but a merge is a cluster. Aashutosh's three spellings
are connected by two AUTO pairs through the middle row, and the ETL rewrite is
a single lookup -- not a transitive one -- so every alias must point straight
at the surviving row. An alias chain A->B->C would leave B live forever.
"""

import pandas as pd

from detect_client_address_duplicates import resolve_clusters


CLIENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def _pairs(*rows):
    return pd.DataFrame(list(rows), columns=["client_id", "key_a", "key_b"])


def test_a_two_row_merge_points_the_weaker_key_at_the_stronger():
    out = resolve_clusters(
        _pairs((CLIENT, "A", "B")),
        evidence={(CLIENT, "A"): 2, (CLIENT, "B"): 14},
    )
    assert out[["address_key_alias", "address_key_canonical"]].values.tolist() == [["A", "B"]]


def test_a_three_row_cluster_points_every_alias_at_one_canonical():
    """A-B and A-C, so B and C are joined only through A. All three are one
    house and both aliases must name the same survivor -- never each other."""
    out = resolve_clusters(
        _pairs((CLIENT, "A", "B"), (CLIENT, "A", "C")),
        evidence={(CLIENT, "A"): 2, (CLIENT, "B"): 3, (CLIENT, "C"): 14},
    )
    assert set(out["address_key_canonical"]) == {"C"}
    assert set(out["address_key_alias"]) == {"A", "B"}


def test_the_canonical_never_appears_as_an_alias():
    """A row that is both would be rewritten away and take the cluster with it."""
    out = resolve_clusters(
        _pairs((CLIENT, "A", "B"), (CLIENT, "A", "C")),
        evidence={(CLIENT, "A"): 2, (CLIENT, "B"): 3, (CLIENT, "C"): 14},
    )
    assert set(out["address_key_alias"]).isdisjoint(set(out["address_key_canonical"]))


def test_the_most_evidenced_spelling_survives():
    """The survivor is the one the RTAs actually sent most often, not the one
    that happened to load first."""
    out = resolve_clusters(
        _pairs((CLIENT, "SPARSE", "COMMON")),
        evidence={(CLIENT, "SPARSE"): 1, (CLIENT, "COMMON"): 25},
    )
    assert out["address_key_canonical"].tolist() == ["COMMON"]


def test_clients_are_resolved_independently():
    """Two people can hold the same two spellings without being one cluster."""
    out = resolve_clusters(
        _pairs((CLIENT, "A", "B"), (OTHER, "A", "B")),
        evidence={(CLIENT, "A"): 1, (CLIENT, "B"): 2,
                  (OTHER, "A"): 9, (OTHER, "B"): 1},
    )
    canonical = dict(zip(out["client_id"], out["address_key_canonical"]))
    assert canonical == {CLIENT: "B", OTHER: "A"}


def test_a_tie_is_broken_deterministically():
    """Equal evidence must not make the survivor depend on row order, or a
    re-run would flip the merge and thrash gold.client_address."""
    first = resolve_clusters(
        _pairs((CLIENT, "A", "B")), evidence={(CLIENT, "A"): 3, (CLIENT, "B"): 3}
    )
    second = resolve_clusters(
        _pairs((CLIENT, "B", "A")), evidence={(CLIENT, "A"): 3, (CLIENT, "B"): 3}
    )
    assert first["address_key_canonical"].tolist() == second["address_key_canonical"].tolist()


def test_no_pairs_yields_no_aliases():
    assert resolve_clusters(_pairs(), evidence={}).empty
