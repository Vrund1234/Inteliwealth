"""Compares a parsed SchemeKey against a candidate's own structured
plan_type/option_type columns (public.scheme_master, public.amfi_scheme_master).

This is corroboration, not parsing: the regex-based SchemeKey in scheme_key.py
remains the primary source of plan/option for every rule. This module only
answers "do the two signals disagree" -- callers decide what to do with that
answer (Rule 2.5 refuses the match; the Phase-3 review queue just surfaces it
to a human). See docs/superpowers/specs/2026-08-21-scheme-master-plan-option-type-mapping-rules.md.
"""


def _is_blank(value):
    """True for None, whitespace-only strings, and pandas/NumPy NaN.

    A bare `if value:` is not enough: a missing SQL value read through pandas
    comes back as a float NaN, and `bool(float("nan"))` is True -- that alone
    let a blank plan_type/option_type fall through as the literal string
    "NAN" and register as a mismatch against every real value. Checking
    `value != value` (true only for NaN) catches it without importing pandas
    into this otherwise dependency-free module.
    """
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    return not str(value).strip()


def plan_option_mismatch(rta_key, plan_type, option_type):
    """Reason string if rta_key disagrees with plan_type/option_type, else None.

    A blank plan_type/option_type ("no structured signal yet" -- most
    scheme_master rows are still unbackfilled) is never treated as a specific
    value, so it can never itself cause a mismatch. rta_key=None (an empty or
    unparseable name) is likewise never flagged -- there is nothing to compare.
    """
    if rta_key is None:
        return None

    reasons = []

    sm_plan = "" if _is_blank(plan_type) else str(plan_type).strip().upper()
    if sm_plan and sm_plan != rta_key.plan:
        reasons.append(f"PLAN_MISMATCH(rta={rta_key.plan},sm={sm_plan})")

    sm_option = "" if _is_blank(option_type) else str(option_type).strip().upper()
    if sm_option and sm_option != rta_key.option:
        reasons.append(f"OPTION_MISMATCH(rta={rta_key.option},sm={sm_option})")

    return "; ".join(reasons) if reasons else None
