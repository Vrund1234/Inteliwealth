# python_scripts/tests/test_sip_next_due_date.py
"""gold.sip.next_due_date and sip_day are derived, never ingested.

No RTA feed carries a next-due-date: CAMS WBR49 and KFIN MFSD243 both stop at
period_day / periodicity / from_date / to_date. Until now transform_sip()
wrote pd.NaT into next_due_date unconditionally, so the column was NULL on all
1404 gold rows and all 1218 master rows.

The derivation has to straddle two RTAs that are almost exactly
complementary: CAMS fills period_day (735/743) but never status (0/743);
KFIN fills status (661/661) but never period_day (0/661). So the debit day
comes from the SIP record for CAMS and from transaction history for KFIN,
and liveness comes from status for KFIN and from the date columns for CAMS.

See docs/superpowers/specs/2026-09-08-sip-next-due-date-design.md.
"""

import datetime as dt

from etl_gold_sip import (
    MIN_MODAL_DEBITS,
    modal_day,
    parse_period_days,
    project_next_due,
    resolve_sip_day,
    sip_is_live,
)

TODAY = dt.date(2026, 9, 8)          # a Tuesday


# ============================================================
# parse_period_days
# ============================================================

def test_single_day_parsed():
    assert parse_period_days("10") == [10]


def test_zero_padded_day_parsed():
    """CAMS writes '01', not '1'."""
    assert parse_period_days("01") == [1]


def test_multi_day_list_parsed():
    """SEMI_MONTHLY carries 4-5 days per month, not one. pd.to_numeric()
    coerced these to NaN, which is why sip_day was NULL on 14 rows."""
    assert parse_period_days("7,14,21,28") == [7, 14, 21, 28]


def test_multi_day_list_is_sorted_and_deduped():
    assert parse_period_days("28,1,7,14,21") == [1, 7, 14, 21, 28]
    assert parse_period_days("01,01") == [1]


def test_out_of_range_and_junk_days_dropped():
    assert parse_period_days("0") == []
    assert parse_period_days("32") == []
    assert parse_period_days("") == []
    assert parse_period_days(None) == []


# ============================================================
# modal_day  -- evidence threshold
# ============================================================

def test_modal_day_returns_most_common():
    assert modal_day({21: 56, 22: 20, 23: 17}) == 21


def test_modal_day_rejects_weak_evidence():
    """Folio 7779295505 has exactly one debit (2026-08-18) while its
    from_date declares the 15th. One row is not evidence."""
    assert modal_day({18: 1}) is None
    assert modal_day({18: 2}) is None
    assert modal_day({18: MIN_MODAL_DEBITS}) == 18


def test_modal_day_of_nothing_is_none():
    assert modal_day({}) is None


# ============================================================
# sip_is_live
# ============================================================

def test_kfin_status_decides_when_present():
    assert sip_is_live("Live Sip", None, dt.date(2099, 12, 31), None, None, TODAY)
    assert not sip_is_live("Terminated", None, dt.date(2099, 12, 31), None, None, TODAY)
    assert not sip_is_live("Expired", None, dt.date(2099, 12, 31), None, None, TODAY)


def test_live_stp_and_swp_count_as_live():
    assert sip_is_live("Live Stp", None, dt.date(2099, 12, 31), None, None, TODAY)
    assert sip_is_live("Live Swp", None, dt.date(2099, 12, 31), None, None, TODAY)


def test_cams_blank_status_falls_back_to_dates():
    """CAMS never sends status, so liveness is inferred. Validated against
    KFIN's declared status: 238 true positives, 0 false positives."""
    assert sip_is_live("", None, dt.date(2099, 12, 1), None, None, TODAY)
    assert not sip_is_live("", dt.date(2022, 4, 30), dt.date(2099, 12, 1), None, None, TODAY)
    assert not sip_is_live("", None, dt.date(2025, 7, 7), None, None, TODAY)


def test_missing_end_date_means_no_end_not_dead():
    """14 rows have neither to_date nor cease_date, and 8 of them debited in
    the last 3 months. Treating NULL as 'finished' dropped 13 live SIPs."""
    assert sip_is_live("", None, None, None, None, TODAY)


def test_paused_sip_is_not_live():
    assert not sip_is_live(
        "Live Sip", None, dt.date(2099, 12, 31),
        dt.date(2026, 8, 1), dt.date(2026, 12, 1), TODAY,
    )


def test_pause_window_that_has_ended_does_not_suppress():
    assert sip_is_live(
        "Live Sip", None, dt.date(2099, 12, 31),
        dt.date(2025, 1, 1), dt.date(2025, 6, 1), TODAY,
    )


# ============================================================
# resolve_sip_day
# ============================================================

def _resolve(**kw):
    base = dict(
        periodicity="MONTHLY", period_day=None, modal_dom_recent=None,
        modal_dom_all=None, modal_dow=None, source="KFIN",
        to_date=None, from_date=None, recent_days=None,
    )
    base.update(kw)
    return resolve_sip_day(**base)


def test_period_day_wins_when_present():
    assert _resolve(source="CAMS", period_day="01", modal_dom_recent=9) == ("dom", [1], "period_day")


def test_recent_modal_preferred_over_all_history():
    """All-history is systematically +1 on KFIN (legacy T+1 settlement).
    On 11 live SIPs recent matched day(from_date) and all-history did not."""
    assert _resolve(modal_dom_recent=1, modal_dom_all=2) == ("dom", [1], "modal_day_recent")


def test_all_history_used_when_no_recent_debits():
    assert _resolve(modal_dom_all=16) == ("dom", [16], "modal_day_all")


def test_cams_falls_back_to_to_date_day():
    """to_date's day matches period_day on 478/523 CAMS monthly rows."""
    assert _resolve(source="CAMS", to_date=dt.date(2031, 12, 25)) == ("dom", [25], "to_date")


def test_sentinel_to_date_is_not_a_day_source():
    """146 KFIN rows carry 2099-12-31; day 31 there is an artifact."""
    got = _resolve(source="CAMS", to_date=dt.date(2099, 12, 31), from_date=dt.date(2016, 3, 15))
    assert got == ("dom", [15], "from_date")


def test_kfin_never_uses_to_date_day():
    """KFIN to_date day agrees only 47% of the time."""
    got = _resolve(source="KFIN", to_date=dt.date(2030, 5, 23), from_date=dt.date(2017, 6, 21))
    assert got == ("dom", [21], "from_date")


def test_from_date_is_last_resort():
    assert _resolve(from_date=dt.date(2021, 12, 15)) == ("dom", [15], "from_date")


def test_unresolvable_returns_none():
    assert _resolve() == (None, [], "unresolved")


def test_weekly_ignores_period_day_entirely():
    """period_day is 2 on nearly every weekly SIP, yet debits land on every
    weekday. It matched the observed weekday 39/198 (CAMS) and 0/185 (KFIN)."""
    got = _resolve(periodicity="WEEKLY", period_day="2", modal_dow=5)
    assert got == ("dow", [5], "modal_weekday")


def test_weekly_falls_back_to_from_date_weekday():
    """CAMS weeklies have no ft_sip_regno, so they always land here."""
    got = _resolve(periodicity="WEEKLY", period_day="2", from_date=dt.date(2026, 1, 16))
    assert got == ("dow", [5], "from_date_weekday")     # 2026-01-16 is a Friday


# ============================================================
# project_next_due
# ============================================================

def _project(kind, days, periodicity="MONTHLY", from_date=None, today=TODAY):
    return project_next_due(kind, days, periodicity, from_date, today)


def test_monthly_day_already_passed_rolls_to_next_month():
    assert _project("dom", [1]) == dt.date(2026, 10, 1)


def test_monthly_day_still_ahead_stays_in_month():
    assert _project("dom", [21]) == dt.date(2026, 9, 21)


def test_monthly_day_equal_to_today_is_today():
    assert _project("dom", [8]) == dt.date(2026, 9, 8)


def test_day_thirty_one_clamps_to_month_length():
    """A 31st SIP in a shorter month debits on the last day of that month."""
    assert _project("dom", [31], today=dt.date(2026, 2, 1)) == dt.date(2026, 2, 28)
    assert _project("dom", [31], today=dt.date(2026, 4, 1)) == dt.date(2026, 4, 30)


def test_day_thirty_one_clamps_to_february_29_in_leap_year():
    assert _project("dom", [31], today=dt.date(2028, 2, 1)) == dt.date(2028, 2, 29)


def test_day_thirty_clamps_in_february():
    assert _project("dom", [30], today=dt.date(2026, 2, 1)) == dt.date(2026, 2, 28)


def test_quarterly_phase_anchored_on_from_date():
    """from_date 2026-04-01 means Apr/Jul/Oct/Jan. Counting 3 months from the
    current month instead would give 2026-12-01."""
    got = _project("dom", [1], periodicity="QUARTERLY", from_date=dt.date(2026, 4, 1))
    assert got == dt.date(2026, 10, 1)


def test_half_yearly_and_yearly_steps():
    assert _project("dom", [10], "HALF_YEARLY", dt.date(2026, 1, 10)) == dt.date(2027, 1, 10)
    assert _project("dom", [10], "YEARLY", dt.date(2026, 1, 10)) == dt.date(2027, 1, 10)


def test_day_list_picks_earliest_upcoming():
    got = _project("dom", [7, 14, 21, 28], periodicity="SEMI_MONTHLY")
    assert got == dt.date(2026, 9, 14)


def test_weekly_projects_to_next_matching_weekday():
    """Friday = ISO 5; today is Tuesday 2026-09-08."""
    assert _project("dow", [5], periodicity="WEEKLY") == dt.date(2026, 9, 11)


def test_weekly_same_weekday_as_today_moves_a_week():
    assert _project("dow", [2], periodicity="WEEKLY") == dt.date(2026, 9, 15)


def test_future_start_date_is_never_preceded():
    """A SIP starting 15-Oct must not be given a 15-Sep due date."""
    got = _project("dom", [15], from_date=dt.date(2026, 10, 15))
    assert got == dt.date(2026, 10, 15)


def test_future_start_weekly_is_never_preceded():
    got = _project("dow", [5], periodicity="WEEKLY", from_date=dt.date(2026, 10, 15))
    assert got == dt.date(2026, 10, 16)      # first Friday on/after 15 Oct


def test_one_time_sip_uses_start_date_only():
    assert _project("dom", [29], "ONE_TIME", dt.date(2026, 12, 29)) == dt.date(2026, 12, 29)
    assert _project("dom", [29], "ONE_TIME", dt.date(2017, 12, 29)) is None


def test_unresolved_day_projects_to_nothing():
    assert _project(None, []) is None


# ============================================================
# pandas NaT tolerance
# ============================================================
#
# Missing dates arrive from the dataframe as pd.NaT, not None. NaT is
# truthy, is not None, and raises TypeError when compared to a date, so
# every date argument has to be normalised before it is used.

def test_sip_is_live_treats_nat_end_date_as_no_end_date():
    import pandas as pd
    assert sip_is_live("", None, pd.NaT, None, None, TODAY)


def test_sip_is_live_treats_nat_cease_date_as_absent():
    import pandas as pd
    assert sip_is_live("", pd.NaT, dt.date(2099, 12, 1), None, None, TODAY)


def test_sip_is_live_treats_nat_pause_dates_as_absent():
    import pandas as pd
    assert sip_is_live("Live Sip", None, dt.date(2099, 12, 1), pd.NaT, pd.NaT, TODAY)


def test_resolve_sip_day_treats_nat_dates_as_absent():
    import pandas as pd
    got = resolve_sip_day(
        periodicity="MONTHLY", period_day=None, modal_dom_recent=None,
        modal_dom_all=None, modal_dow=None, source="CAMS",
        to_date=pd.NaT, from_date=pd.NaT,
    )
    assert got == (None, [], "unresolved")


def test_project_next_due_treats_nat_from_date_as_absent():
    import pandas as pd
    assert project_next_due("dom", [21], "MONTHLY", pd.NaT, TODAY) == dt.date(2026, 9, 21)


# ============================================================
# pandas NA tolerance (string columns)
# ============================================================
#
# Text columns are pandas "string" dtype, so a missing value is pd.NA, and
# `pd.NA or ""` raises "boolean value of NA is ambiguous".

def test_sip_is_live_treats_na_status_as_blank():
    import pandas as pd
    assert sip_is_live(pd.NA, None, dt.date(2099, 12, 1), None, None, TODAY)


def test_resolve_sip_day_treats_na_text_as_absent():
    import pandas as pd
    got = resolve_sip_day(
        periodicity=pd.NA, period_day=pd.NA, modal_dom_recent=None,
        modal_dom_all=None, modal_dow=None, source=pd.NA,
        to_date=None, from_date=dt.date(2021, 12, 15),
    )
    assert got == ("dom", [15], "from_date")


def test_parse_period_days_handles_na():
    import pandas as pd
    assert parse_period_days(pd.NA) == []


def test_project_next_due_treats_na_periodicity_as_monthly():
    import pandas as pd
    assert project_next_due("dom", [21], pd.NA, None, TODAY) == dt.date(2026, 9, 21)


# ============================================================
# declared start day vs a holiday-shifted mode
# ============================================================

def test_start_day_preferred_when_it_appears_among_recent_debits():
    """Folio 910106525876: recent debits scatter over 10, 11, 12 and 13. The
    mode is 11, but from_date declares the 10th and day 10 is itself a debit
    day -- the mode landed on a shifted date, not on the schedule."""
    got = _resolve(
        modal_dom_recent=11,
        recent_days={10: 3, 11: 4, 12: 1, 13: 2},
        from_date=dt.date(2025, 3, 10),
    )
    assert got == ("dom", [10], "from_date_confirmed")


def test_mode_kept_when_start_day_absent_from_recent_debits():
    """Folio 91040163442: from_date says 15 but recent debits are 28 and 29.
    The SIP's day moved during its life, so from_date is stale."""
    got = _resolve(
        modal_dom_recent=29,
        recent_days={29: 4, 28: 2},
        from_date=dt.date(2023, 12, 15),
    )
    assert got == ("dom", [29], "modal_day_recent")


def test_declared_period_day_still_outranks_debit_history():
    """CAMS must be untouched by this: period_day is authoritative there."""
    got = _resolve(
        source="CAMS", period_day="15",
        modal_dom_recent=17, recent_days={17: 5, 20: 1},
        from_date=dt.date(2016, 3, 20),
    )
    assert got == ("dom", [15], "period_day")
