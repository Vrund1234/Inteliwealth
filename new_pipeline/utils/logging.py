"""Logging setup.

The existing pipeline has no logging framework — every diagnostic is a print() to a
terminal the Streamlit user never sees, which is why five serious data defects went
unnoticed. Everything here goes through logging so it can be redirected, filtered
and captured in CI.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once. Safe to call repeatedly."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # SQLAlchemy's own INFO logging echoes every statement; keep it at WARNING
    # unless someone explicitly asks for DEBUG.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
