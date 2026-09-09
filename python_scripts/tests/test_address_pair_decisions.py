"""Every candidate pair the detector finds is either merged unattended or put
to a person. decide_pairs is where that call is made, and a wrong AUTO deletes
a real address, so the split is pinned here.

Cases are live gold.client_address rows.
"""

import pandas as pd

from detect_client_address_duplicates import decide_pairs


def _candidates(*rows):
    return pd.DataFrame(list(rows), columns=[
        "client_id", "pan", "key_a", "key_b",
        "line1_a", "line1_b", "shared_rare", "similarity",
    ])


CLIENT = "11111111-1111-1111-1111-111111111111"


def test_two_rare_tokens_at_one_house_number_is_merged_unattended():
    """38, ALOK BUNGLOWS / NERSAL HOSPITAL vs 38, THALTEJ ALOK BUNGLOWS.
    Trigram scores it 0.49; only the house number and ALOK+THALTEJ prove it."""
    out = decide_pairs(_candidates(
        (CLIENT, "AMHPP6820E", "KEYA", "KEYB",
         "38, ALOK BUNGLOWS", "38, THALTEJ ALOK BUNGLOWS THALTEJ", 2, 0.49),
    ))
    assert out["tier"].tolist() == ["AUTO"]


def test_one_rare_token_at_different_house_numbers_waits_for_a_person():
    """244-1489 GAYATRI NAGAR vs B 25 Kunwar Park Society share only
    CHANDKHEDA. They are two real addresses."""
    out = decide_pairs(_candidates(
        (CLIENT, "DCWPM1406H", "KEYA", "KEYB",
         "244-1489 GAYATRI NAGAR", "B 25 Kunwar Park Society I O C Road", 1, 0.13),
    ))
    assert out["tier"].tolist() == ["REVIEW"]


def test_house_numbers_are_compared_across_punctuation():
    """B/102 and B 102 are one flat written two ways."""
    out = decide_pairs(_candidates(
        (CLIENT, "P", "KEYA", "KEYB",
         "B/102 PANCHJANYA APPARTMENT", "B 102 PANCHJANYA APPARTMENT", 2, 0.68),
    ))
    assert out["tier"].tolist() == ["AUTO"]


def test_a_truncated_string_is_recorded_as_a_prefix_match():
    """Strongest evidence available -- one key literally extends the other --
    so it must not be filed under the weaker rule that also matched."""
    out = decide_pairs(_candidates(
        (CLIENT, "P", "3AGOPIKUNJSOCGARDE", "3AGOPIKUNJSOCGARDENAHMEDABAD",
         "3/A GOPIKUNJ SOC", "3/A GOPIKUNJ SOC", 2, 0.73),
    ))
    assert out["match_type"].tolist() == ["PREFIX"]


def test_a_high_trigram_pair_is_recorded_as_fuzzy():
    out = decide_pairs(_candidates(
        (CLIENT, "P", "KEYA", "KEYB", "9 KAIRVI", "9,KAIRVI", 2, 0.88),
    ))
    assert out["match_type"].tolist() == ["FUZZY"]


def test_a_pair_only_the_token_rule_reaches_is_recorded_as_token():
    out = decide_pairs(_candidates(
        (CLIENT, "P", "KEYA", "KEYB",
         "38, ALOK BUNGLOWS", "38, THALTEJ ALOK BUNGLOWS", 2, 0.49),
    ))
    assert out["match_type"].tolist() == ["TOKEN"]


def test_no_candidates_yields_no_decisions():
    out = decide_pairs(_candidates())
    assert out.empty
