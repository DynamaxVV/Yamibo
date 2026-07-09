import pytest

from yamibo_mcp.domain.enums import JobStatus, JobType
from yamibo_mcp.domain.job_state import new_job_id
from yamibo_mcp.domain.models import FloorSnapshot, Job, ThreadSnapshot, TitleSnapshot
from yamibo_mcp.time_utils import utc_after_iso, utc_now, utc_now_iso


class TestJobStatus:
    def test_all_expected_values(self):
        expected = {"queued", "running", "paused", "succeeded", "partial", "failed",
                    "superseded", "interrupted", "retrying", "cancel_requested", "cancelled"}
        actual = {s.value for s in JobStatus}
        assert actual == expected

    @pytest.mark.parametrize("status", list(JobStatus), ids=lambda s: s.value)
    def test_string_enum(self, status):
        assert isinstance(status, str)
        assert status == status.value


class TestJobType:
    def test_all_expected_values(self):
        expected = {"noop", "sync_thread", "update_thread", "export_thread", "cleanup_job", "title_refine", "rag_index", "discussion_trend_index", "discussion_trend_report", "image_backfill"}
        actual = {t.value for t in JobType}
        assert actual == expected


class TestNewJobId:
    def test_format(self):
        job_id = new_job_id("sync_thread")
        assert job_id.startswith("sync_thread_")
        suffix = job_id.split("_", 2)[2]
        assert len(suffix) == 16

    def test_uniqueness(self):
        ids = {new_job_id("noop") for _ in range(100)}
        assert len(ids) == 100


class TestTitleSnapshot:
    def test_frozen(self):
        title = TitleSnapshot(
            raw_title="t", display_title="t", group_name=None,
            author_guess=None, core_title_guess="t", normalized_core_title="t",
            series_key="t", title_aliases=[], chapter_name=None,
            chapter_index=None, chapter_index_end=None, chapter_title=None,
            subtitle=None, tags=[], confidence=0.9, needs_review=False,
        )
        with pytest.raises(AttributeError):
            title.raw_title = "changed"


class TestFloorSnapshot:
    def test_defaults(self):
        f = FloorSnapshot(pid=1, tid=100, floor_no=1, publisher="u", content="c",
                          pub_time=None, has_images=False)
        assert f.image_urls == []
