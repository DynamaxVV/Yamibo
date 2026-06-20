from __future__ import annotations

from yamibo_mcp.domain.models import JobEvent
from yamibo_mcp.web.app import WebHandler


def test_render_job_events_timeline_contains_event_rows():
    handler = object.__new__(WebHandler)
    handler.lang = "zh"
    events = [
        JobEvent(
            event_id=1,
            job_id="sync_thread_1",
            event_type="job.started",
            status="running",
            stage="acquired",
            payload={"worker_id": "daemon-1"},
            created_at="2026-06-20T10:00:00+00:00",
        ),
        JobEvent(
            event_id=2,
            job_id="sync_thread_1",
            event_type="job.partial",
            status="partial",
            stage="finalize",
            payload={"artifacts": {"tid": 42}},
            created_at="2026-06-20T10:01:00+00:00",
        ),
    ]

    html = handler._render_job_events_timeline(events)

    assert "事件时间线" not in html
    assert "job.started" in html
    assert "job.partial" in html
    assert "daemon-1" in html
    assert "&quot;tid&quot;: 42" in html
