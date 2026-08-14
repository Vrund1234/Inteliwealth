"""Gold orchestrator.

Fails fast. The existing gold_loader wraps each of its eight entities in its own
`try/except Exception: print(e)`, so a failed gold.scheme lets scheme_nav,
transactions and holdings join a stale dimension while the UI still reports
"Transformation Completed". Here a failure stops the run and propagates, and every
entity's outcome is recorded in audit_wbr.load_summary either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import Settings
from gold import reports
from gold.exporter import ExportResult, export_all
from utils.logging import get_logger

log = get_logger(__name__)

# Explicit dependency order. Nothing here depends on anything else yet, but the order
# is declared rather than implied so adding a dependent entity has an obvious place.
LOAD_ORDER = ["brokerage_by_scheme", "investor_kyc_status", "invalid_euin"]

TABLE_TO_REPORTS = {
    "brokerage_by_scheme": ["WBR36", "WBR36H"],
    "investor_kyc_status": ["WBR56"],
    "invalid_euin": ["WBR68"],
}


@dataclass
class GoldRunResult:
    loaded: list[reports.GoldResult] = field(default_factory=list)
    exported: list[ExportResult] = field(default_factory=list)

    def describe(self) -> str:
        lines = ["gold tables:"]
        for item in self.loaded:
            lines.append(
                f"  {item.table}: {item.rows_out} rows "
                f"(grain {item.grain_rows}/{item.grain_keys}, ratio {item.grain_ratio:.2f})"
            )
        if self.exported:
            lines.append("reports:")
            for item in self.exported:
                names = ", ".join(p.name for p in item.files)
                lines.append(f"  {item.report_code}: {item.rows} rows -> {names}")
        return "\n".join(lines)


def load_gold(
    engine,
    settings: Settings,
    report_period: str,
    tables: list[str] | None = None,
    export: bool = True,
    formats: tuple[str, ...] = ("xlsx", "csv"),
) -> GoldRunResult:
    targets = [t for t in LOAD_ORDER if tables is None or t in tables]
    result = GoldRunResult()

    for table in targets:
        log.info("=" * 70)
        log.info("gold: %s", table)

        if table == "brokerage_by_scheme":
            frame = reports.extract_brokerage(engine, settings)
            frame = reports.transform_brokerage(frame, report_period)
            result.loaded.append(reports.load_brokerage(engine, settings, frame))

        elif table == "investor_kyc_status":
            frame = reports.extract_kyc(engine, settings)
            frame = reports.transform_kyc(frame)
            result.loaded.append(reports.load_kyc(engine, settings, frame))

        elif table == "invalid_euin":
            frame = reports.extract_euin(engine, settings)
            frame = reports.transform_euin(frame)
            result.loaded.append(reports.load_euin(engine, settings, frame))

        else:  # pragma: no cover - guarded by LOAD_ORDER
            raise ValueError(f"unknown gold table {table!r}")

    if export:
        codes = [code for table in targets for code in TABLE_TO_REPORTS[table]]
        result.exported = export_all(engine, settings, codes, formats)

    return result


if __name__ == "__main__":  # pragma: no cover
    from utils.db import get_engine
    from utils.logging import setup_logging

    _settings = Settings()
    setup_logging(_settings.runtime.log_level)
    print(load_gold(get_engine(_settings), _settings, report_period="UNSPECIFIED").describe())
