from __future__ import annotations

import pytest

from yamibo_mcp.domain.content import (
    build_content_snapshot,
    classify_content_kind,
    render_context_by_profile,
    validate_by_profile,
)
from yamibo_mcp.domain.models import (
    AssetSnapshot,
    ContentBlock,
    FloorSnapshot,
    PostSnapshot,
    ThreadContentSnapshot,
    ThreadSnapshot,
    TitleSnapshot,
)


def _title(**overrides) -> TitleSnapshot:
    defaults = dict(
        raw_title="[A组] 测试漫画 第1话",
        display_title="测试漫画 第1话",
        group_name="A组",
        author_guess="作者A",
        core_title_guess="测试漫画",
        normalized_core_title="测试漫画",
        series_key="测试漫画",
        title_aliases=[],
        chapter_name="第1话",
        chapter_index=1.0,
        chapter_index_end=None,
        chapter_title=None,
        subtitle=None,
        tags=[],
        confidence=0.9,
        needs_review=False,
        parser_version="title-v1",
    )
    defaults.update(overrides)
    return TitleSnapshot(**defaults)


def _floor(pid=1001, tid=999, floor_no=1, content="正文", has_images=False, image_urls=None, publisher="user1"):
    return FloorSnapshot(
        pid=pid, tid=tid, floor_no=floor_no,
        publisher=publisher, content=content,
        pub_time="2025-01-01", has_images=has_images,
        image_urls=image_urls or [],
    )


def _snapshot(tid=999, floors=None, publisher="user1", image_count=0, **overrides):
    title = overrides.pop("title", _title())
    floors = floors or [_floor(tid=tid)]
    defaults = dict(
        tid=tid, url=None, page_type="thread_detail",
        raw_title=title.raw_title, display_title=title.display_title,
        title=title, publisher=publisher, publisher_uid="1",
        pub_time="2025-01-01", permission=0, floors=floors, image_count=image_count,
    )
    defaults.update(overrides)
    return ThreadSnapshot(**defaults)


def _comic_snapshot(tid=999):
    floors = [
        _floor(pid=1001, tid=tid, floor_no=1, content="", has_images=True, image_urls=["http://img/p1.jpg"]),
        _floor(pid=1002, tid=tid, floor_no=2, content="", has_images=True, image_urls=["http://img/p2.jpg"]),
    ]
    return _snapshot(tid=tid, floors=floors, image_count=2)


def _novel_snapshot(tid=999):
    long_text = "这是一段很长的小说正文。" * 50
    floors = [_floor(pid=1001, tid=tid, floor_no=1, content=long_text)]
    return _snapshot(tid=tid, floors=floors, image_count=0)


def _discussion_snapshot(tid=999):
    floors = [
        _floor(pid=1001, tid=tid, floor_no=1, content="大家好，讨论一下这个话题"),
        _floor(pid=1002, tid=tid, floor_no=2, content="我觉得不错", publisher="user2"),
    ]
    return _snapshot(tid=tid, floors=floors, image_count=0)


def _mixed_snapshot(tid=999):
    floors = [
        _floor(pid=1001, tid=tid, floor_no=1, content="图文并茂", has_images=True, image_urls=["http://img/mix.jpg"]),
        _floor(pid=1002, tid=tid, floor_no=2, content="补充说明" * 20),
    ]
    return _snapshot(tid=tid, floors=floors, image_count=1)


# --- classify_content_kind ---

class TestClassifyContentKind:
    def test_comic_forum_returns_comic(self):
        assert classify_content_kind(30, image_count=5, word_count=10) == "comic"

    def test_novel_forum_returns_novel(self):
        assert classify_content_kind(55, image_count=0, word_count=1000) == "novel"

    def test_discussion_forum_returns_discussion(self):
        assert classify_content_kind(33, image_count=0, word_count=100) == "discussion"

    def test_anime_forum_returns_discussion(self):
        assert classify_content_kind(5, image_count=2, word_count=50) == "discussion"

    def test_unknown_forum_heavy_image_returns_comic(self):
        assert classify_content_kind(999, image_count=10, word_count=50) == "comic"

    def test_unknown_forum_long_text_returns_novel(self):
        assert classify_content_kind(999, image_count=0, word_count=600) == "novel"

    def test_unknown_forum_mixed_returns_mixed(self):
        assert classify_content_kind(999, image_count=3, word_count=300) == "mixed"

    def test_unknown_forum_short_text_returns_discussion(self):
        assert classify_content_kind(999, image_count=0, word_count=100) == "discussion"

    def test_none_forum_defaults_to_comic(self):
        assert classify_content_kind(None, image_count=0, word_count=100) == "comic"


# --- build_content_snapshot ---

class TestBuildContentSnapshot:
    def test_comic_snapshot_has_correct_content_kind(self):
        snap = _comic_snapshot()
        content = build_content_snapshot(snap, forum_id=30)
        assert content.content_kind == "comic"
        assert content.forum_id == 30

    def test_novel_snapshot_has_correct_content_kind(self):
        snap = _novel_snapshot()
        content = build_content_snapshot(snap, forum_id=55)
        assert content.content_kind == "novel"

    def test_discussion_snapshot_has_correct_content_kind(self):
        snap = _discussion_snapshot()
        content = build_content_snapshot(snap, forum_id=33)
        assert content.content_kind == "discussion"

    def test_posts_match_floors(self):
        snap = _snapshot(floors=[_floor(pid=1001, floor_no=1), _floor(pid=1002, floor_no=2, content="第二层", publisher="u2")])
        content = build_content_snapshot(snap, forum_id=30)
        assert len(content.posts) == 2
        assert content.posts[0].pid == 1001
        assert content.posts[1].pid == 1002

    def test_post_blocks_include_text(self):
        snap = _snapshot()
        content = build_content_snapshot(snap, forum_id=30)
        text_blocks = [b for b in content.posts[0].blocks if b.block_type == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0].text == "正文"

    def test_post_blocks_include_images(self):
        snap = _snapshot(floors=[_floor(has_images=True, image_urls=["http://img/a.jpg", "http://img/b.jpg"])])
        content = build_content_snapshot(snap, forum_id=30)
        img_blocks = [b for b in content.posts[0].blocks if b.block_type == "image"]
        assert len(img_blocks) == 2

    def test_empty_floor_gets_unknown_block(self):
        snap = _snapshot(floors=[_floor(content="", has_images=False)])
        content = build_content_snapshot(snap, forum_id=30)
        assert content.posts[0].blocks[0].block_type == "unknown"

    def test_assets_built_from_image_urls(self):
        snap = _snapshot(floors=[_floor(has_images=True, image_urls=["http://img/a.jpg"])])
        content = build_content_snapshot(snap, forum_id=30)
        assert len(content.assets) == 1
        assert content.assets[0].remote_url == "http://img/a.jpg"
        assert content.assets[0].asset_type == "image"

    def test_shared_assets_classified(self):
        snap = _snapshot(floors=[_floor(has_images=True, image_urls=["https://bbs.yamibo.com/static/image/smiley/default.png"])])
        content = build_content_snapshot(snap, forum_id=30)
        assert content.assets[0].asset_type == "shared"
        assert content.assets[0].required is False

    def test_primary_floor_image_is_required(self):
        snap = _snapshot(floors=[_floor(has_images=True, image_urls=["http://img/a.jpg"])])
        content = build_content_snapshot(snap, forum_id=30)
        assert content.assets[0].required is True

    def test_reply_floor_image_is_not_required(self):
        floors = [
            _floor(pid=1001, floor_no=1, content="主楼"),
            _floor(pid=1002, floor_no=2, content="", has_images=True, image_urls=["http://img/reply.jpg"], publisher="other"),
        ]
        snap = _snapshot(floors=floors)
        content = build_content_snapshot(snap, forum_id=30)
        reply_assets = [a for a in content.assets if a.pid == 1002]
        assert reply_assets[0].required is False

    def test_default_forum_id_is_30(self):
        snap = _snapshot()
        content = build_content_snapshot(snap)
        assert content.forum_id == 30

    def test_tid_preserved(self):
        snap = _snapshot(tid=12345)
        content = build_content_snapshot(snap, forum_id=30)
        assert content.tid == 12345


# --- validate_by_profile ---

class TestValidateComicProfile:
    def test_comic_with_primary_image_valid(self):
        snap = _comic_snapshot()
        content = build_content_snapshot(snap, forum_id=30)
        result = validate_by_profile(content)
        assert result.valid is True

    def test_comic_missing_required_image_warns(self):
        snap = _comic_snapshot()
        content = build_content_snapshot(snap, forum_id=30)
        result = validate_by_profile(content, archived_image_pids=set())
        assert any("missing" in w and "required" in w for w in result.warnings)

    def test_comic_no_text_no_image_is_error(self):
        floors = [_floor(pid=1001, floor_no=1, content="", has_images=False)]
        snap = _snapshot(floors=floors, image_count=0)
        content = build_content_snapshot(snap, forum_id=30)
        result = validate_by_profile(content)
        assert result.valid is False
        assert any("neither text nor required image" in e for e in result.errors)


class TestValidateNovelProfile:
    def test_novel_with_long_text_valid(self):
        snap = _novel_snapshot()
        content = build_content_snapshot(snap, forum_id=55)
        result = validate_by_profile(content)
        assert result.valid is True

    def test_novel_short_text_is_error(self):
        floors = [_floor(pid=1001, floor_no=1, content="短文本")]
        snap = _snapshot(floors=floors, image_count=0)
        content = build_content_snapshot(snap, forum_id=55)
        result = validate_by_profile(content)
        assert result.valid is False
        assert any("substantial primary text" in e for e in result.errors)

    def test_novel_missing_non_critical_image_warns(self):
        snap = _novel_snapshot()
        content = build_content_snapshot(snap, forum_id=55)
        # Mark a non-required asset as missing
        content = ThreadContentSnapshot(
            tid=content.tid,
            forum_id=content.forum_id,
            content_kind=content.content_kind,
            posts=content.posts,
            assets=[
                AssetSnapshot(
                    asset_id="a1", tid=999, pid=1001, asset_type="image",
                    remote_url="http://img/decor.jpg", local_path=None,
                    exportable=True, required=False, status="missing",
                ),
            ],
        )
        result = validate_by_profile(content)
        assert any("non-critical" in w for w in result.warnings)


class TestValidateDiscussionProfile:
    def test_discussion_with_text_valid(self):
        snap = _discussion_snapshot()
        content = build_content_snapshot(snap, forum_id=33)
        result = validate_by_profile(content)
        assert result.valid is True

    def test_discussion_empty_posts_is_error(self):
        floors = [_floor(pid=1001, floor_no=1, content="", has_images=False)]
        snap = _snapshot(floors=floors, image_count=0)
        content = build_content_snapshot(snap, forum_id=33)
        result = validate_by_profile(content)
        assert result.valid is False
        assert any("at least one post with text" in e for e in result.errors)

    def test_discussion_missing_optional_image_warns(self):
        snap = _discussion_snapshot()
        content = build_content_snapshot(snap, forum_id=33)
        content = ThreadContentSnapshot(
            tid=content.tid,
            forum_id=content.forum_id,
            content_kind=content.content_kind,
            posts=content.posts,
            assets=[
                AssetSnapshot(
                    asset_id="a1", tid=999, pid=1001, asset_type="image",
                    remote_url="http://img/opt.jpg", local_path=None,
                    exportable=True, required=False, status="missing",
                ),
            ],
        )
        result = validate_by_profile(content)
        assert any("optional images" in w for w in result.warnings)


class TestValidateMixedProfile:
    def test_mixed_with_content_valid(self):
        snap = _mixed_snapshot()
        content = build_content_snapshot(snap, forum_id=999)
        result = validate_by_profile(content)
        assert result.valid is True

    def test_mixed_no_content_is_error(self):
        floors = [_floor(pid=1001, floor_no=1, content="", has_images=False)]
        snap = _snapshot(floors=floors, image_count=0)
        content = build_content_snapshot(snap, forum_id=999)
        # Force mixed kind
        content = ThreadContentSnapshot(
            tid=content.tid, forum_id=999, content_kind="mixed",
            posts=content.posts, assets=content.assets,
        )
        result = validate_by_profile(content)
        assert result.valid is False

    def test_mixed_unknown_blocks_warn(self):
        snap = _mixed_snapshot()
        content = build_content_snapshot(snap, forum_id=999)
        content = ThreadContentSnapshot(
            tid=content.tid, forum_id=999, content_kind="mixed",
            posts=[
                PostSnapshot(
                    pid=1001, tid=999, floor_no=1, publisher="u", pub_time=None,
                    content_text="text",
                    blocks=[
                        ContentBlock(block_id="b1", pid=1001, order_index=0, block_type="unknown"),
                    ],
                ),
            ],
            assets=[],
        )
        result = validate_by_profile(content)
        assert any("unrecognized" in w for w in result.warnings)


# --- render_context_by_profile ---

class TestRenderContextByProfile:
    def test_comic_image_only_floor(self):
        snap = _comic_snapshot()
        md = render_context_by_profile(snap, content_kind="comic")
        assert "[image-only floor]" in md

    def test_novel_empty_floor(self):
        floors = [_floor(pid=1001, floor_no=1, content="")]
        snap = _snapshot(floors=floors)
        md = render_context_by_profile(snap, content_kind="novel")
        assert "[empty floor]" in md

    def test_contains_title_heading(self):
        snap = _snapshot()
        md = render_context_by_profile(snap, content_kind="comic")
        assert "# 测试漫画 第1话" in md

    def test_contains_floor_content(self):
        snap = _snapshot()
        md = render_context_by_profile(snap, content_kind="comic")
        assert "正文" in md
        assert "1F · user1" in md

    def test_archived_images_rendered(self):
        snap = _snapshot(floors=[_floor(has_images=True, image_urls=["http://img/a.jpg"])])
        md = render_context_by_profile(snap, content_kind="comic", archived_images={1001: ["images/img.jpg"]})
        assert "![image](images/img.jpg)" in md

    def test_ends_with_newline(self):
        snap = _snapshot()
        md = render_context_by_profile(snap, content_kind="comic")
        assert md.endswith("\n")


# --- dataclass properties ---

class TestDataclasses:
    def test_content_block_frozen(self):
        block = ContentBlock(block_id="b1", pid=1001, order_index=0, block_type="text", text="hello")
        import pytest
        with pytest.raises(AttributeError):
            block.block_type = "image"

    def test_asset_snapshot_frozen(self):
        asset = AssetSnapshot(
            asset_id="a1", tid=999, pid=1001, asset_type="image",
            remote_url="http://img/a.jpg", local_path=None,
            exportable=True, required=True, status="pending",
        )
        import pytest
        with pytest.raises(AttributeError):
            asset.status = "downloaded"

    def test_post_snapshot_has_default_blocks(self):
        post = PostSnapshot(pid=1001, tid=999, floor_no=1, publisher="u", pub_time=None, content_text="text")
        assert post.blocks == []

    def test_thread_content_snapshot_frozen(self):
        content = ThreadContentSnapshot(tid=999, forum_id=30, content_kind="comic", posts=[], assets=[])
        import pytest
        with pytest.raises(AttributeError):
            content.content_kind = "novel"

    def test_content_block_default_metadata(self):
        block = ContentBlock(block_id="b1", pid=1001, order_index=0, block_type="text")
        assert block.metadata == {}
        assert block.text is None
        assert block.asset_url is None
