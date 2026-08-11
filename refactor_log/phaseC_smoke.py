"""Phase C smoke test — vectorised folio-nominee reshape + scheme-mapping rules.

Run with:
    cd python_scripts && PYTHONPATH=$PWD ./venv/bin/python <this file>

Part 1  before/after parity on messy sample frames (strict dtype equality AND
        normalize_for_compare equality)
Part 2  wall-clock comparison on ~50k rows
"""

import re
import time

import pandas as pd

from common.etl_helpers import normalize_for_compare
import gold.folio_nominees as fn
import mappings.scheme_mapping as sm


# =====================================================================
# REFERENCE IMPLEMENTATIONS — verbatim pre-Phase-C logic
# =====================================================================

def ref_flatten_nominee_rows(df):
    """The original iterrows() double loop + DataFrame construction."""
    gold_rows = []

    nominee_configs = [
        (1, "nominee1"),
        (2, "nominee2"),
        (3, "nominee3"),
    ]

    for _, row in df.iterrows():

        for seq, prefix in nominee_configs:

            nominee_name = row.get(f"{prefix}_name")

            if pd.isna(nominee_name):
                nominee_name = None
            else:
                nominee_name = str(nominee_name).strip()
                if nominee_name == "":
                    nominee_name = None

            relationship = row.get(f"{prefix}_relation")

            if pd.isna(relationship):
                relationship = None
            else:
                relationship = str(relationship).strip()

            percentage = pd.to_numeric(
                row.get(f"{prefix}_percentage"),
                errors="coerce",
            )

            gold_rows.append({
                "holding_id": row["holding_id"],
                "seq": seq,
                "name": nominee_name,
                "relationship": relationship,
                "percentage": percentage,
                "dob": None,
                "is_minor": None,
                "guardian_name": None,
                "id_type": None,
                "id_no": None,
                "address": None,
            })

    return pd.DataFrame(
        gold_rows,
        columns=[
            "holding_id", "seq", "name", "relationship", "percentage",
            "dob", "is_minor", "guardian_name", "id_type", "id_no", "address",
        ],
    )


def ref_transform_folio_nominees(df, holdings):
    """Original transform_folio_nominees, with the holdings read injected."""
    if df.empty:
        return pd.DataFrame()

    if holdings.empty:
        return pd.DataFrame()

    df["source"] = df["source"].fillna("").astype(str).str.strip().str.upper()
    df["folio_no"] = (
        df["folio_no"].fillna("").astype(str).str.strip()
        .str.replace(".0", "", regex=False)
    )
    holdings["rta"] = (
        holdings["rta"].fillna("").astype(str).str.strip().str.upper()
    )
    holdings["folio_number"] = (
        holdings["folio_number"].fillna("").astype(str).str.strip()
        .str.replace(".0", "", regex=False)
    )

    df = df.merge(
        holdings,
        left_on=["source", "folio_no"],
        right_on=["rta", "folio_number"],
        how="left",
    )
    df.rename(columns={"id": "holding_id"}, inplace=True)

    gold_df = ref_flatten_nominee_rows(df)

    gold_df = gold_df[gold_df["holding_id"].notna()]
    gold_df = gold_df.drop_duplicates(
        subset=["holding_id", "seq"], keep="last"
    )

    gold_df["name"] = gold_df["name"].astype("string").str[:255]
    gold_df["relationship"] = gold_df["relationship"].astype("string").str[:60]
    gold_df["guardian_name"] = (
        gold_df["guardian_name"].astype("string").str[:255]
    )
    gold_df["id_type"] = gold_df["id_type"].astype("string").str[:20]
    gold_df["id_no"] = gold_df["id_no"].astype("string").str[:50]
    gold_df["address"] = gold_df["address"].astype("string").str[:500]

    gold_df["created_at"] = pd.Timestamp.now()

    return gold_df


def ref_apply_isin_match(df, amfi_df):
    """Original Rule 0 loop."""
    isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

    for idx, row in df.iterrows():

        if not row["rta_isin"]:
            continue

        if not isin_pattern.match(row["rta_isin"]):
            continue

        matches = amfi_df[
            (amfi_df["isin_growth"] == row["rta_isin"]) |
            (amfi_df["isin_idcw"] == row["rta_isin"])
        ]

        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "ISIN_MATCH"
            df.at[idx, "mapping_confidence"] = 100


def ref_apply_isin_match_null_guarded(df, amfi_df):
    """Original Rule 0 loop, with ONE addition: a null guard.

    Needed because `iterrows()` under pandas 3 rebuilds each row as a `str`
    Series, so an object-column None arrives as NaN — which is truthy, so the
    unmodified loop feeds a float to re.match() and raises TypeError.  This
    variant skips nulls (what the original `if not row[...]` line was plainly
    written to do) so the *matching* logic can still be compared before/after.
    """
    isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

    for idx, row in df.iterrows():

        if pd.isna(row["rta_isin"]) or not row["rta_isin"]:
            continue

        if not isin_pattern.match(row["rta_isin"]):
            continue

        matches = amfi_df[
            (amfi_df["isin_growth"] == row["rta_isin"]) |
            (amfi_df["isin_idcw"] == row["rta_isin"])
        ]

        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "ISIN_MATCH"
            df.at[idx, "mapping_confidence"] = 100


def ref_apply_exact_name_match(df, amfi_df):
    """Original Rule 1 loop."""
    for idx, row in df.iterrows():

        if pd.notna(df.at[idx, "mapping_confidence"]):
            continue

        matches = amfi_df[
            amfi_df["name_norm"] == row["normalized_scheme_name"]
        ]

        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "EXACT_NAME"
            df.at[idx, "mapping_confidence"] = 99


def ref_apply_product_match(df, amfi_df):
    """Original Rule 2 loop — unchanged logic, used to prove the narrowing."""
    for idx, row in df.iterrows():

        if pd.notna(df.at[idx, "mapping_confidence"]):
            continue

        matches = amfi_df[
            (amfi_df["amc_code"] == row["rta_amc_code"])
            &
            (amfi_df["name_norm"] == row["normalized_scheme_name"])
        ]

        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "PRODUCT_MATCH"
            df.at[idx, "mapping_confidence"] = 100


def new_apply_product_match(df, amfi_df):
    """Rule 2 exactly as it now reads in scheme_mapping.load_scheme_mapping."""
    unmatched_product_df = df[df["mapping_confidence"].isna()].copy()

    for idx, row in unmatched_product_df.iterrows():

        matches = amfi_df[
            (amfi_df["amc_code"] == row["rta_amc_code"])
            &
            (amfi_df["name_norm"] == row["normalized_scheme_name"])
        ]

        if len(matches) == 1:
            df.at[idx, "amfi_scheme_code"] = matches.iloc[0]["amfi_scheme_code"]
            df.at[idx, "mapping_source"] = "PRODUCT_MATCH"
            df.at[idx, "mapping_confidence"] = 100


# =====================================================================
# COMPARISON HELPERS
# =====================================================================

FAILURES = []


def check(label, expected, actual, drop=()):
    """Strict (dtype-exact) equality plus normalize_for_compare equality."""
    expected = expected.drop(columns=list(drop), errors="ignore")
    actual = actual.drop(columns=list(drop), errors="ignore")

    try:
        pd.testing.assert_frame_equal(
            expected, actual, check_dtype=True, check_exact=True
        )
        strict = "strict-equal (dtypes included)"
    except AssertionError as exc:
        FAILURES.append(f"{label} [strict]: {exc}")
        print(f"  [FAIL] {label} — strict: {str(exc).splitlines()[0]}")
        return

    try:
        pd.testing.assert_frame_equal(
            normalize_for_compare(expected),
            normalize_for_compare(actual),
            check_dtype=True,
            check_exact=True,
        )
    except AssertionError as exc:
        FAILURES.append(f"{label} [normalized]: {exc}")
        print(f"  [FAIL] {label} — normalize_for_compare: "
              f"{str(exc).splitlines()[0]}")
        return

    print(f"  [PASS] {label} — {strict} + normalize_for_compare match "
          f"({expected.shape[0]} rows x {expected.shape[1]} cols)")


# =====================================================================
# SAMPLE FRAMES
# =====================================================================

def sample_silver():
    """Deliberately messy: nulls, blanks, whitespace, numerics-as-text,
    unparseable percentages, duplicate (source, folio_no) pairs, an
    over-length name, a folio that matches no holding."""
    return pd.DataFrame({
        "source": ["cams", " KFIN ", "CAMS", "cams", None, "KFIN", "CAMS"],
        "folio_no": ["1001.0", " 2002 ", "1001", "9999", "3003", "2002", "4004"],
        "nominee1_name": [
            " Alice ", "BOB", "", "  ", None, "Zed" * 200, 12345,
        ],
        "nominee1_relation": [
            "Spouse ", None, "", "  Son ", "Daughter", "F" * 90, 7,
        ],
        "nominee1_percentage": ["50", 25.5, None, "abc", "100", "", 33],
        "nominee2_name": [
            "Carol", None, "Dan", "Eve", "Fay", "Gil", None,
        ],
        "nominee2_relation": [None, "Brother", "", "Sister", None, "X", "Y"],
        "nominee2_percentage": [50, "50", "50", 0, None, "12.5", "nan"],
        "nominee3_name": [None, None, None, None, None, None, None],
        "nominee3_relation": [None, None, None, None, None, None, None],
        "nominee3_percentage": [None, None, None, None, None, None, None],
        "created_at": pd.to_datetime(["2026-01-01"] * 7),
    })


def sample_silver_missing_columns():
    """nominee2_* / nominee3_* absent entirely — the row.get(...) -> None path."""
    return pd.DataFrame({
        "source": ["CAMS", "KFIN"],
        "folio_no": ["1001", "2002"],
        "nominee1_name": ["Alice", None],
        "nominee1_relation": ["Spouse", None],
        "nominee1_percentage": ["100", None],
    })


def sample_holdings():
    return pd.DataFrame({
        "id": [11, 22, 33],
        "rta": ["CAMS", "kfin ", "CAMS"],
        "folio_number": ["1001", "2002", "3003"],
    })


def sample_scheme_frames():
    """Covers: unique ISIN hit, ambiguous ISIN (2 AMFI rows -> no match),
    growth==idcw on one row (still ONE match), malformed ISIN, null ISIN,
    unique name hit, ambiguous name, name with no AMFI counterpart, and a
    row already matched by Rule 0 that Rule 1 must leave alone."""
    df = pd.DataFrame({
        "rta": ["CAMS"] * 8,
        "rta_amc_code": ["A1", "A1", "A2", "A2", "A3", "A3", "A4", "A4"],
        "rta_scheme_code": [f"P{i}" for i in range(8)],
        "normalized_scheme_name": [
            "ALPHA GROWTH",     # 0 unique ISIN match wins before name
            "UNIQUE NAME ONE",  # 1 unique name
            "AMBIGUOUS NAME",   # 2 two AMFI rows -> no match
            "NO SUCH NAME",     # 3 zero AMFI rows -> no match
            None,               # 4 null name -> no match
            "UNIQUE NAME TWO",  # 5 unique name
            "SELF SAME ISIN",   # 6 growth == idcw, single AMFI row
            "AMBIG ISIN NAME",  # 7 ISIN on two AMFI rows -> falls to name
        ],
    })
    # Object dtype with literal None, exactly as production builds it
    # (`df["rta_isin"] = None`).  A plain list would be inferred as pandas
    # 3.0 `str` dtype, turning the Nones into NaN — see the NaN-ISIN check
    # further down.
    df["rta_isin"] = pd.Series(
        [
            "INF209K01234",   # 0 valid + unique
            None,             # 1 null
            "",               # 2 blank
            "NOTANISIN",      # 3 malformed
            "in209k012345",   # 4 lowercase -> malformed
            None,             # 5
            "INF999K01119",   # 6 growth == idcw on one AMFI row
            "INF888K01118",   # 7 present on two AMFI rows -> ambiguous
        ],
        dtype=object,
    )
    df["mapping_source"] = None
    df["mapping_confidence"] = None

    amfi_df = pd.DataFrame({
        "amfi_scheme_code": ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8",
                             "C9"],
        "amc_code": ["A1", "A1", "A2", "A2", "A3", "A3", "A4", "A4", "A5"],
        "name_norm": [
            "ALPHA GROWTH",
            "UNIQUE NAME ONE",
            "AMBIGUOUS NAME",
            "AMBIGUOUS NAME",
            None,
            "UNIQUE NAME TWO",
            "SELF SAME ISIN",
            "AMBIG ISIN NAME",
            "SOME OTHER NAME",   # C9 shares row 7's ISIN -> ambiguous ISIN
        ],
        "isin_growth": [
            "INF209K01234", None, None, None,
            None, None, "INF999K01119", "INF888K01118", "INF888K01118",
        ],
        # C7 carries the SAME isin on growth and idcw: that is still ONE
        # matching AMFI row and must match, not read as ambiguous.
        "isin_idcw": [
            None, None, None, None,
            None, None, "INF999K01119", None, None,
        ],
    })
    return df, amfi_df


# =====================================================================
# PART 1 — PARITY
# =====================================================================

print("=" * 78)
print("PART 1 — BEFORE / AFTER PARITY")
print("=" * 78)

print("\n[folio_nominees]")

for label, silver in (
    ("flatten_nominee_rows / messy sample", sample_silver()),
    ("flatten_nominee_rows / missing nominee2+3 columns",
     sample_silver_missing_columns()),
):
    merged = silver.copy()
    merged["holding_id"] = [
        11.0, 22.0, None, 33.0, 11.0, 22.0, None
    ][:len(merged)]
    check(label, ref_flatten_nominee_rows(merged), fn.flatten_nominee_rows(merged))

# Full transform, with safe_read stubbed out (no DB).
holdings = sample_holdings()
fn.safe_read = lambda query: holdings.copy()

check(
    "transform_folio_nominees / end-to-end",
    ref_transform_folio_nominees(sample_silver(), holdings.copy()),
    fn.transform_folio_nominees(sample_silver()),
    drop=("created_at",),
)

# All-unmatched folios -> empty gold frame.
lonely = sample_silver()
lonely["folio_no"] = "does-not-exist"
check(
    "transform_folio_nominees / no holding matches",
    ref_transform_folio_nominees(lonely.copy(), holdings.copy()),
    fn.transform_folio_nominees(lonely.copy()),
    drop=("created_at",),
)

print("\n[scheme_mapping]")

# --- The original Rule 0 loop cannot run at all under pandas 3 ------------
# `df["rta_isin"] = None` makes an object column; iterrows() rebuilds each row
# as a `str` Series, turning that None into NaN, which is truthy — so the loop
# reaches re.match(nan).  Demonstrated on a frame shaped like the real one.
production_shaped = pd.DataFrame({
    "rta": ["CAMS", "KFIN"],
    "rta_amc_code": ["A1", "A2"],
    "rta_scheme_code": ["P1", "P2"],
    "rta_scheme_name": ["Alpha Fund", "Beta Fund"],
    "normalized_scheme_name": ["ALPHA FUND", "BETA FUND"],
    "mapping_id": ["u1", "u2"],
    "short_scheme_name": ["ALPHA", "BETA"],
    "amc_slug": ["alpha", "beta"],
})
production_shaped["rta_isin"] = None          # exactly as load_scheme_mapping does
production_shaped["mapping_source"] = None
production_shaped["mapping_confidence"] = None
production_shaped["amfi_scheme_code"] = None

_, prod_amfi = sample_scheme_frames()

try:
    ref_apply_isin_match(production_shaped.copy(), prod_amfi)
    print("  [FAIL] expected the original Rule 0 loop to raise on a "
          "production-shaped frame, but it did not")
    FAILURES.append("original Rule 0 unexpectedly survived a null rta_isin")
except TypeError as exc:
    print(f"  [PASS] original Rule 0 loop raises on the production frame "
          f"(TypeError: {exc}) — the vectorised replacement does not")

vectorised_prod = production_shaped.copy()
sm.apply_isin_match(vectorised_prod, prod_amfi)
if vectorised_prod["mapping_confidence"].notna().any():
    FAILURES.append("vectorised Rule 0 matched on an all-null rta_isin frame")
    print("  [FAIL] vectorised Rule 0 matched a row it should have skipped")
else:
    print("  [PASS] vectorised Rule 0 on the production frame — no crash, "
          "no rows matched (the rule's documented no-op behaviour)")

# --- Rule 0 matching logic, on ISINs the original loop can actually reach ---
ref_df, amfi = sample_scheme_frames()
new_df = ref_df.copy()

ref_df["amfi_scheme_code"] = None
new_df["amfi_scheme_code"] = None

# (a) every ISIN non-null -> the ORIGINAL loop runs unmodified
all_isin_ref = ref_df.copy()
all_isin_ref["rta_isin"] = pd.Series(
    [
        "INF209K01234", "NOTANISIN", "BADISIN", "NOTANISIN",
        "in209k012345", "NOTANISIN", "INF999K01119", "INF888K01118",
    ],
    dtype=object,
)
all_isin_new = all_isin_ref.copy()
ref_apply_isin_match(all_isin_ref, amfi)
sm.apply_isin_match(all_isin_new, amfi)
check("Rule 0 : ISIN match (unmodified original loop, no nulls)",
      all_isin_ref, all_isin_new)

# (b) nulls / blanks / malformed mixed in -> compared against the
#     minimally null-guarded original
ref_apply_isin_match_null_guarded(ref_df, amfi)
sm.apply_isin_match(new_df, amfi)
check("Rule 0 : ISIN match (nulls + blanks + malformed)", ref_df, new_df)

ref_apply_exact_name_match(ref_df, amfi)
sm.apply_exact_name_match(new_df, amfi)
check("Rule 1 : exact name match (after Rule 0)", ref_df, new_df)

ref_apply_product_match(ref_df, amfi)
new_apply_product_match(new_df, amfi)
check("Rule 2 : product match (narrowed iteration)", ref_df, new_df)

print("\n  resulting mapping (new implementation):")
print(
    new_df[[
        "rta_scheme_code", "normalized_scheme_name", "rta_isin",
        "amfi_scheme_code", "mapping_source", "mapping_confidence",
    ]].to_string(index=False)
)

# Rule 1 in isolation, on a frame Rule 0 never touched.
iso_ref, iso_amfi = sample_scheme_frames()
iso_new = iso_ref.copy()
iso_ref["amfi_scheme_code"] = None
iso_new["amfi_scheme_code"] = None
ref_apply_exact_name_match(iso_ref, iso_amfi)
sm.apply_exact_name_match(iso_new, iso_amfi)
check("Rule 1 : exact name match (standalone)", iso_ref, iso_new)

# Zero-match frame: proves amfi_scheme_code survives as an all-null column.
empty_ref, empty_amfi = sample_scheme_frames()
empty_new = empty_ref.copy()
empty_ref["amfi_scheme_code"] = None
empty_new["amfi_scheme_code"] = None
no_amfi = empty_amfi.iloc[0:0]
ref_apply_isin_match_null_guarded(empty_ref, no_amfi)
ref_apply_exact_name_match(empty_ref, no_amfi)
sm.apply_isin_match(empty_new, no_amfi)
sm.apply_exact_name_match(empty_new, no_amfi)
check("Rules 0+1 : empty AMFI master (nothing matches)", empty_ref, empty_new)

# NaN (rather than None) in rta_isin — the old loop raised TypeError here
# because `not nan` is False and re.match() then got a float.
nan_ref, nan_amfi = sample_scheme_frames()
nan_ref["rta_isin"] = float("nan")
nan_new = nan_ref.copy()
nan_ref["amfi_scheme_code"] = None
nan_new["amfi_scheme_code"] = None

try:
    ref_apply_isin_match(nan_ref, nan_amfi)
    old_behaviour = "did not raise"
except TypeError:
    old_behaviour = "raised TypeError"

try:
    sm.apply_isin_match(nan_new, nan_amfi)
    if nan_new["mapping_confidence"].notna().any():
        FAILURES.append("NaN rta_isin should match nothing")
        print("  [FAIL] NaN rta_isin — new implementation matched a row")
    else:
        print(f"  [PASS] NaN rta_isin — old loop {old_behaviour}; "
              f"new implementation skips it cleanly")
except Exception as exc:
    FAILURES.append(f"NaN rta_isin raised in new implementation: {exc!r}")
    print(f"  [FAIL] NaN rta_isin — new implementation raised {exc!r}")


# =====================================================================
# PART 2 — 50k TIMING
# =====================================================================

print()
print("=" * 78)
print("PART 2 — WALL CLOCK, ~50k ROWS")
print("=" * 78)


def timed(fn_, *fn_args):
    start = time.perf_counter()
    out = fn_(*fn_args)
    return time.perf_counter() - start, out


N = 50_000

big_silver = pd.DataFrame({
    "source": ["CAMS", "KFIN"] * (N // 2),
    "folio_no": [str(1000 + i) for i in range(N)],
    "nominee1_name": [f" Nominee {i} " for i in range(N)],
    "nominee1_relation": ["Spouse", "", None, "Son "] * (N // 4),
    "nominee1_percentage": ["50", 25.5, None, "abc"] * (N // 4),
    "nominee2_name": [f"Second {i}" if i % 3 else None for i in range(N)],
    "nominee2_relation": ["Brother", None] * (N // 2),
    "nominee2_percentage": [50, "50"] * (N // 2),
    "nominee3_name": [None] * N,
    "nominee3_relation": [None] * N,
    "nominee3_percentage": [None] * N,
})
big_silver["holding_id"] = [
    float(i) if i % 10 else None for i in range(N)
]

ref_secs, ref_out = timed(ref_flatten_nominee_rows, big_silver)
new_secs, new_out = timed(fn.flatten_nominee_rows, big_silver)

print(f"\nflatten_nominee_rows  ({N:,} silver rows -> {len(new_out):,} gold rows)")
print(f"  iterrows (before) : {ref_secs:8.3f} s")
print(f"  vectorised (after): {new_secs:8.3f} s")
print(f"  speed-up          : {ref_secs / new_secs:8.1f}x")

try:
    pd.testing.assert_frame_equal(ref_out, new_out, check_dtype=True,
                                  check_exact=True)
    print("  50k output        : strict-equal to the iterrows version")
except AssertionError as exc:
    FAILURES.append(f"50k flatten: {exc}")
    print(f"  [FAIL] 50k output differs: {str(exc).splitlines()[0]}")

# scheme mapping — N distinct schemes against a 20k-row AMFI master
M = 20_000

def synthetic_isin(i):
    """A well-formed ISIN: 2 letters + 9 alphanumerics + 1 digit."""
    return f"IN{i:09d}"[:11] + str(i % 10)


big_amfi = pd.DataFrame({
    "amfi_scheme_code": [f"C{i}" for i in range(M)],
    "amc_code": [f"A{i % 40}" for i in range(M)],
    "name_norm": [f"SCHEME NAME {i}" for i in range(M)],
    "isin_growth": [synthetic_isin(i) for i in range(M)],
    "isin_idcw": [None] * M,
})

big_scheme = pd.DataFrame({
    "rta": ["CAMS"] * N,
    "rta_amc_code": [f"A{i % 40}" for i in range(N)],
    "rta_scheme_code": [f"P{i}" for i in range(N)],
    # half hit the AMFI master by name, half do not
    "normalized_scheme_name": [
        f"SCHEME NAME {i}" if i % 2 else f"UNKNOWN NAME {i}" for i in range(N)
    ],
    # Real ISINs (no nulls) so the ORIGINAL Rule 0 loop can run at all and
    # actually pays its per-row amfi_df scan; half of them hit the master.
    "rta_isin": pd.Series(
        [synthetic_isin(i) if i % 2 else f"XX{i:09d}" for i in range(N)],
        dtype=object,
    ),
})
big_scheme["mapping_source"] = None
big_scheme["mapping_confidence"] = None
big_scheme["amfi_scheme_code"] = None

ref_big = big_scheme.copy()
new_big = big_scheme.copy()

r0_ref, _ = timed(ref_apply_isin_match, ref_big, big_amfi)
r0_new, _ = timed(sm.apply_isin_match, new_big, big_amfi)

r1_ref, _ = timed(ref_apply_exact_name_match, ref_big, big_amfi)
r1_new, _ = timed(sm.apply_exact_name_match, new_big, big_amfi)

print(f"\nscheme_mapping rules  ({N:,} schemes x {M:,} AMFI rows)")
print(f"  Rule 0 ISIN   iterrows (before) : {r0_ref:8.3f} s")
print(f"  Rule 0 ISIN   vectorised (after): {r0_new:8.3f} s"
      f"   ({r0_ref / r0_new:.1f}x)")
print(f"  Rule 1 name   iterrows (before) : {r1_ref:8.3f} s")
print(f"  Rule 1 name   vectorised (after): {r1_new:8.3f} s"
      f"   ({r1_ref / r1_new:.1f}x)")
print(f"  matched by Rule 1 : {new_big['mapping_confidence'].notna().sum():,}"
      f" / {N:,}")

try:
    pd.testing.assert_frame_equal(ref_big, new_big, check_dtype=True,
                                  check_exact=True)
    print("  50k output        : strict-equal to the iterrows version")
except AssertionError as exc:
    FAILURES.append(f"50k scheme rules: {exc}")
    print(f"  [FAIL] 50k output differs: {str(exc).splitlines()[0]}")


# =====================================================================

print()
print("=" * 78)
if FAILURES:
    print(f"PHASE C SMOKE: {len(FAILURES)} FAILURE(S)")
    for failure in FAILURES:
        print("-" * 78)
        print(failure)
    raise SystemExit(1)

print("PHASE C SMOKE: ALL CHECKS PASSED")
print("=" * 78)
