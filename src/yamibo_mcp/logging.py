from __future__ import annotations

import logging

from yamibo_mcp.web.log_buffer import get_log_buffer


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    get_log_buffer()
