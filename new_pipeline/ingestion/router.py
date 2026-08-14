"""Classify a file into an entity.

An unrecognised file is returned as a typed Unrouted result, never a print() and a
`continue`. The existing pipeline prints "Unknown file type" to a terminal the
Streamlit user never sees and reports the extraction as successful.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.file_patterns import CompiledPattern, compiled_patterns
from ingestion.reader import UnsupportedFormat, detect_format
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Routed:
    path: Path
    entity: str
    fmt: str
    report_variant: str


@dataclass(frozen=True)
class Unrouted:
    path: Path
    reason: str


def route_one(path: Path, patterns: list[CompiledPattern] | None = None) -> Routed | Unrouted:
    patterns = patterns or compiled_patterns()
    name = path.name.lower()

    try:
        fmt = detect_format(path)
    except UnsupportedFormat as exc:
        return Unrouted(path, str(exc))

    for pattern in patterns:
        if not pattern.regex.search(name):
            continue
        if fmt not in pattern.formats:
            return Unrouted(
                path,
                f"matched entity {pattern.entity} but format {fmt!r} is not one of "
                f"{list(pattern.formats)}",
            )
        return Routed(path, pattern.entity, fmt, pattern.report_variant)

    return Unrouted(
        path,
        f"filename matches no pattern in FILE_PATTERNS "
        f"(tried {[p.entity for p in patterns]})",
    )


def route_all(paths: list[Path]) -> tuple[list[Routed], list[Unrouted]]:
    patterns = compiled_patterns()
    routed: list[Routed] = []
    unrouted: list[Unrouted] = []

    for path in sorted(paths):
        result = route_one(path, patterns)
        if isinstance(result, Routed):
            routed.append(result)
            log.info("routed %s -> %s (variant %s)", path.name, result.entity,
                     result.report_variant)
        else:
            unrouted.append(result)
            log.error("UNROUTED %s: %s", path.name, result.reason)

    return routed, unrouted


def discover(directory: Path) -> list[Path]:
    """Every candidate file in a directory, skipping editor lock files.

    LibreOffice leaves `.~lock.<name>#` beside an open document; one is currently
    present in files/excel/. Those are not data.
    """
    return [
        p for p in sorted(directory.iterdir())
        if p.is_file() and not p.name.startswith(".~lock") and not p.name.startswith("~$")
    ]
