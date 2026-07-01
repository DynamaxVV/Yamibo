from __future__ import annotations

import json
import logging

from yamibo_mcp.structured_logging import StructuredJSONFormatter

_LOG = logging.getLogger("tests.job_lifecycle")


def test_emit_sets_structured_attrs_on_record() -> None:
    from yamibo_mcp.structured_logging import emit

    formatter = StructuredJSONFormatter()

    with _capture_log(_LOG) as records:
        emit(_LOG, logging.INFO, "job.created", "job-42 created",
             result="success", status="queued", job_id="job-42",
             job_type="archive", error_code=None)

    assert records
    payload = json.loads(formatter.format(records[0]))
    assert payload["event_type"] == "job.created"
    assert payload["message"] == "job-42 created"
    assert payload["result"] == "success"
    assert payload["status"] == "queued"
    assert payload["job_id"] == "job-42"
    assert payload["job_type"] == "archive"


def test_emit_failure_records_error_fields() -> None:
    from yamibo_mcp.structured_logging import emit

    formatter = StructuredJSONFormatter()

    with _capture_log(_LOG) as records:
        emit(_LOG, logging.ERROR, "job.failed", "job-99 failed: REMOTE_TIMEOUT",
             result="failure", status="failed",
             error_code="REMOTE_TIMEOUT", error_message="timed out",
             retryable=True, attempt=3)

    assert records
    payload = json.loads(formatter.format(records[0]))
    assert payload["error_code"] == "REMOTE_TIMEOUT"
    assert payload["error_message"] == "timed out"
    assert payload["retryable"] is True
    assert payload["attempt"] == 3


def test_emit_warning_codes() -> None:
    from yamibo_mcp.structured_logging import emit

    formatter = StructuredJSONFormatter()

    with _capture_log(_LOG) as records:
        emit(_LOG, logging.WARNING, "query.fallback", "SQL fallback used",
             result="degraded", status="ok",
             fallback_mode="sql", fallback_reason="RAG unavailable",
             warning_codes=["RAG_UNAVAILABLE"])

    assert records
    payload = json.loads(formatter.format(records[0]))
    assert payload["fallback_mode"] == "sql"
    assert payload["fallback_reason"] == "RAG unavailable"
    assert payload["warning_codes"] == ["RAG_UNAVAILABLE"]


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _capture_log:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._handler = _CaptureHandler()

    def __enter__(self) -> list[logging.LogRecord]:
        self._handler.records.clear()
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self._handler.records

    def __exit__(self, *_: object) -> None:
        self._logger.removeHandler(self._handler)
