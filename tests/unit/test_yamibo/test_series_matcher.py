import pytest

from yamibo_mcp.domain.models import TitleSnapshot
from yamibo_mcp.yamibo.series_matcher import build_series_match_decision


def _make_title(**overrides) -> TitleSnapshot:
    defaults = dict(
        raw_title="t", display_title="t", group_name=None,
        author_guess=None, core_title_guess="t", normalized_core_title="t",
        series_key="t", title_aliases=[], chapter_name=None,
        chapter_index=None, chapter_index_end=None, chapter_title=None,
        subtitle=None, tags=[], confidence=0.9, needs_review=False,
        parser_version="title-v1",
    )
    defaults.update(overrides)
    return TitleSnapshot(**defaults)


class TestBuildSeriesMatchDecision:
    def test_basic_decision(self):
        title = _make_title(series_key="漫画a", author_guess="作者A")
        decision = build_series_match_decision(title)
        assert decision.base_key == "漫画a"
        assert decision.creator_key is not None
        assert decision.needs_review is False

    def test_no_author_yields_none_creator_key(self):
        title = _make_title(series_key="漫画b", author_guess=None)
        decision = build_series_match_decision(title)
        assert decision.creator_key is None

    def test_empty_author_yields_none_creator_key(self):
        title = _make_title(series_key="漫画c", author_guess="")
        decision = build_series_match_decision(title)
        assert decision.creator_key is None

    def test_aliases_produce_alias_keys(self):
        title = _make_title(series_key="主标题", title_aliases=["别名1", "别名2"])
        decision = build_series_match_decision(title)
        assert len(decision.alias_keys) == 2
        assert len(decision.aliases) == 2

    def test_alias_same_as_base_key_filtered(self):
        title = _make_title(series_key="same", title_aliases=["same"])
        decision = build_series_match_decision(title)
        assert decision.alias_keys == []

    def test_alias_same_as_core_title_filtered(self):
        title = _make_title(series_key="key", core_title_guess="核心",
                            title_aliases=["核心"])
        decision = build_series_match_decision(title)
        assert decision.aliases == []

    def test_needs_review_when_aliases_present(self):
        title = _make_title(series_key="k", title_aliases=["别名"])
        decision = build_series_match_decision(title)
        assert decision.needs_review is True

    def test_needs_review_preserved_from_title(self):
        title = _make_title(series_key="k", needs_review=True)
        decision = build_series_match_decision(title)
        assert decision.needs_review is True
