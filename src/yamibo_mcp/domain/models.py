from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Job:
    job_id: str
    job_type: str
    status: str
    stage: str | None
    tid: int | None
    payload: dict[str, Any]
    progress_current: int
    progress_total: int | None
    worker_id: str | None
    heartbeat_at: str | None
    lease_until: str | None
    retry_count: int
    max_retries: int
    resumable: bool
    error_code: str | None
    error_message: str | None
    artifacts: dict[str, Any]
    created_at: str
    updated_at: str
    finished_at: str | None


@dataclass(frozen=True)
class FloorSnapshot:
    pid: int
    tid: int
    floor_no: int
    publisher: str | None
    content: str
    pub_time: str | None
    has_images: bool
    image_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TitleSnapshot:
    raw_title: str
    display_title: str
    group_name: str | None
    author_guess: str | None
    core_title_guess: str
    normalized_core_title: str
    series_key: str
    title_aliases: list[str]
    chapter_name: str | None
    chapter_index: float | None
    chapter_index_end: float | None
    chapter_title: str | None
    subtitle: str | None
    tags: list[str]
    confidence: float
    needs_review: bool
    parser_version: str = "title-v1"


@dataclass(frozen=True)
class ThreadSnapshot:
    tid: int
    url: str | None
    page_type: str
    raw_title: str
    display_title: str
    title: TitleSnapshot
    publisher: str | None
    publisher_uid: str | None
    pub_time: str | None
    permission: int
    floors: list[FloorSnapshot]
    image_count: int = 0


@dataclass(frozen=True)
class ContentBlock:
    block_id: str
    pid: int
    order_index: int
    block_type: str  # text | image | attachment | quote | link | divider | unknown
    text: str | None = None
    asset_id: str | None = None
    asset_url: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetSnapshot:
    asset_id: str
    tid: int
    pid: int
    asset_type: str  # image | attachment | shared | external_link
    remote_url: str
    local_path: str | None
    exportable: bool
    required: bool
    status: str  # pending | downloaded | skipped | missing


@dataclass(frozen=True)
class PostSnapshot:
    pid: int
    tid: int
    floor_no: int
    publisher: str | None
    pub_time: str | None
    content_text: str
    blocks: list[ContentBlock] = field(default_factory=list)


@dataclass(frozen=True)
class ThreadContentSnapshot:
    tid: int
    forum_id: int
    content_kind: str
    posts: list[PostSnapshot]
    assets: list[AssetSnapshot]


@dataclass(frozen=True)
class JobEvent:
    event_id: int
    job_id: str
    event_type: str
    status: str | None
    stage: str | None
    payload: dict[str, object]
    created_at: str
