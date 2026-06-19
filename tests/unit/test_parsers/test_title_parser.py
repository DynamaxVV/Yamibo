"""标题解析器测试 - 基于人工判断的预期值

预期值规则（来自 AGENTS.md）：
- chapter_index：有章节号用数字，"第1话上/6话1/第2话前篇"等用小数，xx特典/xx彩页/xx番外填999，无章节号填1
- chapter_name：无章节名则用章节数替代，xx特典/xx彩页/xx番外前加"999 "
"""

import pytest
from tests.fixtures.loader import load_title_parse


ALL_FIXTURE_NAMES = [
    "simple", "with_group", "chapter_name",
    "special_prefix", "complex_title", "short_title",
]

# 每个 fixture 的完整预期值（由人工判断，非 parser 输出）
EXPECTED = {
    "simple": {
        # 【超时空辉夜姬】[ポテトルス] ray
        "group_name": None,
        "author_guess": "ポテトルス",
        "core_title_guess": "超时空辉夜姬",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
    },
    "with_group": {
        # 【金城まち】对残香施以獠牙 Kakukuroi汉化组
        "group_name": "Kakukuroi汉化组",
        "author_guess": "金城まち",
        "core_title_guess": "对残香施以獠牙",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
    },
    "chapter_name": {
        # 【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录
        "group_name": "提灯喵汉化组",
        "author_guess": "なもり",
        "core_title_guess": "摇曳百合",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
    },
    "special_prefix": {
        # 【!】【汉化工房九九组】[花野ちあき]校内恋爱 13话其2
        "group_name": "汉化工房九九组",
        "author_guess": "花野ちあき",
        "core_title_guess": "校内恋爱",
        "chapter_name": "13话其2",
        "chapter_index": 13.2,
        "chapter_index_end": None,
    },
    "complex_title": {
        # [原作:りんご饴サードX漫画:川田暁生]公爵千金的笼络任务—第08话
        "group_name": None,
        "author_guess": "原作:りんご饴サードX漫画:川田暁生",
        "core_title_guess": "公爵千金的笼络任务",
        "chapter_name": "第08话",
        "chapter_index": 8.0,
        "chapter_index_end": None,
    },
    "short_title": {
        # 【个人汉化】[きさらぎ壱吾]撩拨
        "group_name": "个人汉化",
        "author_guess": "きさらぎ壱吾",
        "core_title_guess": "撩拨",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
    },
}


class TestTitleParser:
    """标题解析器测试 - 预期值由人工判断"""

    # ── 核心字段逐项断言 ──────────────────────────────────────

    @pytest.mark.parametrize(
        "name,field,expected",
        [
            (n, f, v)
            for n, fields in EXPECTED.items()
            for f, v in fields.items()
        ],
        ids=[f"{n}.{f}" for n, fields in EXPECTED.items() for f in fields],
    )
    def test_parsed_field_matches_expected(self, name: str, field: str, expected):
        """每个解析字段应与人工判断的预期值一致"""
        # Arrange
        data = load_title_parse(name)

        # Act
        result = data.get(field)

        # Assert
        assert result == expected, (
            f"[{name}] {field}: expected={expected!r}, got={result!r}"
        )

    # ── 结构性断言 ────────────────────────────────────────────

    @pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
    def test_confidence_is_valid_float_in_range(self, name: str):
        """置信度应为 0~1 之间的浮点数"""
        # Arrange
        data = load_title_parse(name)

        # Act
        confidence = data["confidence"]

        # Assert
        assert isinstance(confidence, (int, float))
        assert 0 <= confidence <= 1

    @pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
    def test_needs_review_is_boolean(self, name: str):
        """needs_review 应为布尔值"""
        # Arrange
        data = load_title_parse(name)

        # Act
        needs_review = data["needs_review"]

        # Assert
        assert isinstance(needs_review, bool)

    @pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
    def test_chapter_index_is_positive_number(self, name: str):
        """章节索引应为正数"""
        # Arrange
        data = load_title_parse(name)

        # Act
        chapter_index = data.get("chapter_index")

        # Assert
        assert isinstance(chapter_index, (int, float))
        assert chapter_index > 0

    @pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
    def test_chapter_name_is_non_empty_string(self, name: str):
        """章节名应为非空字符串（无章节名时用章节数替代）"""
        # Arrange
        data = load_title_parse(name)

        # Act
        chapter_name = data.get("chapter_name")

        # Assert
        assert isinstance(chapter_name, str)
        assert len(chapter_name) > 0

    @pytest.mark.parametrize("name", ALL_FIXTURE_NAMES)
    def test_structure_has_required_fields(self, name: str):
        """每个解析结果应包含必需字段"""
        # Arrange
        data = load_title_parse(name)

        # Act / Assert
        for field in ("core_title_guess", "author_guess", "confidence", "chapter_name", "chapter_index"):
            assert field in data, f"[{name}] missing required field: {field}"
