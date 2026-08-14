"""Shape validation, applied before anything is written.

This is the layer the existing pipeline does not have. There, a required column
missing from an incoming file becomes a silently NULL database column: 45 KFin
columns are lost this way today, including PAN, date of birth, mobile and every
nominee-1 field, and nothing anywhere reports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from bronze.cleaners import normalize_header
from config.mapping_cams_wbr import required_columns, source_to_target
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ValidationReport:
    entity: str
    file_name: str
    matched: dict[str, str] = field(default_factory=dict)      # source -> target
    missing_required: list[str] = field(default_factory=list)   # target names
    unmapped_in_file: list[str] = field(default_factory=list)   # normalised source names

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.unmapped_in_file

    def describe(self) -> str:
        parts = [
            f"{self.file_name} [{self.entity}]: "
            f"{len(self.matched)} columns matched"
        ]
        if self.missing_required:
            parts.append(f"MISSING REQUIRED: {self.missing_required}")
        if self.unmapped_in_file:
            parts.append(f"UNMAPPED IN FILE: {self.unmapped_in_file}")
        return " | ".join(parts)


def validate_columns(frame: pd.DataFrame, mapping: dict, entity: str,
                     file_name: str) -> ValidationReport:
    """Compare a file's headers against the mapping, both sides normalised identically.

    Using one function on both sides is the fix for the defect that costs the existing
    pipeline 45 columns: there, headers get " " -> "_" and mapping aliases get only
    .lower().strip(), so any alias containing a space can never match.
    """
    file_columns = {
        normalize_header(c): c
        for c in frame.columns
        if c != "__row_number__"
    }

    wanted = {normalize_header(src): tgt for src, tgt in source_to_target(mapping).items()}

    matched = {
        file_columns[norm]: target
        for norm, target in wanted.items()
        if norm in file_columns
    }

    matched_norms = {normalize_header(src) for src in matched}
    required = set(required_columns(mapping))

    missing_required = sorted(
        target for norm, target in wanted.items()
        if target in required and norm not in file_columns
    )
    unmapped_in_file = sorted(norm for norm in file_columns if norm not in matched_norms)

    report = ValidationReport(
        entity=entity,
        file_name=file_name,
        matched=matched,
        missing_required=missing_required,
        unmapped_in_file=unmapped_in_file,
    )

    if report.ok:
        log.info(report.describe())
    else:
        log.error(report.describe())

    return report
