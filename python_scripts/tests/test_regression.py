# Baseline correction (2026-08-11):
#
#   Row: CAMS / B331DD
#   RTA name: "Aditya Birla Sun Life Savings Fund -  Retail - Daily IDCW -
#              Closed for FV change"
#   Old amfi_scheme_code: 105888 (ADITYA BIRLA SUN LIFE SAVINGS FUND REGULAR
#                          DAILY IDCW)
#   New amfi_scheme_code: 109108 (ADITYA BIRLA SUN LIFE SAVINGS FUND RETAIL
#                          DAILY IDCW)
#
# Evidence: the RTA scheme name explicitly says "Retail", which matches
# 109108, not the "Regular" plan (105888) the baseline previously pointed
# to. The old value came from RULE 3.5 (NAV_MATCH) coincidentally matching a
# NAV fingerprint of three identical values (10.0068, from March 2011) on a
# dormant fund — exactly the kind of weak, false-positive-prone fingerprint
# the Task 12 NAV-verification analysis flagged as unreliable. The
# structured engine's STRUCT_EXACT rule independently and correctly
# resolves this row to 109108 from the name alone.
#
# Re-derivation confirmed B331DD is the only baseline row where
# STRUCT_EXACT's unambiguous single-candidate result disagrees with the
# baseline's stored amfi_scheme_code (see task-12-fix-report.md for the
# script and output). Baseline_mappings.csv was updated for this one row
# only; all 222 other rows are unchanged and remain the regression
# contract.
def test_no_baseline_mapping_was_lost(baseline_df, current_df):
    """Every scheme matched before Phase 2 is still matched."""
    base_keys = set(zip(baseline_df.rta, baseline_df.rta_scheme_code))
    curr_keys = set(zip(current_df.rta, current_df.rta_scheme_code))
    lost = base_keys - curr_keys
    assert not lost, f"{len(lost)} mappings lost: {sorted(lost)[:10]}"


def test_no_baseline_mapping_changed_code(baseline_df, current_df):
    """Every scheme matched before Phase 2 kept the same AMFI code."""
    merged = baseline_df.merge(
        current_df,
        on=["rta", "rta_scheme_code"],
        suffixes=("_base", "_curr"),
    )
    changed = merged[merged.amfi_scheme_code_base != merged.amfi_scheme_code_curr]
    assert changed.empty, (
        f"{len(changed)} mappings changed AMFI code:\n"
        f"{changed.head(10).to_string()}"
    )


def test_baseline_is_the_expected_size(baseline_df):
    assert len(baseline_df) == 223
