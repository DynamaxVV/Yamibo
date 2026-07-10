from __future__ import annotations

import logging
import os
import sys

from yamibo_mcp.structured_logging import StructuredJSONFormatter
from yamibo_mcp.web_fastapi.log_buffer import get_log_buffer

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_log_level() -> int:
    raw = os.environ.get("YAMIBO_LOG_LEVEL", "").strip().upper()
    if raw in _LOG_LEVELS:
        return _LOG_LEVELS[raw]
    return logging.INFO


def configure_logging(level: int | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level if level is not None else _resolve_log_level())

    if not any(
        getattr(handler, "_yamibo_logging_handler", False) and isinstance(handler, logging.StreamHandler)
        for handler in root.handlers
    ):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler._yamibo_logging_handler = True  # type: ignore[attr-defined]
        console_handler.setFormatter(StructuredJSONFormatter())
        root.addHandler(console_handler)

    get_log_buffer()
