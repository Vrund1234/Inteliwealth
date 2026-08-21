"""resolve_scheme_master_exact is Rule 2.5's decision function: exact
normalized-name match against scheme_master, refused when scheme_master's own
plan_type/option_type disagree with the RTA name's parsed plan/option."""

from scheme_mapping import resolve_scheme_master_exact


class TestResolveSchemeMasterExact:
    def test_no_name_match_returns_none_none(self):
        code, reason = resolve_scheme_master_exact(
            "Some Unknown Fund - Direct Plan - Growth",
            alias_fn=None,
            sm_name_to_code={"AXIS LARGE CAP FUND DIRECT PLAN GROWTH": "120465"},
            sm_attrs_by_code={},
        )
        assert code is None
        assert reason is None

    def test_name_match_with_no_structured_signal_is_accepted(self):
        code, reason = resolve_scheme_master_exact(
            "Axis Large Cap Fund Direct Plan Growth",
            alias_fn=None,
            sm_name_to_code={"AXIS LARGE CAP FUND DIRECT PLAN GROWTH": "120465"},
            sm_attrs_by_code={"120465": {"plan_type": None, "option_type": None}},
        )
        assert code == "120465"
        assert reason is None

    def test_name_match_with_agreeing_structured_signal_is_accepted(self):
        code, reason = resolve_scheme_master_exact(
            "Axis Large Cap Fund Direct Plan Growth",
            alias_fn=None,
            sm_name_to_code={"AXIS LARGE CAP FUND DIRECT PLAN GROWTH": "120465"},
            sm_attrs_by_code={
                "120465": {"plan_type": "DIRECT", "option_type": "GROWTH"}
            },
        )
        assert code == "120465"
        assert reason is None

    def test_name_match_with_disagreeing_structured_signal_is_refused(self):
        code, reason = resolve_scheme_master_exact(
            "Axis Large Cap Fund Direct Plan Growth",
            alias_fn=None,
            sm_name_to_code={"AXIS LARGE CAP FUND DIRECT PLAN GROWTH": "120465"},
            sm_attrs_by_code={
                "120465": {"plan_type": "REGULAR", "option_type": "GROWTH"}
            },
        )
        assert code is None
        assert reason == "PLAN_MISMATCH(rta=DIRECT,sm=REGULAR)"
