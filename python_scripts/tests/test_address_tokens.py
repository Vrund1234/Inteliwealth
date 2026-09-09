"""The token rule decides which addresses are proposed as the same place, and
its AUTO tier merges without a human. A change here silently merges or splits
real client addresses, so every rule it relies on is pinned by a test.

Every case below is taken from live gold.client_address rows.
"""

import pytest

from utils.address_tokens import (
    address_tokens,
    classify_pair,
    house_number,
)


# ---- tokenising ----------------------------------------------------------

def test_tokens_are_upper_cased_and_split_on_punctuation():
    assert address_tokens("38, THALTEJ ALOK BUNGLOWS", None, None) >= {"THALTEJ", "ALOK"}


def test_stopwords_are_dropped():
    """BUNGLOWS and NEAR appear in hundreds of addresses and prove nothing."""
    tokens = address_tokens("38, ALOK BUNGLOWS", "NEAR SAL HOSPITAL", None)
    assert "BUNGLOWS" not in tokens
    assert "NEAR" not in tokens
    assert "ALOK" in tokens


def test_short_tokens_are_dropped():
    """SAL is 3 characters -- too short to distinguish one address from another."""
    assert "SAL" not in address_tokens("NEAR SAL HOSPITAL", None, None)


def test_bare_numbers_are_dropped():
    """House and pincode digits are handled by house_number, not by tokens."""
    assert address_tokens("38 380054 ALOK", None, None) == {"ALOK"}


def test_lines_are_tokenised_together():
    """Different RTAs split one phrase across line1/line2/line3."""
    split = address_tokens("38 ALOK", "BUNGLOWS THALTEJ", None)
    joined = address_tokens("38 ALOK BUNGLOWS THALTEJ", None, None)
    assert split == joined


def test_missing_lines_are_ignored():
    assert address_tokens("ALOK", None, float("nan")) == {"ALOK"}


# ---- house number --------------------------------------------------------

@pytest.mark.parametrize("line1,expected", [
    ("38, ALOK BUNGLOWS", "38"),
    ("NO 38 ALOK BUNGLOWS", "38"),
    ("B/102 PANCHJANYA APPARTMENT", "B102"),
    ("B 102 PANCHJANYA APPARTMENT", "B102"),
    ("A201, Sugam Residency", "A201"),
    ("56 SUMERU BUNGLOWS BODAKDEV", "56"),
])
def test_house_number_is_format_agnostic(line1, expected):
    assert house_number(line1) == expected


def test_house_number_is_none_when_the_line_starts_with_words():
    assert house_number("Consolidated Folio") is None


# ---- tiering -------------------------------------------------------------
#
# AUTO merges with no human. REVIEW waits for one. The house-number condition
# is what separates "same house, different landmark" from "different flat in
# one building".

def test_prefix_pair_is_auto():
    """3/A GOPIKUNJ SOC NEAR PRAGATINAGAR GARDE vs ...GARDEN AHMEDABAD."""
    assert classify_pair(
        key_a="3AGOPIKUNJSOCNEARPRAGATINAGARGARDE",
        key_b="3AGOPIKUNJSOCNEARPRAGATINAGARGARDENAHMEDABAD",
        shared_rare=2, same_house_number=True, similarity=0.73,
    ) == "AUTO"


def test_high_similarity_pair_is_auto_even_without_a_house_number():
    """S O Rakeshbhai B25 Motera Sanskrut Bungl vs ...Bunglows."""
    assert classify_pair(
        key_a="A", key_b="B",
        shared_rare=3, same_house_number=False, similarity=0.87,
    ) == "AUTO"


def test_same_house_number_with_two_rare_tokens_is_auto():
    """38, ALOK BUNGLOWS / NERSAL HOSPITAL vs 38, THALTEJ ALOK BUNGLOWS.
    Trigram scores this 0.49 -- no workable threshold reaches it."""
    assert classify_pair(
        key_a="A", key_b="B",
        shared_rare=2, same_house_number=True, similarity=0.49,
    ) == "AUTO"


def test_one_shared_token_is_never_auto():
    """NO 38 ALOK BUNGLOWS vs 38, THALTEJ ALOK BUNGLOWS share only ALOK."""
    assert classify_pair(
        key_a="A", key_b="B",
        shared_rare=1, same_house_number=True, similarity=0.19,
    ) == "REVIEW"


def test_different_house_numbers_are_never_auto():
    """244-1489 GAYATRI NAGAR vs B 25 Kunwar Park Society -- two real
    addresses sharing the rare token CHANDKHEDA. Auto-merging destroys one."""
    assert classify_pair(
        key_a="A", key_b="B",
        shared_rare=1, same_house_number=False, similarity=0.13,
    ) == "REVIEW"


# ---- house number behind a care-of prefix --------------------------------
#
# RTAs prepend the holder's relation to the address line. The house number is
# still there, just not first, and without this the pair cannot qualify for
# AUTO no matter how much else it shares.

@pytest.mark.parametrize("line1,expected", [
    ("W/O: JATINBHAI PATEL 38 ALOK BUNGLOWS NEAR SAL HOSPITAL", "38"),
    ("W/O Hemant B Pandya, 7 MANGALAM SOCIETY,", "7"),
    ("SO Naresh Gupta 209 BANGUR AVENUE", "209"),
    ("S/O Fardoon 8 Mohudawala Flats Khamasa", "8"),
    ("S O MUKTILAL PRAJAPATI 2 SANSKAR NAGAR MODHERA ROAD", "2"),
    ("C/O RAJENDRA ASHOK KUMAR PATNI. 402, KSP LANDMARK", "402"),
    ("S O Rakeshbhai B25 Motera Sanskrut Bunglows", "B25"),
])
def test_a_care_of_prefix_does_not_hide_the_house_number(line1, expected):
    assert house_number(line1) == expected


def test_the_same_house_matches_with_and_without_the_prefix():
    """This is the whole point: one feed writes the relation, the other does
    not, and both describe 209 Bangur Avenue."""
    assert house_number("SO Naresh Gupta 209 BANGUR AVENUE") == \
        house_number("209 BANGUR AVENUE BLOCK -A LAKETOWN")


def test_a_locality_beginning_with_so_is_not_read_as_a_care_of():
    """SOLA is a place, not "son of". Treating it as a prefix would go hunting
    for a number deeper in the line and match unrelated addresses."""
    assert house_number("SOLA ROAD NEAR 5 BUNGLOWS") is None


# ---- geography is derived, never listed ----------------------------------
#
# A city name cannot distinguish two addresses of ONE client -- both are in the
# same city, and the rule already requires both to share a pincode. Naming the
# cities in STOPWORDS only worked for the cities someone had thought of: a
# client in Pune kept PUNE as evidence, so "12 SHIVAM APARTMENT, PUNE" and
# "12 SHIVAM PLAZA, PUNE" shared two rare words and merged unattended, while
# the same pair in Ahmedabad correctly went to review.

def test_the_rows_own_city_is_not_evidence():
    assert "AHMEDABAD" not in address_tokens(
        "38 ALOK BUNGLOWS", "THALTEJ AHMEDABAD", None, city="Ahmedabad"
    )


def test_any_city_works_not_only_the_ones_someone_listed():
    assert "PUNE" not in address_tokens(
        "12 SHIVAM APARTMENT", "KOTHRUD PUNE", None, city="Pune"
    )


def test_state_and_country_are_dropped_the_same_way():
    tokens = address_tokens(
        "38 ALOK GUJARAT INDIA", None, None, state="Gujarat", country="India"
    )
    assert "GUJARAT" not in tokens and "INDIA" not in tokens


def test_a_multi_word_city_drops_every_one_of_its_words():
    """CAMS writes the city as 'AHMEDABAD CITY'."""
    tokens = address_tokens("9 KAIRVI AHMEDABAD CITY", None, None, city="AHMEDABAD CITY")
    assert "AHMEDABAD" not in tokens


def test_a_city_name_survives_in_a_row_that_is_not_in_that_city():
    """SURAT in the lines of an Ahmedabad address is real text, not the city --
    dropping it everywhere is what the hardcoded list got wrong."""
    assert "SURAT" in address_tokens("5 SURAT WALA HOUSE", None, None, city="Ahmedabad")


def test_geography_is_optional():
    assert address_tokens("38 ALOK BUNGLOWS", None, None) == {"ALOK"}


def test_no_place_names_remain_in_the_stopword_list():
    """STOPWORDS is address vocabulary now. A place in it would be a place that
    stops being evidence everywhere, for every client."""
    from utils.address_tokens import STOPWORDS

    assert not ({"AHMEDABAD", "SURAT", "VADODARA", "RAJKOT", "MUMBAI",
                 "DELHI", "GUJARAT", "INDIA"} & STOPWORDS)


# ---- rarity scales with the book -----------------------------------------

def test_the_rare_cutoff_is_a_share_of_the_book():
    """A fixed 20 was right for 640 addresses and wrong for 10,000: ROAD and
    LANE would start counting as evidence as the book grew."""
    from utils.address_tokens import rare_token_cutoff

    assert rare_token_cutoff(640) == 20
    assert rare_token_cutoff(10000) == 300


def test_a_small_book_still_has_a_usable_cutoff():
    """Below ~30 addresses the share rounds toward zero and nothing would ever
    count as rare, which would silently switch the token rule off."""
    from utils.address_tokens import rare_token_cutoff

    assert rare_token_cutoff(10) >= 1
