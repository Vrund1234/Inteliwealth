"""Word-level evidence that two addresses of one client are the same place.

utils/address_key.py is exact identity: two rows are the same address when the
normalised string matches, and gold.client_address enforces that with a UNIQUE
index. This module handles what exact matching cannot see -- one RTA naming a
different landmark for the same house:

    38, ALOK BUNGLOWS      | NERSAL HOSPITAL      | THALTEJ,BODAKDEV
    NO 38 ALOK BUNGLOWS    | NEAR SUN N STEP CLUB | BODAKDEV
    38, THALTEJ ALOK BUNGLOWS THALTEJ | NEAR SAL HOSPITAL

One house, three folios, three spellings. They score 0.194 - 0.491 trigram
against each other, so no workable similarity threshold reaches them and none
is a prefix of another. What they share is the rare token ALOK and the house
number 38.

WHY SO LOOSE A RULE IS SAFE
===========================

Only within ONE client. Two addresses filed under one PAN are far likelier to
be one house than two arbitrary rows are, and the candidate space is tiny --
686 live addresses across 593 clients produce just 104 within-client pairs.

WHAT MAKES A TOKEN WORTH ANYTHING
=================================

Rarity. BUNGLOWS, NEAR and AHMEDABAD appear in hundreds of addresses and prove
nothing; they are dropped as stopwords before counting. Everything else is
scored by how many addresses carry it, and only tokens in <= 20 of them
(rare_token_cutoff) count as evidence. ALOK qualifies. Bare numbers are
dropped too -- house and pincode digits are handled by house_number(), which
compares them as a unit rather than as loose tokens.

Measured on the 686 rows live 2026-09-09: 63 pairs across 53 clients, of which
39 are strong enough to merge unattended (see classify_pair).
"""

import math
import re


# Tokens shorter than this cannot distinguish one address from another: SAL,
# NR, B/H, OPP. Kept out before rarity is measured.
MIN_TOKEN_LENGTH = 4

# A token carried by more addresses than this share of the book is vocabulary,
# not identity. Expressed as a share and not a count because the count that is
# right for 640 addresses is wrong for 10,000: ROAD (188 of 640 today) would
# slip under a fixed 20 long before the book stopped growing, and start
# counting as evidence. 3% is 20 of the 640 live on 2026-09-09, so this is the
# same cutoff that produced the measurements above.
RARE_TOKEN_SHARE = 0.03

# Trigram score at or above which a pair is the same address on the strength of
# the string alone. Measured: every pair >= 0.85 was one address.
AUTO_SIMILARITY = 0.85

# Shared rare tokens required to merge unattended when the house numbers match.
# One is not enough: 244-1489 GAYATRI NAGAR and B 25 Kunwar Park Society share
# only CHANDKHEDA and are two real addresses.
AUTO_MIN_SHARED_RARE_TOKENS = 2


# Indian address VOCABULARY -- the words a person writes to describe where a
# building is, rather than which building it is. Dropping them before the rarity
# count stops a pair qualifying on nothing but "SOCIETY" and "ROAD".
#
# No place names live here. A city cannot distinguish two addresses of ONE
# client -- both are in it, and the rule already requires a shared pincode --
# but naming cities only covered the ones someone had thought of, so a client
# in Pune kept PUNE as evidence while a client in Ahmedabad did not. Geography
# is taken from the row's own city/state/country instead; see address_tokens.
#
# Kept in sync with the array in
# sql_scripts/check_token_similar_client_address.sql.
STOPWORDS = frozenset({
    "NEAR", "OPPOSITE", "BEHIND", "ROAD", "MARG", "STREET", "LANE", "GALI",
    "CROSS", "CHAR", "RASTA", "SOCIETY", "NAGAR", "PARK", "APARTMENT",
    "APARTMENTS", "FLAT", "BLOCK", "PLOT", "HOUSE", "HOME", "BUNGLOW",
    "BUNGLOWS", "BUNGALOW", "BUNGALOWS", "TOWER", "TOWERS", "COMPLEX",
    "SCHEME", "SECTOR", "PHASE", "FLOOR", "WING", "EAST", "WEST", "NORTH",
    "SOUTH", "SHREE", "SHRI", "BAZAR", "MAIN", "CITY",
    "VILLAGE", "POST", "STATION", "CHOWK", "CIRCLE", "HIGHWAY",
})


def rare_token_cutoff(address_count):
    """How many addresses a token may appear in and still count as evidence.

    Rounded up, and never below 1: a book small enough for 3% to round to zero
    would switch the token rule off silently rather than visibly.
    """
    return max(1, math.ceil(address_count * RARE_TOKEN_SHARE))


_SPLIT = re.compile(r"[^A-Za-z0-9]+")

# An optional "NO"/"NO." prefix, then an optional single letter, then digits.
# Written to read B/102, B 102, B-102 and A201 as one value, because that is
# the same flat written four ways by four feeds.
_HOUSE = re.compile(r"^\s*(?:NO\.?\s+)?([A-Za-z]?)\s*[-/]?\s*(\d+)")

# "W/O:", "S/O", "C O", "SO" -- the holder's relation, which some RTA feeds put
# in front of the address and others leave out. The house number is still on the
# line, just behind a name, and without skipping past it one feed reports a
# house number and the other reports none, so the pair can never qualify.
#
# The \b after O is what keeps this from firing on a locality: SOLA and DOSHI
# both open with one of these letters followed by O, and treating them as a
# prefix would send the scan hunting for a number deeper in the line.
_CARE_OF = re.compile(r"^\s*[WSDC]\s*[/.\s]?\s*O\b\.?\s*[:,-]?\s*", re.IGNORECASE)

# The first word that opens like a house number, used only after a care-of
# prefix has been stripped and a name has to be skipped.
_HOUSE_TOKEN = re.compile(r"^([A-Za-z]?)[-/]?(\d+)")


def _text(value):
    """A line as text. None and pandas NaN/NA count as empty, matching the
    COALESCE(...,'') that address_key uses in SQL."""
    if value is None or not isinstance(value, str):
        try:
            import pandas as pd

            if value is None or pd.isna(value):
                return ""
        except (ImportError, ValueError, TypeError):
            return "" if value is None else str(value)
        return str(value)
    return value


def address_tokens(line1, line2, line3, city=None, state=None, country=None):
    """The words of one address worth comparing, upper-cased.

    The three lines are tokenised together, never separately: different RTAs
    split one phrase across them at different points, so a token must not
    depend on which column it landed in.

    city / state / country are this ROW's own, and their words are dropped.
    They cannot distinguish two addresses of one client -- both carry them, and
    the caller already requires a shared pincode -- so a shared city name is a
    free token toward the threshold, proving nothing. Passing the row's values
    rather than a list of place names means every city works, including the
    ones nobody has seen yet.

    A place name that is NOT this row's geography survives, because there it is
    ordinary text: SURAT in "5 SURAT WALA HOUSE, Ahmedabad" is part of the
    building's name.
    """
    joined = " ".join(_text(v) for v in (line1, line2, line3))

    own_geography = {
        word
        for value in (city, state, country)
        for word in _SPLIT.split(_text(value).upper())
        if word
    }

    return frozenset(
        token
        for token in _SPLIT.split(joined.upper())
        if len(token) >= MIN_TOKEN_LENGTH
        and not token.isdigit()
        and token not in STOPWORDS
        and token not in own_geography
    )


def house_number(line1):
    """The leading house or flat number of an address, or None.

    Returned without punctuation so B/102, B 102 and B-102 compare equal, and
    read through a care-of prefix so "W/O Hemant B Pandya, 7 MANGALAM SOCIETY"
    and "7 MANGALAM SOCIETY" agree on 7. A line that opens with words and no
    prefix has no house number -- "Consolidated Folio" is an RTA placeholder,
    not an address, and must not match anything.
    """
    text = _text(line1)

    care_of = _CARE_OF.match(text)

    if care_of:
        # Skip the holder's name and take the first word that opens with a
        # number. Only done behind a prefix: scanning an ordinary address this
        # way would pick up a pincode or a landmark's number.
        for token in text[care_of.end():].split():

            found = _HOUSE_TOKEN.match(token)

            if found:
                return (found.group(1) + found.group(2)).upper()

        return None

    match = _HOUSE.match(text)

    if match is None:
        return None

    return (match.group(1) + match.group(2)).upper()


def classify_pair(key_a, key_b, shared_rare, same_house_number, similarity):
    """AUTO to merge this pair unattended, REVIEW to put it to a person.

    AUTO on any of three grounds, each measured to hold on the live data:

      * one key extends the other  -- one feed truncated the same string
      * trigram >= AUTO_SIMILARITY -- the strings themselves agree
      * same house number and >= 2 shared rare tokens -- the case trigram
        cannot see, where two feeds name different landmarks for one house

    The house-number condition carries the weight. It is what separates "same
    house, different landmark" from "different flat, same building": 43/44 VRAJ
    VATIKA SOC and B 44 VRAJVATIKA SOC share a rare token and are not the same
    home. Anything AUTO merges is reversible -- the alias row records that it
    was decided automatically, and no address row is ever hard-deleted.
    """
    if key_a.startswith(key_b) or key_b.startswith(key_a):
        return "AUTO"

    if similarity >= AUTO_SIMILARITY:
        return "AUTO"

    if same_house_number and shared_rare >= AUTO_MIN_SHARED_RARE_TOKENS:
        return "AUTO"

    return "REVIEW"
