"""load_rta_nav must accept whatever unique() hands it.

Reproduces a production crash: run_fallback_mapping() calls
load_rta_nav(engine, unmatched["rta_scheme_code"].dropna().unique()), and
.unique() returns a numpy ndarray, not a list. `if not rta_scheme_codes:`
evaluates that array's truthiness -- which numpy allows for 0 or 1 elements
but refuses for 2+, raising:

    ValueError: The truth value of an array with more than one element is
    ambiguous. Use a.any() or a.all()

Two elements is the ordinary case (more than one unmatched scheme), so this
crashed the very first time UNMATCHED held more than one row.
"""

import numpy as np
import pandas as pd

import map_unmatched_nav_name as mod


class TestLoadRtaNavEmptyGuard:
    def test_an_empty_array_returns_an_empty_frame_without_querying(self, monkeypatch):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("pd.read_sql should not run for an empty code list")

        monkeypatch.setattr(mod.pd, "read_sql", fail_if_called)

        df = mod.load_rta_nav(conn_engine=None, rta_scheme_codes=np.array([]))

        assert list(df.columns) == [
            "rta", "rta_scheme_code", "nav_date", "nav", "nav_round",
        ]
        assert df.empty

    def test_a_multi_code_array_does_not_raise_on_the_emptiness_check(self, monkeypatch):
        # This is the reproduction: two or more codes must not raise
        # ValueError just from evaluating truthiness of the guard. Stub
        # pd.read_sql so the real DB is never touched.
        seen_params = {}

        def fake_read_sql(query, conn_engine, params=None):
            seen_params.update(params or {})
            return pd.DataFrame(columns=["rta", "rta_scheme_code", "nav_date", "nav"])

        monkeypatch.setattr(mod.pd, "read_sql", fake_read_sql)

        df = mod.load_rta_nav(conn_engine=None, rta_scheme_codes=np.array(["A1", "B2"]))

        assert df.empty
        assert sorted(seen_params["codes"]) == ["A1", "B2"]
