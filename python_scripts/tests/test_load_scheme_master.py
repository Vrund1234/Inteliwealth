"""load_scheme_master must select plan_type/option_type alongside the
existing columns -- Rule 2.5 cannot corroborate against columns it never
loaded."""

import pandas as pd

import scheme_mapping as mod


class TestLoadSchemeMaster:
    def test_selects_plan_type_and_option_type(self, monkeypatch):
        seen_query = {}

        def fake_read_sql(query, conn_engine):
            seen_query["text"] = query
            return pd.DataFrame(
                {
                    "scheme_code": ["120465"],
                    "scheme_name": ["Axis Large Cap Fund"],
                    "name_norm": ["AXIS LARGE CAP FUND"],
                    "category": ["Equity"],
                    "isin_growth": ["INF846K01DP8"],
                    "isin_reinvest": [None],
                    "plan_type": ["DIRECT"],
                    "option_type": ["GROWTH"],
                }
            )

        monkeypatch.setattr(mod.pd, "read_sql", fake_read_sql)

        df = mod.load_scheme_master(conn_engine=None)

        assert "plan_type" in seen_query["text"]
        assert "option_type" in seen_query["text"]
        assert df.at[0, "plan_type"] == "DIRECT"
        assert df.at[0, "option_type"] == "GROWTH"

    def test_scheme_code_is_stringified_and_stripped(self, monkeypatch):
        def fake_read_sql(query, conn_engine):
            return pd.DataFrame(
                {
                    "scheme_code": [" 120465 "],
                    "scheme_name": ["Axis Large Cap Fund"],
                    "name_norm": ["AXIS LARGE CAP FUND"],
                    "category": ["Equity"],
                    "isin_growth": ["INF846K01DP8"],
                    "isin_reinvest": [None],
                    "plan_type": ["DIRECT"],
                    "option_type": ["GROWTH"],
                }
            )

        monkeypatch.setattr(mod.pd, "read_sql", fake_read_sql)

        df = mod.load_scheme_master(conn_engine=None)

        assert df.at[0, "scheme_code"] == "120465"
