"""Rewriting keys stops a variant coming back, but the rows already in
gold.client_address predate the merge and would stay live forever. The loader
retires them on the same pass, which is what actually drops the client from
three addresses to one in the app.

is_main is the part that bites: Aashutosh's main address is seq 1, the typo'd
spelling, while the survivor is seq 3. Retiring seq 1 without moving the flag
leaves a client with no main address at all.
"""

import pandas as pd

from etl_gold_client_address import plan_alias_retirement


CLIENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

CANON = "38THALTEJALOKBUNGLOWSTHALTEJNEARSALHOSPITAL"
VARIANT = "NO38ALOKBUNGLOWSNEARSUNNSTEPCLUBBODAKDEV"


def _live(*rows):
    return pd.DataFrame(
        list(rows), columns=["id", "client_id", "address_key", "is_main"]
    )


def _aliases(*rows):
    return pd.DataFrame(
        list(rows),
        columns=["client_id", "address_key_alias", "address_key_canonical"],
    )


def test_an_aliased_row_is_retired():
    retire, _ = plan_alias_retirement(
        _live(("id-1", CLIENT, VARIANT, False), ("id-2", CLIENT, CANON, True)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert retire == ["id-1"]


def test_the_surviving_row_is_never_retired():
    retire, _ = plan_alias_retirement(
        _live(("id-1", CLIENT, CANON, True)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert retire == []


def test_retiring_the_main_row_promotes_the_survivor():
    retire, promote = plan_alias_retirement(
        _live(("id-1", CLIENT, VARIANT, True), ("id-2", CLIENT, CANON, False)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert retire == ["id-1"]
    assert promote == [(CLIENT, CANON)]


def test_a_survivor_that_is_already_main_is_not_promoted_again():
    """Rewriting is_main on every pass would bump updated_at forever and make
    it useless as a signal that something changed."""
    _, promote = plan_alias_retirement(
        _live(("id-1", CLIENT, VARIANT, False), ("id-2", CLIENT, CANON, True)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert promote == []


def test_another_clients_identical_spelling_is_left_alone():
    retire, _ = plan_alias_retirement(
        _live(("id-9", OTHER, VARIANT, True)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert retire == []


def test_no_aliases_retires_nothing():
    retire, promote = plan_alias_retirement(
        _live(("id-1", CLIENT, CANON, True)), _aliases()
    )
    assert retire == [] and promote == []


def test_a_row_is_not_retired_when_its_survivor_is_absent():
    """Retiring the only live row would leave the client with no address at
    all. A restore that regenerates client ids can strand aliases this way."""
    retire, promote = plan_alias_retirement(
        _live(("id-1", CLIENT, VARIANT, True)),
        _aliases((CLIENT, VARIANT, CANON)),
    )
    assert retire == []
    assert promote == []


def test_a_stranded_alias_does_not_block_a_real_merge():
    """One client's missing survivor must not suppress another's."""
    retire, _ = plan_alias_retirement(
        _live(("id-1", CLIENT, VARIANT, False),
              ("id-2", CLIENT, CANON, True),
              ("id-9", OTHER, VARIANT, True)),
        _aliases((CLIENT, VARIANT, CANON), (OTHER, VARIANT, CANON)),
    )
    assert retire == ["id-1"]
