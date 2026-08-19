"""WBR reports for CAMS and KFintech, derived from silver.

The reports are OUTPUT: there is no WBR input file. WBR36 and WBR68 are built
from silver.transaction_master_new, WBR56 from silver.investor_master.

Both RTAs land in the same three gold tables, keyed by source. The reference
files in files/gold are CAMS deliveries, so every test that compares against the
provider compares the CAMS export; the KFIN side is tested for the things that
can be asserted without a provider file — that it produces rows at all, in the
same layout, and that WBR68 produces none.

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
    SOURCES,
    UNPRODUCIBLE,
    blank_zero,
    compound,
    extract_invalid_euin,
    resolve_report_period,
    stable_uuid,
    strip_amc_prefix,
    transform_brokerage_by_scheme,
    transform_invalid_euin,
    transform_investor_kyc_status,
    unavailable_for,
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


# Every column extract_investor_kyc_status() selects, so a fixture only has to
# name the ones its assertion is about. Silver carries both feeds\' spellings
# side by side — amc_code and fund, joint1_pan and pan2 — and a test that named
# only one of each would pass on a transform that never looked at the other.

KYC_COLUMNS = [
    "amc_code", "fund", "folio_no", "broker_code", "investor_name", "pan_no",
    "joint_name_1", "joint1_pan", "joint_name_2", "joint2_pan", "pan2", "pan3",
    "guardian_name", "guardian_pan",
    "address1", "address2", "address3", "city", "pincode", "state", "country",
    "phone_res", "phone_off", "mobile_no", "email",
    "fax_residence", "fax_office",
    "kyc1flag", "kyc2flag", "kyc3flag", "kycgflag",
    "holder_1_aadhaar_info", "holder_2_aadhaar_info", "holder_3_aadhaar_info",
    "report_date", "source",
]


def kyc_frame(**columns):
    rows = len(next(iter(columns.values())))

    frame = {c: columns.get(c, [None] * rows) for c in KYC_COLUMNS}

    if "report_date" not in columns:
        frame["report_date"] = ["2025-07-16"] * rows

    return pd.DataFrame(frame)


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
                "amc_code": ["B", "B", "G"],
                "td_fund": [None, None, None],
                "scheme": ["Alpha", "Alpha", "Beta"],
                "funddesc": [None, None, None],
                "traddate": ["2025-01-01", "2025-02-01", "2025-01-15"],
                "source": ["CAMS"] * 3,
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        assert len(out) == 2
        assert out["report_variant"].unique().tolist() == ["STD"]
        assert out["source_row"].tolist() == [1, 2]

    def test_brokerage_measures_are_null_not_zero(self):
        """Neither R2 nor MFSD201 sources them. NULL says "unknown"; 0.0 would
        claim the distributor earned nothing."""
        frame = pd.DataFrame(
            {
                "prodcode": ["B51"],
                "amc_code": ["B"],
                "td_fund": [None],
                "scheme": ["Alpha"],
                "funddesc": [None],
                "traddate": ["2025-01-01"],
                "source": ["CAMS"],
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        for source in SOURCES:
            for measure in unavailable_for("WBR36", source):
                assert out[measure].isna().all(), (source, measure)

    def test_brokerage_reads_the_kfin_product_code_out_of_amc_code(self):
        """The shared ingestion mapping sends MFSD201\'s fmcode to amc_code and
        leaves prodcode empty on 26,673 KFIN rows, so the product code has to be
        read back out here. td_fund is the real AMC code and is what tells
        "128TSGP in the wrong column" from a plain AMC code of 128."""
        frame = pd.DataFrame(
            {
                "prodcode": [None, None],
                "amc_code": ["128TSGP", "128"],
                "td_fund": ["128", "128"],
                "scheme": ["TSGP", "TSGP"],
                "funddesc": ["Axis ELSS Tax Saver Fund", "Axis ELSS Tax Saver Fund"],
                "traddate": ["2025-01-01", "2025-01-01"],
                "source": ["KFIN", "KFIN"],
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        # Row two has nothing but a plain AMC code and is not invented into a
        # product; row one is recovered.
        assert out["product_code"].tolist() == ["128TSGP"]
        assert out["product_name"].iloc[0] == "Axis ELSS Tax Saver Fund"
        assert out["source"].iloc[0] == "KFIN"

    def test_brokerage_keeps_the_two_rtas_apart(self):
        """Same scheme, two RTAs, two codes. source is in the natural key, so
        both survive and each file numbers its own rows from 1."""
        frame = pd.DataFrame(
            {
                "prodcode": ["B51", "128TSGP"],
                "amc_code": ["B", "128"],
                "td_fund": [None, "128"],
                "scheme": ["Alpha", "TSGP"],
                "funddesc": [None, "Axis ELSS Tax Saver Fund"],
                "traddate": ["2025-01-01", "2025-01-01"],
                "source": ["CAMS", "KFIN"],
            }
        )

        out = transform_brokerage_by_scheme(frame, report_period="2025")

        assert len(out) == 2
        assert sorted(out["source"]) == ["CAMS", "KFIN"]
        assert out["source_row"].tolist() == [1, 1]
        assert out["id"].nunique() == 2

    def test_kyc_status_reads_the_kfin_amc_code_out_of_fund(self):
        """MFSD211 has no AMC_CODE column; its Fund column is the same thing and
        the shared ingestion mapping lands it in silver as fund. amc_code is part
        of the natural key, so without reading fund every one of the 1,444 KFIN
        folios is dropped."""
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=[None, "B"],
                fund=["128", None],
                folio_no=["1001", "1002"],
                source=["KFIN", "CAMS"],
            )
        )

        assert len(out) == 2
        assert dict(zip(out["source"], out["amc_code"])) == {
            "KFIN": "128",
            "CAMS": "B",
        }

    def test_kyc_status_skips_rows_without_a_complete_natural_key(self, capsys):
        """A row with neither amc_code nor fund cannot be keyed. The count is
        printed rather than swallowed."""
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=["B", None],
                folio_no=["1001", "1002"],
                source=["CAMS", "CAMS"],
            )
        )

        assert len(out) == 1
        assert "skipped" in capsys.readouterr().out

    def test_kyc_joint_pans_come_from_whichever_column_the_feed_uses(self):
        """CAMS R9 writes JOINT1_PAN / JOINT2_PAN; KFIN MFSD211 writes PAN2 /
        PAN3 for the same two holders. Neither feed fills the other's column."""
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=["B", None],
                fund=[None, "128"],
                folio_no=["1001", "1002"],
                source=["CAMS", "KFIN"],
                joint1_pan=["CAMSPAN1", None],
                joint2_pan=["CAMSPAN2", None],
                pan2=[None, "KFINPAN1"],
                pan3=[None, "KFINPAN2"],
            )
        ).set_index("source")

        assert out.loc["CAMS", "jointpan1"] == "CAMSPAN1"
        assert out.loc["CAMS", "jointpan2"] == "CAMSPAN2"
        assert out.loc["KFIN", "jointpan1"] == "KFINPAN1"
        assert out.loc["KFIN", "jointpan2"] == "KFINPAN2"

    def test_kyc_reporting_window_is_per_rta(self):
        """The window is the span the delivery covers. The two RTAs deliver on
        their own dates, so one shared window would print the CAMS span on the
        KFIN file."""
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=["B", None],
                fund=[None, "128"],
                folio_no=["1001", "1002"],
                source=["CAMS", "KFIN"],
                report_date=["2025-01-31", "2026-07-15"],
            )
        ).set_index("source")

        assert str(out.loc["CAMS", "rep_from_date"]) == "2025-01-31"
        assert str(out.loc["KFIN", "rep_from_date"]) == "2026-07-15"

    def test_kyc_rows_are_kept_apart_by_rta(self):
        """The same AMC code and folio number from two RTAs are two folios, not
        one. source leads the natural key for exactly this."""
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=["128", "128"],
                folio_no=["1001", "1001"],
                source=["CAMS", "KFIN"],
            )
        )

        assert len(out) == 2
        assert out["id"].nunique() == 2
        assert out["source_row"].tolist() == [1, 1]

    def test_kyc_state_and_location_are_compound(self):
        out = transform_investor_kyc_status(
            kyc_frame(
                amc_code=["B"],
                folio_no=["1001"],
                source=["CAMS"],
                city=["Vadodara"],
                state=["Gujarat"],
            )
        )

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
        assert "email" in unavailable_for("WBR68", "CAMS")

    def test_wbr68_cannot_be_produced_from_kfin_at_all(self):
        """MFSD201 has no euin, euin_valid or euin_opted column, so there is no
        verdict to filter on. That is a missing report, not a report with empty
        columns, and it is recorded as one."""
        assert ("WBR68", "KFIN") in UNPRODUCIBLE
        assert unavailable_for("WBR68", "KFIN") == {}


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

# The reference files are CAMS deliveries, so the provider comparisons run
# against the CAMS export. Filenames carry the RTA now — two RTAs writing the
# provider\'s four stems into one directory would overwrite each other.

@pytest.fixture(scope="module")
def exported(tmp_path_factory, gold_loaded):
    out_dir = tmp_path_factory.mktemp("wbr_export")
    results = export_wbr_reports(output_dir=str(out_dir), sources=["CAMS"])
    return {r["report_code"]: r for r in results}, out_dir


@pytest.fixture(scope="module")
def exported_kfin(tmp_path_factory, gold_loaded):
    out_dir = tmp_path_factory.mktemp("wbr_export_kfin")
    results = export_wbr_reports(output_dir=str(out_dir), sources=["KFIN"])
    return {r["report_code"]: r for r in results}, out_dir


def written_csv(out_dir, report, source):
    stem = Path(REFERENCE_FILES[report]).stem

    return pd.read_csv(
        out_dir / f"{stem}-{source}.csv", dtype=str, keep_default_na=False
    )


class TestExport:
    def test_four_layouts_over_three_tables(self, exported):
        results, _ = exported

        assert set(results) == set(REFERENCE_FILES)
        assert len({l["source_table"] for l in WBR_OUTPUT_LAYOUTS.values()}) == 3

    @pytest.mark.parametrize("report", list(REFERENCE_FILES))
    def test_column_order_matches_the_providers_file(self, report, exported):
        _, out_dir = exported

        written = written_csv(out_dir, report, "CAMS")

        assert list(written.columns) == list(reference(report).columns)
        assert list(written.columns) == WBR_OUTPUT_LAYOUTS[report]["columns"]

    def test_wbr36h_is_written_empty_rather_than_skipped(self, exported):
        """Nothing in R2 marks which schemes belong to the H variant, so it has no
        rows. An absent file looks like a failed run; an empty one with the right
        header says "no rows qualified"."""
        results, out_dir = exported

        assert results["WBR36H"]["rows"] == 0

        written = written_csv(out_dir, "WBR36H", "CAMS")
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

        written = written_csv(out_dir, "WBR68", "CAMS")

        assert "trade_date" in WBR_OUTPUT_DATE_FORMATS["WBR68"]

        dates = written["trade_date"][written["trade_date"] != ""]
        assert len(dates)
        # %-m/%-d/%Y — unpadded, so no leading zero survives
        assert not dates.str.match(r"^0").any()

    def test_integral_amounts_carry_no_decimal_point(self, exported):
        """numeric comes back from PostgreSQL as Decimal, and a naive str() writes
        1000.0 where the provider writes 1000."""
        _, out_dir = exported

        written = written_csv(out_dir, "WBR68", "CAMS")

        amounts = written["amount"][written["amount"] != ""]
        assert len(amounts)
        assert not amounts.str.endswith(".0").any()


# =====================================================
# KFINTECH EXPORT
# =====================================================

class TestKfinExport:
    """There is no KFIN reference file, so these assert what can be checked
    without one: the layout is the same, the rows are there, and the report the
    KFIN feed cannot produce produces nothing rather than something wrong."""

    @pytest.mark.parametrize("report", list(REFERENCE_FILES))
    def test_layout_is_the_same_for_both_rtas(self, report, exported_kfin):
        _, out_dir = exported_kfin

        written = written_csv(out_dir, report, "KFIN")

        assert list(written.columns) == WBR_OUTPUT_LAYOUTS[report]["columns"]

    def test_kfin_fills_wbr36_and_wbr56(self, exported_kfin):
        results, _ = exported_kfin

        assert results["WBR36"]["rows"]
        assert results["WBR56"]["rows"]

    def test_wbr68_is_empty_for_kfin(self, exported_kfin):
        """MFSD201 carries no EUIN column, so there is no verdict to filter on.
        The file is still written, with its header, because an absent file reads
        as a failed run."""
        results, out_dir = exported_kfin

        assert results["WBR68"]["rows"] == 0
        assert written_csv(out_dir, "WBR68", "KFIN").empty

    def test_kyc_flags_are_populated_on_the_kfin_file_only(self, exported, exported_kfin):
        """The block CAMS R9 cannot fill and MFSD211 can. If both files come out
        empty here the KFIN feed has stopped delivering Kyc1Flag; if both come
        out full, something is filling the CAMS rows from the wrong place."""
        _, cams_dir = exported
        _, kfin_dir = exported_kfin

        cams = written_csv(cams_dir, "WBR56", "CAMS")
        kfin = written_csv(kfin_dir, "WBR56", "KFIN")

        for column in ("fh_kyc", "fh_g_aadharlink"):
            assert (cams[column] == "").all(), column
            assert (kfin[column] != "").any(), column

    def test_the_two_rtas_do_not_overwrite_each_others_files(
        self, exported, exported_kfin
    ):
        """The four stems are the provider's own. Without the RTA in the
        filename the second export silently replaces the first."""
        _, cams_dir = exported
        _, kfin_dir = exported_kfin

        cams_names = {p.name for p in cams_dir.iterdir()}
        kfin_names = {p.name for p in kfin_dir.iterdir()}

        assert cams_names
        assert not (cams_names & kfin_names)
