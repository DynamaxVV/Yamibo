from __future__ import annotations

import json
import logging

from yamibo_mcp.logging import configure_logging
from yamibo_mcp.structured_logging import StructuredJSONFormatter, StructuredLogEvent
from yamibo_mcp.web.log_buffer import get_log_buffer


def test_structured_json_formatter_serializes_event() -> None:
    formatter = StructuredJSONFormatter()
    event = StructuredLogEvent(
        ts="2026-07-02T00:00:00Z",
        level="INFO",
        service="yamibo_mcp",
        component="tests",
        event_type="job.started",
        message="Started job",
        result="success",
        status="running",
        job_id="job-1",
        tags=["demo"],
    )
    record = logging.LogRecord("tests", logging.INFO, __file__, 1, "ignored", (), None)
    record.structured_event = event

    payload = json.loads(formatter.format(record))

    assert payload["event_type"] == "job.started"
    assert payload["job_id"] == "job-1"
    assert payload["tags"] == ["demo"]


def test_configure_logging_attaches_json_formatter_and_buffer() -> None:
    configure_logging()
    root = logging.getLogger()

    handlers = [handler for handler in root.handlers if getattr(handler, "_yamibo_logging_handler", False)]
    assert handlers, "expected structured logging handlers to be attached"
    assert any(isinstance(handler, logging.StreamHandler) for handler in handlers)

    buffer_handler = get_log_buffer()
    record = logging.LogRecord("tests", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.event_type = "test.event"
    record.result = "success"
    record.status = "done"
    buffer_handler.emit(record)

    recent = buffer_handler.get_recent(limit=1)
    assert recent
    assert recent[-1]["event_type"] == "test.event"
