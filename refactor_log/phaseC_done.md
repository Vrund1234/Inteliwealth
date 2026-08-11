# Phase C — Vectorise the two `iterrows()` hot paths (2026-08-11)

Replaced the nominee-flattening double loop in `gold/folio_nominees.py` and the
ISIN / exact-name rule loops in `mappings/scheme_mapping.py` with vectorised
pandas operations. Output is byte-identical (dtypes included) to the loops they
replace; the row-wise fuzzy rule was deliberately left alone.

| File | Change |
|---|---|
| `gold/folio_nominees.py` | `iterrows()` double loop → `flatten_nominee_rows()`, a three-slice concat reshape |
| `mappings/scheme_mapping.py` | Rule 0 (ISIN) and Rule 1 (exact name) loops → `apply_isin_match()` / `apply_exact_name_match()` |
| `mappings/scheme_mapping.py` | Rule 2 (product match) now iterates only the still-unmatched subset |
| `refactor_log/phaseC_smoke.py` | New — the before/after comparison test described below |

## 1. `gold/folio_nominees.py` — nominee flattening

The old code walked `df.iterrows()` and, for each of the three nominee slots,
appended a Python dict — `3 × len(df)` dict builds plus one `DataFrame(list)`
inference pass. Each nominee slot is now built as whole-column slices and the
three slices are concatenated:

```python
gold_df = flatten_nominee_rows(df)
```

New module-level names: `NOMINEE_CONFIGS`, `GOLD_NOMINEE_COLUMNS`,
`EMPTY_NOMINEE_COLUMNS`, and the helpers `_optional_column`,
`_clean_nominee_name`, `_clean_nominee_relationship`, `_as_object_with_none`,
`_reinfer_column`.

### Behaviours that had to be preserved exactly

- **Row order and index.** The loop emitted `row0/seq1, row0/seq2, row0/seq3,
  row1/seq1, …`. Three concatenated slices would emit all seq-1 rows first.
  This matters: `transform_folio_nominees` then calls
  `drop_duplicates(subset=["holding_id", "seq"], keep="last")`, which is
  order-sensitive. Each slice is therefore given the index
  `position * 3 + (seq - 1)` and the concat is `sort_index()`-ed, reproducing
  the interleaved positional index exactly.
- **`name` vs `relationship` asymmetry.** The loop collapsed a
  blank-after-strip `name` to `None` but left a blank `relationship` as `""`.
  That asymmetry looks accidental but is preserved as-is — changing it is a
  behaviour change, not a refactor.
- **Missing columns.** `row.get(f"{prefix}_name")` returned `None` when the
  Silver frame never carried that column. `_optional_column()` reproduces this
  with an all-null object column, so a frame with no `nominee2_*`/`nominee3_*`
  columns still produces three slots per row (covered by a test).
- **`pd.to_numeric(..., errors="coerce")`** is now applied per column instead
  of per scalar. Verified to give the same dtype in every combination: all-int
  slices concat to `int64`, any float or coerced-NaN slice widens the result to
  `float64`, exactly as the loop's `DataFrame(list_of_dicts)` inference did.
- **Null sentinel and dtype.** The loop handed pandas Python `None`;
  `astype("string")` (what makes the strip vectorised) produces `pd.NA` in a
  `string` column instead. `_as_object_with_none()` converts back to
  object/`None`, and `_reinfer_column()` then re-runs pandas' own inference over
  the concatenated column so the result lands on the *same* dtype the loop
  produced — pandas 3's `str` dtype for a column holding any string, plain
  `object` for one that is entirely null. Without this the intermediate came out
  as `string`/`object` where the loop gave `str`, which the strict test caught.

`transform_folio_nominees` is otherwise untouched: the filter, dedupe,
length-truncation and `created_at` stamp all still run on the same frame.

## 2. `mappings/scheme_mapping.py` — Rules 0 and 1

Both loops re-filtered the whole of `amfi_df` on every iteration — O(rows ×
amfi). Both are now a single lookup build plus one `isin`/`map` pass, applied
only to rows whose `mapping_confidence` is still null.

### Why a plain `pd.merge` is not enough

The brief suggested a left join. A bare left join does not express the rules'
`if len(matches) == 1` guard: a key present on **two or more** AMFI rows must
match *nothing* and fall through to the next rule, whereas a merge would fan the
left row out into duplicates and silently change the row count. The shared
helper `unique_match_lookup(frame, key_column, value_column)` therefore counts
first and keeps only keys that identify exactly one row, which makes the
subsequent join safe — one row in, at most one row out. That is a left join on a
de-duplicated key table; it is just spelled as `.isin()` + `.map()` so the index
is preserved and no reassignment is needed.

Null keys are dropped by `value_counts(dropna=True)`, which matches the old
`amfi_df[col] == value` filter: a null never compares equal, so a null-named
AMFI row could never be the single match.

### ISIN pairs need de-duplicating before counting

`isin_growth` and `isin_idcw` are unioned into one long frame. An AMFI row whose
growth and IDCW ISIN are the *same* string is still **one** row for
`len(matches)`, so pairs are de-duplicated per `(amfi row, isin)` before
counting — otherwise such a row would read as ambiguous and be skipped. Both
cases are covered by the test (`SELF SAME ISIN` matches; an ISIN spread across
two different AMFI rows does not, and correctly falls through to Rule 1).

### `amfi_scheme_code` is now created up front

The rules used to create that column implicitly, via `.at[]` enlargement on the
first row that matched. On a run where *no* rule matched a single row the column
would never exist and the later `df.merge(matched_amfi, on="amfi_scheme_code")`
would raise `KeyError`. It is now initialised to `None` alongside
`mapping_source`/`mapping_confidence`. In the normal case this is equivalent —
enlargement also produced an object column, and the difference between `NaN` and
`None` in the untouched cells is erased by the existing
`df.where(pd.notna(df), None)` before insert (and both factorise as null for the
merge).

### Rule 2 narrowed

Rule 2 (Product Match) is still row-wise — it is untouched by this phase — but
it now iterates `df[df["mapping_confidence"].isna()]` instead of walking every
row and `continue`-ing past the matched ones, the same shape Rule 3 already
used. Assignments only ever touch the row being visited, so this is
behaviour-identical; verified by the test.

### Rule 4 (fuzzy) left row-wise — deliberate

`fuzzy_match` calls `rapidfuzz.process.extractOne(..., scorer=fuzz.ratio,
score_cutoff=98)` per row against the AMFI name list. That is genuinely fuzzy —
it cannot be expressed as a join — so it stays `df.apply(..., axis=1)` exactly
as before, per the phase brief. (Rules 2 and 3 *are* exact and could be
vectorised the same way as Rule 1, but they were out of scope here.)

## ⚠️ Bug found: Rule 0 could not run at all under pandas 3

This is not a behaviour change I chose — it is a crash the vectorisation
removes, and it is worth flagging on its own.

`load_scheme_mapping` sets `df["rta_isin"] = None`, which makes an **object**
column of `None`. But `iterrows()` rebuilds each row as a Series, and under
pandas 3 that row is inferred as `str` dtype — which turns the `None` into
`NaN`. `NaN` is truthy, so `if not row["rta_isin"]: continue` does **not** fire,
and the next line reaches `re.match(nan)`:

```
TypeError: expected string or bytes-like object, got 'float'
```

Reproduced on a frame with the exact column shape `load_scheme_mapping` builds
(test: *"original Rule 0 loop raises on the production frame"*). Since
`rta_isin` is unconditionally `None`, this fired on **every** run — so
`load_scheme_mapping` was raising at Rule 0 before it could reach Rules 1–4 or
the insert. The vectorised version tests eligibility with
`str.match(ISIN_PATTERN, na=False)`, so nulls are skipped as the original
`if not …` line was plainly written to do, and the rule is the documented no-op
until an RTA starts supplying ISINs.

Because of this, the before/after comparison for Rule 0 is run two ways: once
with the **unmodified** original loop against a frame whose ISINs are all
non-null (so it can run), and once against a messy frame using a copy of the
loop with a single `pd.isna()` guard added, so the *matching* logic is still
compared like-for-like. Both are strict-equal.

## Pre-existing issue noted, not fixed (out of scope)

Rule 4 assigns `df.loc[mask, [3 cols]] = unmatched_df.apply(fuzzy_match,
axis=1).values`. When `unmatched_df` is empty, `.apply(axis=1)` returns a `(0,
0)` frame and the shapes will not line up. Untouched by this phase — Rules 0/1
match exactly the same rows as before, so this is neither introduced nor made
more likely here.

## Smoke test ✅

Test lives at `refactor_log/phaseC_smoke.py`. It carries verbatim copies of the
pre-Phase-C loops as reference implementations and compares them against the new
code two ways for every case: `assert_frame_equal(check_dtype=True,
check_exact=True)` **and** equality after `normalize_for_compare()` (from
`common/etl_helpers.py`).

Run with the project interpreter — `python_scripts/venv/bin/python`
(Python 3.13, pandas 3.0.5). The system `python3` (3.10) has no pandas.

```
cd python_scripts && PYTHONPATH=$PWD ./venv/bin/python ../refactor_log/phaseC_smoke.py
→ PHASE C SMOKE: ALL CHECKS PASSED
```

### Part 1 — parity, 12/12 checks passed

**folio_nominees** (messy fixture: nulls, blanks, whitespace-only names,
numerics-as-text, an unparseable percentage, a 600-char name, a 90-char
relationship, duplicate `(source, folio_no)` pairs, a folio matching no
holding, and a nominee slot whose columns are absent entirely):

```
[PASS] flatten_nominee_rows / messy sample                  (21 rows x 11 cols)
[PASS] flatten_nominee_rows / missing nominee2+3 columns    ( 6 rows x 11 cols)
[PASS] transform_folio_nominees / end-to-end                ( 6 rows x 11 cols)
[PASS] transform_folio_nominees / no holding matches        ( 0 rows x 11 cols)
```

The end-to-end checks run the real `transform_folio_nominees` with `safe_read`
stubbed to an in-memory holdings frame (no DB), against a verbatim copy of the
original transform. `created_at` is excluded — it is `Timestamp.now()` and
differs by construction.

**scheme_mapping** (unique ISIN hit, ambiguous ISIN across two AMFI rows,
growth==idcw on one row, malformed ISIN, lowercase ISIN, null and blank ISIN,
unique name, ambiguous name, name absent from AMFI, null name, and a row Rule 0
already claimed that Rule 1 must leave alone):

```
[PASS] original Rule 0 loop raises on the production frame — the vectorised replacement does not
[PASS] vectorised Rule 0 on the production frame — no crash, no rows matched
[PASS] Rule 0 : ISIN match (unmodified original loop, no nulls)
[PASS] Rule 0 : ISIN match (nulls + blanks + malformed)
[PASS] Rule 1 : exact name match (after Rule 0)
[PASS] Rule 2 : product match (narrowed iteration)
[PASS] Rule 1 : exact name match (standalone)
[PASS] Rules 0+1 : empty AMFI master (nothing matches)
[PASS] NaN rta_isin — old loop raised TypeError; new implementation skips it cleanly
```

Resulting mapping, showing the fall-through chain working end to end:

```
rta_scheme_code normalized_scheme_name     rta_isin amfi_scheme_code mapping_source mapping_confidence
             P0           ALPHA GROWTH INF209K01234               C1     ISIN_MATCH                100
             P1        UNIQUE NAME ONE         None               C2     EXACT_NAME                 99
             P2         AMBIGUOUS NAME                          None           None               None
             P3           NO SUCH NAME    NOTANISIN             None           None               None
             P4                    NaN in209k012345             None           None               None
             P5        UNIQUE NAME TWO         None               C6     EXACT_NAME                 99
             P6         SELF SAME ISIN INF999K01119               C7     ISIN_MATCH                100
             P7        AMBIG ISIN NAME INF888K01118               C8     EXACT_NAME                 99
```

`P6` (same ISIN on growth and idcw of one row) matches on ISIN; `P7` (ISIN on
two different AMFI rows) is correctly ambiguous, skips Rule 0 and is picked up
by Rule 1.

### Part 2 — wall clock, ~50k rows

Output at 50k was also asserted strict-equal to the iterrows version, not just
timed.

```
flatten_nominee_rows  (50,000 silver rows -> 150,000 gold rows)
  iterrows (before) :    2.706 s
  vectorised (after):    0.094 s
  speed-up          :     28.9x
  50k output        : strict-equal to the iterrows version

scheme_mapping rules  (50,000 schemes x 20,000 AMFI rows)
  Rule 0 ISIN   iterrows (before) :   18.058 s
  Rule 0 ISIN   vectorised (after):    0.023 s     778.8x
  Rule 1 name   iterrows (before) :   15.803 s
  Rule 1 name   vectorised (after):    0.145 s     109.3x
  50k output        : strict-equal to the iterrows version
```

Total for the two scheme rules: **33.9 s → 0.17 s**. The scheme numbers scale
with the AMFI master too (the loops were O(rows × amfi), the replacements are
O(rows + amfi)), so the gap widens as `public.amfi_scheme_master` grows. The
Rule 0 benchmark uses non-null synthetic ISINs — the original loop cannot run on
the real all-null column at all (see above).

### Import / compile

```
compileall over gold/ mappings/ common/ silver/ bronze/ app.py   → exit 0
import gold.loader, mappings.scheme_mapping, app                 → OK
```

`gold/loader.py` (the only caller of `transform_folio_nominees`) and the
top-level `scheme_mapping.py` shim were not touched and need no change — both
new entry points are additions, and the functions they call keep their
signatures.
