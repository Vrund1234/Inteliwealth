"""gold.client_address_alias records that two spellings are one place. The ETL
rewrites an alias key to its canonical form BEFORE dedupe, so the variant never
becomes a second row again.

This is what makes a merge survive a re-run. silver.investor_master keeps every
spelling forever, and uq_client_address_natural is partial on is_deleted=false,
so a soft-deleted row alone would simply be re-inserted on the next pass.
"""

import pandas as pd

from etl_gold_client_address import apply_address_aliases


CLIENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

CANONICAL = "38THALTEJALOKBUNGLOWSTHALTEJNEARSALHOSPITAL"
VARIANT = "NO38ALOKBUNGLOWSNEARSUNNSTEPCLUBBODAKDEV"


def _rows(*pairs):
    return pd.DataFrame(
        [{"client_id": c, "address_key": k} for c, k in pairs]
    )


def _aliases(*rows):
    return pd.DataFrame(
        list(rows),
        columns=["client_id", "address_key_alias", "address_key_canonical"],
    )


def test_an_alias_key_is_rewritten_to_its_canonical():
    out = apply_address_aliases(
        _rows((CLIENT, VARIANT)),
        _aliases((CLIENT, VARIANT, CANONICAL)),
    )
    assert out["address_key"].tolist() == [CANONICAL]


def test_a_key_with_no_alias_is_untouched():
    out = apply_address_aliases(
        _rows((CLIENT, "SOMEOTHERADDRESS")),
        _aliases((CLIENT, VARIANT, CANONICAL)),
    )
    assert out["address_key"].tolist() == ["SOMEOTHERADDRESS"]


def test_an_alias_does_not_leak_to_another_client():
    """Two people can live at one address. A merge approved for one of them
    says nothing about the other, so the alias is scoped to its client."""
    out = apply_address_aliases(
        _rows((OTHER, VARIANT)),
        _aliases((CLIENT, VARIANT, CANONICAL)),
    )
    assert out["address_key"].tolist() == [VARIANT]


def test_rewritten_rows_collapse_onto_one_key():
    """The point of the rewrite: what arrived as two spellings leaves as one
    key, which the existing dedupe and UNIQUE index then reduce to one row."""
    out = apply_address_aliases(
        _rows((CLIENT, VARIANT), (CLIENT, CANONICAL)),
        _aliases((CLIENT, VARIANT, CANONICAL)),
    )
    assert out["address_key"].nunique() == 1


def test_no_aliases_leaves_every_key_alone():
    rows = _rows((CLIENT, VARIANT), (OTHER, CANONICAL))
    out = apply_address_aliases(rows, _aliases())
    assert out["address_key"].tolist() == [VARIANT, CANONICAL]


def test_row_count_and_column_set_are_preserved():
    """The rewrite renames keys. Dropping or adding rows here would change
    what the caller then dedupes."""
    rows = _rows((CLIENT, VARIANT), (CLIENT, CANONICAL), (OTHER, VARIANT))
    out = apply_address_aliases(rows, _aliases((CLIENT, VARIANT, CANONICAL)))
    assert len(out) == 3
    assert set(out.columns) == {"client_id", "address_key"}
