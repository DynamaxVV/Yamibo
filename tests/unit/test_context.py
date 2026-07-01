from __future__ import annotations

import json
import logging

from yamibo_mcp.context import LogContext, get_log_context, new_trace_id
from yamibo_mcp.structured_logging import StructuredJSONFormatter


def test_log_context_sets_and_clears_contextvars() -> None:
    tid = new_trace_id()
    with LogContext(trace_id=tid, job_id="job-1", job_type="archive"):
        ctx = get_log_context()
        assert ctx["trace_id"] == tid
        assert ctx["job_id"] == "job-1"
        assert ctx["job_type"] == "archive"

    # Context should be cleared after exiting the block
    ctx_after = get_log_context()
    assert "trace_id" not in ctx_after
    assert "job_id" not in ctx_after
    assert "job_type" not in ctx_after


def test_formatter_injects_contextvars_into_log_record() -> None:
    formatter = StructuredJSONFormatter()
    tid = new_trace_id()

    with LogContext(trace_id=tid, job_id="job-ctx", forum_id=55):
        record = logging.LogRecord("daemon.runner", logging.INFO, __file__, 1, "processing job", (), None)
        payload = json.loads(formatter.format(record))

    assert payload["trace_id"] == tid
    assert payload["job_id"] == "job-ctx"
    assert payload["forum_id"] == 55


def test_formatter_record_attr_overrides_context() -> None:
    """Explicit record attrs take priority over contextvars."""
    formatter = StructuredJSONFormatter()

    with LogContext(job_id="from-context"):
        record = logging.LogRecord("daemon.runner", logging.INFO, __file__, 1, "msg", (), None)
        record.job_id = "from-record"
        payload = json.loads(formatter.format(record))

    assert payload["job_id"] == "from-record"
