"""CAMS WBR reports, derived from silver.

The reports are OUTPUT: there is no WBR input file. WBR36 and WBR68 are built
from silver.transaction_master_new, WBR56 from silver.investor_master.

Most of these tests pin a defect that reached the database once — a filter that
turned 406 invalid-EUIN transactions into 44,299, a scheme code carrying the AMC
letter the report does not want, a literal "0" standing in for an absent folio,
and an email column filled from the wrong party entirely.
"""

from pathlib import Path

import pandas as pd
import pytest

from etl_gold_wbr import (
    KFIN_ONLY_COLUMNS,
    NATURAL_KEYS,
    UNAVAILABLE,
    blank_zero,
    compound,
    extract_invalid_euin,
    resolve_report_period,
    stable_uuid,
    strip_amc_prefix,
    transform_brokerage_by_scheme,
    transform_invalid_euin,
    transform_investor_kyc_status,
)
from export_wbr import export_wbr_reports
from mapping import WBR_OUTPUT_DATE_FORMATS, WBR_OUTPUT_LAYOUTS
from utils.db import engine

# The provider's own reports. Not inputs — they are the reference for what the
# derived output is meant to look like.
REFERENCE_DIR = Path("/home/user/Inteliwealth-pipeline/files/gold")

REFERENCE_FILES = {
    "WBR36": "WBR36-Brokerage summary by scheme.xls",
    "WBR36H": "WBR36H-Brokerage summary by scheme.xls",
    "WBR56": "WBR56-KYC status of Investor.xls",
    "WBR68": "WBR68-Invalid EUIN Report.xls",
}


def table_exists(schema, table):
    return bool(
        pd.read_sql(
            f"""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            """,
            engine,
        ).shape[0]
    )


def row_count(schema, table):
    return int(
        pd.read_sql(f"SELECT count(*) AS n FROM {schema}.{table}", engine)["n"].iloc[0]
    )


def reference(report):
    path = REFERENCE_DIR / REFERENCE_FILES[report]
    if not path.exists():
        pytest.skip(f"reference report not available: {path}")
    frame = pd.read_excel(path, dtype=str, keep_default_na=False)
    frame.columns = [c.strip().lower() for c in frame.columns]
    return frame


@pytest.fixture(scope="module")
def gold_loaded():
    for table in NATURAL_KEYS:
        if not table_exists("gold", table):
            pytest.skip(f"gold.{table} does not exist — run sql/wbr_gold_tables.sql")
        if not row_count("gold", table):
            pytest.skip(f"gold.{table} is empty — run etl_gold_wbr.py")
    return True


# =====================================================
# HELPERS
# =====================================================

class TestHelpers:
    def test_amc_prefix_is_stripped_from_the_scheme_code(self):
        """The report wants 51, R2 delivers B51. Verified against all 9 rows of
        the WBR68 reference: B51/51, G201/201, TSCFG/SCFG."""
        assert strip_amc_prefix("B51", "B") == "51"
        assert strip_amc_prefix("G201", "G") == "201"
        assert strip_amc_prefix("TSCFG", "T") == "SCFG"

    def test_a_code_that_only_looks_prefixed_is_left_alone(self):
        assert strip_amc_prefix("BB", "X") == "BB"
        assert strip_amc_prefix("B", "B") == "B"
        assert strip_amc_prefix(None, "B") is None

    def test_zero_means_absent_not_zero(self):
        """R2 writes 0 into ALTFOLIO and SIPTRXNNO when there is none; the
        provider's report leaves the cell empty."""
        out = blank_zero(pd.Series(["0", "123", "", None]))
        assert pd.isna(out.iloc[0])
        assert out.iloc[1] == "123"
        assert pd.isna(out.iloc[2])

    def test_compound_uses_the_providers_own_convention(self):
        assert compound("GU", "Gujarat") == "GU/Gujarat"
        assert compound(None, "Palakkad") == "/Palakkad"
        assert compound(None, None) == "/"

    def test_ids_are_stable_across_runs(self):
        """uuid5 over the natural key. gold.holdings uses uuid4 and regenerates
        every id on every run, which breaks the rows referencing it."""
        assert stable_uuid("B", "51") == stable_uuid("B", "51")
        assert stable_uuid("B", "51") != stable_uuid("B", "52")


# =====================================================
# WBR68 - THE INVALID-EUIN FILTER
# =====================================================

class TestInvalidEuinFilter:
    def test_blank_euin_valid_is_not_an_invalid_euin(self):
        """43,894 silver rows carry an EUIN with euin_valid blank — validity is
        simply not reported for them — against 406 with an explicit non-'Y'
        verdict. Treating blank as invalid inflated the report to 44,299 rows."""
        counts = pd.read_sql(
            """
            SELECT
                count(*) FILTER (
                    WHERE btrim(coalesce(euin_valid, '')) = ''
                ) AS blank_verdict,
                count(*) FILTER (
                    WHERE btrim(coalesce(euin_valid, '')) <> ''
                    AND upper(btrim(euin_valid)) <> 'Y'
                ) AS invalid
            FROM silver.transaction_master_new
            WHERE btrim(coalesce(euin, '')) <> ''
            """,
            engine,
        )

        blank = int(counts["blank_verdict"].iloc[0])
        invalid = int(counts["invalid"].iloc[0])

        extracted = len(extract_invalid_euin())

        assert extracted == invalid
        assert blank == 0 or extracted < blank

    def test_the_filter_is_not_equals_n(self):
        """The provider's file carries both 'N' and 'F' under the same reason, so
        every test on this column is <> 'Y', never = 'N'."""
        verdicts = reference("WBR68")["euin_valid"].str.strip().unique()
        assert set(verdicts) - {"Y"} == set(verdicts)
        assert len(verdicts) > 1

    def test_every_reference_transaction_is_in_the_derived_output(self, gold_loaded):
        wanted = reference("WBR68")["trxn_no"].str.strip().tolist()

        found = pd.read_sql(
            "SELECT trxn_no FROM gold.invalid_euin WHERE trxn_no = ANY(%(w)s)",
            engine,
            params={"w": wanted},
        )

        assert len(found) == len(wanted), (
            f"{len(wanted) - len(found)} of the provider's transactions are "
            f"missing from gold.invalid_euin"
        )

    def test_reason_is_constant(self, gold_loaded):
        reasons = pd.read_sql(
            "SELECT DISTINCT reason FROM gold.invalid_euin", engine
        )["reason"].tolist()
        assert reasons == ["Invalid EUIN"]


# =====================================================
# TRANSFORMS
# =====================================================

class TestTransforms:
    def test_brokerage_is_one_row_per_scheme(self):
        frame = pd.DataFrame(
            {
                "prodcode": ["B51", "B51", "G201"],
                "scheme": ["Alpha", "Alpha", "Beta"],
                "traddate": ["2025-01-01", "2025-02-01", "2025-01-15"],
                "source": ["CAMS"] * 3,
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        assert len(out) == 2
        assert out["report_variant"].unique().tolist() == ["STD"]
        assert out["source_row"].tolist() == [1, 2]

    def test_brokerage_measures_are_null_not_zero(self):
        """Nothing in R2 sources them. NULL says "unknown"; 0.0 would claim the
        distributor earned nothing."""
        frame = pd.DataFrame(
            {
                "prodcode": ["B51"],
                "scheme": ["Alpha"],
                "traddate": ["2025-01-01"],
                "source": ["CAMS"],
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        for measure in UNAVAILABLE["WBR36"]:
            assert out[measure].isna().all(), measure

    def test_kyc_status_skips_rows_without_a_complete_natural_key(self, capsys):
        """amc_code is half the key. The KFIN feed leaves it blank, so those rows
        cannot be written — and the count has to be printed rather than
        swallowed, or 40% of the folios vanish quietly."""
        frame = pd.DataFrame(
            {
                "amc_code": ["B", None],
                "folio_no": ["1001", "1002"],
                "broker_code": ["ARN-1", "ARN-1"],
                "investor_name": ["A", "B"],
                "pan_no": ["P1", "P2"],
                "joint_name_1": [None, None],
                "joint1_pan": [None, None],
                "joint_name_2": [None, None],
                "joint2_pan": [None, None],
                "guardian_name": [None, None],
                "guardian_pan": [None, None],
                "address1": ["a", "a"],
                "address2": [None, None],
                "address3": [None, None],
                "city": ["Vadodara", "Surat"],
                "pincode": ["390001", "395001"],
                "state": ["Gujarat", "Gujarat"],
                "country": ["INDIA", "INDIA"],
                "phone_res": [None, None],
                "phone_off": [None, None],
                "mobile_no": ["9", "9"],
                "email": ["a@b.c", "d@e.f"],
                "fax_residence": [None, None],
                "fax_office": [None, None],
                "kyc1flag": [None, None],
                "kyc2flag": [None, None],
                "kyc3flag": [None, None],
                "kycgflag": [None, None],
                "holder_1_aadhaar_info": [None, None],
                "holder_2_aadhaar_info": [None, None],
                "holder_3_aadhaar_info": [None, None],
                "report_date": ["2025-07-16", "2025-07-16"],
                "source": ["CAMS", "KFIN"],
            }
        )

        out = transform_investor_kyc_status(frame)

        assert len(out) == 1
        assert "skipped" in capsys.readouterr().out

    def test_kyc_state_and_location_are_compound(self):
        frame = pd.DataFrame(
            {
                "amc_code": ["B"],
                "folio_no": ["1001"],
                "broker_code": ["ARN-1"],
                "investor_name": ["A"],
                "pan_no": ["P1"],
                "joint_name_1": [None], "joint1_pan": [None],
                "joint_name_2": [None], "joint2_pan": [None],
                "guardian_name": [None], "guardian_pan": [None],
                "address1": ["a"], "address2": [None], "address3": [None],
                "city": ["Vadodara"], "pincode": ["390001"],
                "state": ["Gujarat"], "country": ["INDIA"],
                "phone_res": [None], "phone_off": [None],
                "mobile_no": ["9"], "email": ["a@b.c"],
                "fax_residence": [None], "fax_office": [None],
                "kyc1flag": [None], "kyc2flag": [None], "kyc3flag": [None],
                "kycgflag": [None],
                "holder_1_aadhaar_info": [None],
                "holder_2_aadhaar_info": [None],
                "holder_3_aadhaar_info": [None],
                "report_date": ["2025-07-16"],
                "source": ["CAMS"],
            }
        )

        out = transform_investor_kyc_status(frame)

        assert out["state"].iloc[0] == "/Gujarat"
        assert out["location"].iloc[0] == "/Vadodara"

    def test_kyc_columns_fill_in_when_the_feed_supplies_them(self):
        """They are NULL for CAMS folios and populated for KFIN ones. The mapping
        exists so a KFIN delivery is not silently blanked too."""
        assert KFIN_ONLY_COLUMNS["fh_kyc"] == "kyc1flag"
        assert KFIN_ONLY_COLUMNS["fh_g_aadharlink"] == "holder_1_aadhaar_info"

    def test_invalid_euin_strips_the_amc_prefix_and_blanks_zeros(self):
        frame = pd.DataFrame(
            {
                "amc_code": ["B"],
                "trxnno": ["813162041"],
                "brokcode": ["ARN-266051"],
                "application_no": [None],
                "folio_no": ["1049869526"],
                "old_folio": [None],
                "altfolio": ["0"],
                "scheme_folio_number": [None],
                "inv_name": ["Suresh Kumar V"],
                "pan": ["AKWPB3091J"],
                "prodcode": ["B51"],
                "scheme": ["Bandhan Large & Mid Cap Fund"],
                "trxntype": ["P234ES"],
                "amount": ["1999.9"],
                "subbrok": [None],
                "sub_brk_arn": [None],
                "ter_location": ["B"],
                "location": ["Palakkad"],
                "usercode": ["CMAGE24375"],
                "usrtrxno": ["9966684"],
                "euin": ["E064801"],
                "euin_valid": ["N"],
                "traddate": ["2026-07-06"],
                "postdate": ["2026-07-06"],
                "sys_regn_date": ["2025-09-17"],
                "siptrxnno": ["0"],
                "source": ["CAMS"],
            }
        )

        out = transform_invalid_euin(frame)

        assert out["sch_code"].iloc[0] == "51"
        assert pd.isna(out["alt_folio"].iloc[0])
        assert pd.isna(out["auto_trxn_no"].iloc[0])
        assert out["location"].iloc[0] == "/Palakkad"
        assert out["cons_code"].iloc[0] == "ARN-266051"
        assert out["reason"].iloc[0] == "Invalid EUIN"

    def test_email_is_not_filled_from_the_investor(self):
        """The provider writes the DISTRIBUTOR's email: all 9 reference rows carry
        one address across 5 folios. Joining investor_master.email looked right
        and produced a different address on every row."""
        emails = reference("WBR68")["email"].str.strip().unique()
        folios = reference("WBR68")["folio_no"].str.strip().nunique()

        assert len(emails) == 1
        assert folios > 1
        assert "email" in UNAVAILABLE["WBR68"]


# =====================================================
# REPORT PERIOD
# =====================================================

class TestReportPeriod:
    def test_environment_override_wins(self, monkeypatch):
        monkeypatch.setenv("WBR_REPORT_PERIOD", "2019")
        assert resolve_report_period() == "2019"

    def test_period_comes_from_the_data_not_from_today(self, monkeypatch):
        monkeypatch.delenv("WBR_REPORT_PERIOD", raising=False)

        latest = pd.read_sql(
            "SELECT max(traddate) AS d FROM silver.transaction_master_new", engine
        )["d"].iloc[0]

        if latest is None or pd.isna(latest):
            pytest.skip("no trade dates in silver")

        assert resolve_report_period() == str(pd.Timestamp(latest).year)


# =====================================================
# GRAIN
# =====================================================

class TestGrain:
    @pytest.mark.parametrize("table", list(NATURAL_KEYS))
    def test_one_row_per_natural_key(self, table, gold_loaded):
        keys = ", ".join(f'"{c}"' for c in NATURAL_KEYS[table])

        result = pd.read_sql(
            f"SELECT count(*) AS rows, count(DISTINCT ({keys})) AS keys "
            f"FROM gold.{table}",
            engine,
        )

        assert int(result["rows"].iloc[0]) == int(result["keys"].iloc[0])


# =====================================================
# EXPORT
# =====================================================

@pytest.fixture(scope="module")
def exported(tmp_path_factory, gold_loaded):
    out_dir = tmp_path_factory.mktemp("wbr_export")
    results = export_wbr_reports(output_dir=str(out_dir))
    return {r["report_code"]: r for r in results}, out_dir


class TestExport:
    def test_four_layouts_over_three_tables(self, exported):
        results, _ = exported

        assert set(results) == set(REFERENCE_FILES)
        assert len({l["source_table"] for l in WBR_OUTPUT_LAYOUTS.values()}) == 3

    @pytest.mark.parametrize("report", list(REFERENCE_FILES))
    def test_column_order_matches_the_providers_file(self, report, exported):
        _, out_dir = exported
        stem = Path(REFERENCE_FILES[report]).stem

        written = pd.read_csv(
            out_dir / f"{stem}.csv", dtype=str, keep_default_na=False
        )

        assert list(written.columns) == list(reference(report).columns)
        assert list(written.columns) == WBR_OUTPUT_LAYOUTS[report]["columns"]

    def test_wbr36h_is_written_empty_rather_than_skipped(self, exported):
        """Nothing in R2 marks which schemes belong to the H variant, so it has no
        rows. An absent file looks like a failed run; an empty one with the right
        header says "no rows qualified"."""
        results, out_dir = exported

        assert results["WBR36H"]["rows"] == 0

        written = pd.read_csv(
            out_dir / f"{Path(REFERENCE_FILES['WBR36H']).stem}.csv",
            dtype=str,
            keep_default_na=False,
        )
        assert written.empty
        assert list(written.columns) == WBR_OUTPUT_LAYOUTS["WBR36H"]["columns"]

    def test_two_runs_produce_identical_files(self, tmp_path, gold_loaded):
        """ORDER BY source_row is what makes this hold. A bare SELECT * returns
        heap order, which changes after an UPDATE."""
        first, second = tmp_path / "a", tmp_path / "b"

        export_wbr_reports(formats=("csv",), output_dir=str(first))
        export_wbr_reports(formats=("csv",), output_dir=str(second))

        names = sorted(p.name for p in first.iterdir())
        assert names

        for name in names:
            assert (first / name).read_bytes() == (second / name).read_bytes(), name

    def test_dates_render_in_the_providers_formats(self, exported):
        _, out_dir = exported

        written = pd.read_csv(
            out_dir / f"{Path(REFERENCE_FILES['WBR68']).stem}.csv",
            dtype=str,
            keep_default_na=False,
        )

        assert "trade_date" in WBR_OUTPUT_DATE_FORMATS["WBR68"]

        dates = written["trade_date"][written["trade_date"] != ""]
        assert len(dates)
        # %-m/%-d/%Y — unpadded, so no leading zero survives
        assert not dates.str.match(r"^0").any()

    def test_integral_amounts_carry_no_decimal_point(self, exported):
        """numeric comes back from PostgreSQL as Decimal, and a naive str() writes
        1000.0 where the provider writes 1000."""
        _, out_dir = exported

        written = pd.read_csv(
            out_dir / f"{Path(REFERENCE_FILES['WBR68']).stem}.csv",
            dtype=str,
            keep_default_na=False,
        )

        amounts = written["amount"][written["amount"] != ""]
        assert len(amounts)
        assert not amounts.str.endswith(".0").any()
