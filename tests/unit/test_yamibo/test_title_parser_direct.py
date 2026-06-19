"""规则引擎直接测试 — 验证 parse_title() 输出与 LLM 规则对齐"""

import pytest

from yamibo_mcp.yamibo.title.parser import parse_title


class TestDefaultChapter:
    def test_no_chapter_info_defaults_to_1(self):
        r = parse_title("【超时空辉夜姬】[ポテトルス] ray")
        assert r.chapter_name == "1"
        assert r.chapter_index == 1.0

    def test_simple_no_chapter(self):
        r = parse_title("[作者A] 漫画名")
        assert r.chapter_name == "1"
        assert r.chapter_index == 1.0

    def test_group_no_chapter(self):
        r = parse_title("【个人汉化】[きさらぎ壱吾]撩拨")
        assert r.chapter_name == "1"
        assert r.chapter_index == 1.0


class TestSpecialContent999:
    @pytest.mark.parametrize("keyword", ["特典", "彩页", "番外", "加笔", "贴附"])
    def test_special_keyword_sets_999(self, keyword):
        r = parse_title(f"[作者] 漫画名 {keyword}")
        assert r.chapter_index == 999.0
        assert r.chapter_name == f"999 {keyword}"

    def test_special_standalone(self):
        r = parse_title("[作者] 漫画名 番外")
        assert r.chapter_index == 999.0
        assert r.chapter_name == "999 番外"


class TestColonPattern:
    def test_colon_chapter_title(self):
        r = parse_title("[作者] 漫画名 33:露露娜大人、获得新天地")
        assert r.chapter_name == "33"
        assert r.chapter_index == 33.0
        assert r.chapter_title == "露露娜大人、获得新天地"

    def test_fullwidth_colon(self):
        r = parse_title("[作者] 漫画名 5：标题")
        assert r.chapter_name == "5"
        assert r.chapter_index == 5.0
        assert r.chapter_title == "标题"


class TestPopPrefixes:
    def test_skip_non_group_bracket_extracts_author(self):
        r = parse_title("【超时空辉夜姬】[ポテトルス] ray")
        assert r.author_guess == "ポテトルス"
        assert r.core_title_guess == "ray"

    def test_skip_multiple_non_group_brackets(self):
        r = parse_title("【标签A】【标签B】[作者] 漫画名")
        assert r.author_guess == "作者"
        assert r.core_title_guess == "漫画名"

    def test_group_bracket_extracted(self):
        r = parse_title("【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录")
        assert r.group_name == "提灯喵汉化组"
        assert r.author_guess == "なもり"

    def test_exclamation_tag(self):
        r = parse_title("【!】【汉化工房九九组】[花野ちあき]校内恋爱 13话其2")
        assert "!" in r.tags
        assert r.group_name == "汉化工房九九组"
        assert r.author_guess == "花野ちあき"

    def test_non_group_bracket_skipped(self):
        r = parse_title("【金城まち】 对残香施以獠牙 Kakukuroi汉化组")
        assert "对残香施以獠牙" in r.core_title_guess


class TestChapterIndex:
    def test_basic_chapter(self):
        r = parse_title("[作者] 漫画名 第13话")
        assert r.chapter_index == 13.0

    def test_chapter_with_suffix_up(self):
        r = parse_title("[作者] 漫画名 13话上")
        assert r.chapter_index == 13.1

    def test_chapter_with_suffix_down(self):
        r = parse_title("[作者] 漫画名 13话下")
        assert r.chapter_index == 13.2

    def test_chapter_with_its(self):
        r = parse_title("[作者] 漫画名 13话其2")
        assert r.chapter_index == 13.2

    def test_range_chapter(self):
        r = parse_title("[作者] 漫画名 51~60")
        assert r.chapter_index == 51.0
        assert r.chapter_index_end == 60.0

    def test_chapter_name_with_range(self):
        r = parse_title("[作者] 漫画名 51~60")
        assert r.chapter_name == "51~60"

    def test_special_chapter_name_prefix(self):
        r = parse_title("[作者] 漫画名 番外")
        assert r.chapter_name == "999 番外"
        assert r.chapter_index == 999.0


class TestBareNumberNotChapter:
    def test_bare_number_without_unit_word_not_parsed_as_chapter(self):
        r = parse_title("【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录")
        assert r.chapter_name == "1"
        assert r.chapter_index == 1.0
        # Bare "233" without unit word (话/話/回/章) is not a chapter number.
        # The entire remaining text becomes core_title.
        assert "摇曳百合" in r.core_title_guess
