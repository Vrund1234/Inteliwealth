"""Filename to (entity, format) resolution.

Compiled regex, ordered most-specific-first. `wbr36h` must be tested before `wbr36`
or the H variant is misclassified — and since the two share 10 of 11 product codes,
that misclassification would silently overwrite real rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config.mapping_cams_wbr import FILE_PATTERNS

EXTENSION_TO_FORMAT = {
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "csv",
    ".txt": "csv",
}


@dataclass(frozen=True)
class CompiledPattern:
    entity: str
    regex: re.Pattern
    formats: tuple[str, ...]
    report_variant: str
    required: bool


def _specificity(entity: str, spec: dict) -> tuple[int, int]:
    """Longer, more literal patterns first."""
    return (-len(spec["pattern"]), -len(entity))


def compiled_patterns() -> list[CompiledPattern]:
    ordered = sorted(FILE_PATTERNS.items(), key=lambda kv: _specificity(kv[0], kv[1]))
    return [
        CompiledPattern(
            entity=entity,
            regex=re.compile(spec["pattern"], re.IGNORECASE),
            formats=tuple(spec["formats"]),
            report_variant=spec["report_variant"],
            required=spec["required"],
        )
        for entity, spec in ordered
    ]
