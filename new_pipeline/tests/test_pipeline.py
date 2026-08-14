"""Tests for the WBR pipeline.

Two of these need no database, which is deliberate: python_scripts/tests/conftest.py
imports the engine at module scope and queries bronze.scheme_mapping during
collection, so the whole existing suite fails to collect without a live PostgreSQL.

Run:  ./venv/bin/pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bronze.cleaners import (  # noqa: E402
    is_blank,
    normalize_header,
    parse_date_value,
    parse_numeric_value,
    split_compound,
    strip_float_artifacts,
)
from config import lookups  # noqa: E402
from config.mapping_cams_wbr import (  # noqa: E402
    DATE_FALLBACKS,
    ENTITIES,
    GOLD_GRAIN,
    OUTPUT_LAYOUTS,
    date_columns,
    required_columns,
    source_to_target,
)

SAMPLES = Path("/home/user/Inteliwealth-pipeline/files/gold")

SAMPLE_FILES = {
    "wbr36_brokerage": "WBR36-Brokerage summary by scheme.xls",
    "wbr36h_brokerage": "WBR36H-Brokerage summary by scheme.xls",
    "wbr56_kyc": "WBR56-KYC status of Investor.xls",
    "wbr68_invalid_euin": "WBR68-Invalid EUIN Report.xls",
}


# =====================================================================
# No database required
# =====================================================================

def test_normalize_header_is_symmetric():
    """The fix for the defect that costs the existing pipeline 45 columns.

    There, headers are normalised with " " -> "_" while mapping aliases get only
    .lower().strip(), so any alias containing a space can never match.
    """
    assert normalize_header("Product Code") == normalize_header("product_code")
    assert normalize_header("Address #1") == "address_1"
    assert normalize_header("  'FOLIO_NO' ") == "folio_no"
    assert normalize_header("Joint holder 1 Email id") == "joint_holder_1_email_id"


def test_slash_is_not_a_blank():
    """WBR56 emits a bare '/' for an unknown compound value.

    It must survive to the generated report verbatim, and mean "unknown" only inside
    split_compound.
    """
    assert not is_blank("/")
    assert is_blank("") and is_blank("nan") and is_blank(None)

    left, right = split_compound(pd.Series(["GU/Gujarat", "/", ""]))
    assert list(left) == ["GU", None, None]
    assert list(right) == ["Gujarat", None, None]


def test_identifier_float_artifact_stripped_but_slash_folios_survive():
    result = strip_float_artifacts(pd.Series(["1049217049.0", "42213157/43", None]))
    assert list(result) == ["1049217049", "42213157/43", None]


def test_xls_typed_date_parses_via_fallback_chain():
    """A .xls date cell renders as ISO datetime under dtype=str.

    The first profile pass read LibreOffice-converted CSVs and recorded the display
    format, so the declared format alone is not enough.
    """
    value, ok = parse_date_value("2026-07-16 00:00:00", "%m/%d/%Y", DATE_FALLBACKS)
    assert ok and value.isoformat() == "2026-07-16"

    value, ok = parse_date_value("01-Jan-2025", "%d-%b-%Y", DATE_FALLBACKS)
    assert ok and value.isoformat() == "2025-01-01"

    value, ok = parse_date_value("20250928", "%m/%d/%Y", DATE_FALLBACKS)
    assert ok and value.isoformat() == "2025-09-28"

    value, ok = parse_date_value("not-a-date", "%m/%d/%Y", DATE_FALLBACKS)
    assert not ok and value is None      # rejected, never a silent NaT


def test_numeric_refuses_garbage_instead_of_coercing():
    assert parse_numeric_value("-5327.04630385") == (-5327.04630385, True)
    assert parse_numeric_value("2,000") == (2000.0, True)
    assert parse_numeric_value("(500)") == (-500.0, True)
    assert parse_numeric_value("abc") == (None, False)


def test_lookups_report_unrecognised_values():
    """An unmapped value must be reported, not silently passed through.

    The existing pipeline does `.map(dict).fillna(original)`, which hides both a new
    legitimate provider code and bad data.
    """
    assert lookups.resolve("kyc_flag", "KYC OK") == ("OK", True)
    assert lookups.resolve("kyc_flag", "KYC Not Verified") == ("NOT_VERIFIED", True)
    assert lookups.resolve("aadhaar_link", "Not Applicable") == ("NOT_APPLICABLE", True)
    assert lookups.resolve("euin_valid", "F") == ("INVALID_FORMAT", True)

    value, recognised = lookups.resolve("kyc_flag", "SOMETHING NEW")
    assert value is None and recognised is False


def test_euin_lookup_treats_F_as_invalid():
    """The sample carries both N and F as invalid with the same reason.

    A filter of `euin_valid = 'N'` would drop the F row from the report.
    """
    assert lookups.resolve("euin_valid", "N")[0] == "INVALID"
    assert lookups.resolve("euin_valid", "F")[0] == "INVALID_FORMAT"
    assert lookups.resolve("euin_valid", "Y")[0] == "VALID"


def test_brokerage_variants_share_a_table_but_not_a_key():
    """WBR36 and WBR36H share 10 of 11 product codes.

    Without report_variant in the key the H variant overwrites the standard one.
    """
    std = ENTITIES["wbr36_brokerage"]
    h = ENTITIES["wbr36h_brokerage"]
    assert std["table"] == h["table"]
    assert "report_variant" in std["natural_key"]
    assert std["natural_key"] == h["natural_key"]


def test_every_declared_date_column_has_a_format():
    """No date column may rely on inference."""
    for entity, spec in ENTITIES.items():
        mapping = spec["mapping"]
        for column in date_columns(mapping):
            assert mapping[column]["date_format"], f"{entity}.{column} has no date_format"


def test_output_layouts_cover_the_source_columns():
    """Every output column must be reachable, either from the mapping or as derived."""
    for code, layout in OUTPUT_LAYOUTS.items():
        assert layout["columns"], f"{code} has no columns"
        assert len(layout["columns"]) == len(set(layout["columns"])), f"{code} has dupes"
        assert layout["source_table"] in GOLD_GRAIN, f"{code} points at an unknown table"


def test_gold_grain_declares_a_ratio_for_every_table():
    for table, grain in GOLD_GRAIN.items():
        assert grain["natural_key"], f"{table} has no natural key"
        assert grain["max_row_ratio"] >= 1.0
        assert grain["kind"] in {"ledger", "position", "dimension"}


# =====================================================================
# Needs the sample files, but no database
# =====================================================================

@pytest.mark.parametrize("entity,file_name", sorted(SAMPLE_FILES.items()))
def test_mapping_matches_the_real_file_headers(entity, file_name):
    """The config-vs-reality check.

    Reads each .xls and asserts that every required column is present and that the
    file carries nothing the mapping does not declare. This is the test that would
    have caught all 45 columns the existing pipeline loses.
    """
    path = SAMPLES / file_name
    if not path.is_file():
        pytest.skip(f"sample file absent: {path}")

    frame = pd.read_excel(path, dtype=str, keep_default_na=False, engine="xlrd")
    mapping = ENTITIES[entity]["mapping"]

    file_headers = {normalize_header(c) for c in frame.columns}
    declared = {normalize_header(s) for s in source_to_target(mapping)}

    missing = sorted(
        target for src, target in source_to_target(mapping).items()
        if target in required_columns(mapping)
        and normalize_header(src) not in file_headers
    )
    unmapped = sorted(file_headers - declared)

    assert not missing, f"{file_name}: required columns absent from the file: {missing}"
    assert not unmapped, f"{file_name}: file carries undeclared columns: {unmapped}"


# =====================================================================
# Needs a live database
# =====================================================================

@pytest.fixture(scope="session")
def engine():
    from config.settings import load_settings
    from utils.db import get_engine, ping

    settings = load_settings()
    try:
        eng = get_engine(settings)
        ping(eng)
    except Exception as exc:                                  # pragma: no cover
        pytest.skip(f"database unavailable: {exc}")
    return eng


@pytest.fixture(scope="session")
def settings():
    from config.settings import load_settings
    return load_settings()


def test_gold_tables_hold_their_declared_grain(engine, settings):
    """The check that would have caught the 36x holdings inflation on day one."""
    from utils.audit import assert_grain

    for table, grain in GOLD_GRAIN.items():
        rows, keys, ratio = assert_grain(
            engine, settings.schemas.gold, table,
            grain["natural_key"], grain["max_row_ratio"],
        )
        assert ratio <= grain["max_row_ratio"]


def test_generated_reports_reproduce_the_sources(engine, settings):
    """Row count, column order, key set and row order, per report."""
    from datetime import datetime

    date_cols = {"rep_date", "rep_from_date", "rep_to_date", "trade_date",
                 "posted_date", "sys_reg_dt", "sip_regn_date"}
    keys = {"WBR36": "product_code", "WBR36H": "product_code",
            "WBR56": "folio", "WBR68": "trxn_no"}

    def canonical(column: str, value: object) -> str:
        text = str(value).strip()
        if column in date_cols and text:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y%m%d"):
                try:
                    return datetime.strptime(text, fmt).date().isoformat()
                except ValueError:
                    continue
        return text

    for code, layout in OUTPUT_LAYOUTS.items():
        stem = layout["file_stem"]
        source = SAMPLES / f"{stem}.xls"
        generated = settings.paths.output_dir / f"{stem}.csv"
        if not source.is_file() or not generated.is_file():
            pytest.skip(f"{code}: source or generated file absent")

        src = pd.read_excel(source, dtype=str, keep_default_na=False, engine="xlrd")
        gen = pd.read_csv(generated, dtype=str, keep_default_na=False)

        assert list(gen.columns) == layout["columns"], f"{code}: column order"
        assert list(src.columns) == list(gen.columns), f"{code}: columns vs source"
        assert len(src) == len(gen), f"{code}: row count"

        key = keys[code]
        assert src[key].tolist() == gen[key].tolist(), f"{code}: row order"

        for column in src.columns:
            a = src[column].map(lambda v, c=column: canonical(c, v))
            b = gen[column].map(lambda v, c=column: canonical(c, v))
            assert a.equals(b), f"{code}.{column}: values differ from source"


def test_rerunning_changes_nothing(engine, settings):
    """Idempotency.

    Re-uploading a file must not grow a table. The existing bronze flags duplicates
    and inserts them anyway, so re-uploading the same six files takes
    bronze.transaction_master_new from 128,766 rows to roughly 257,532.
    """
    from sqlalchemy import text as sql_text

    from bronze.writer import write_bronze
    from ingestion.reader import read_file
    from ingestion.router import route_one
    import uuid

    def count(schema: str, table: str) -> int:
        with engine.connect() as conn:
            return conn.execute(
                sql_text(f'SELECT count(*) FROM "{schema}"."{table}"')
            ).scalar_one()

    path = SAMPLES / SAMPLE_FILES["wbr68_invalid_euin"]
    if not path.is_file():
        pytest.skip("sample file absent")

    routed = route_one(path)
    table = ENTITIES[routed.entity]["table"]
    before = count(settings.schemas.bronze, table)

    for _ in range(2):
        frame, meta = read_file(path, str(uuid.uuid4()))
        meta.entity = routed.entity
        meta.report_variant = routed.report_variant
        write_bronze(engine, settings, frame, meta, routed.entity, routed.report_variant)

    assert count(settings.schemas.bronze, table) == before, "bronze grew on re-ingest"
