"""帖子详情解析器测试 - 预期值由人工判断

基于原始标题逐项分析正确的 chapter_name / chapter_index / group_name / author / core_title。
"""

from pathlib import Path

import pytest
from tests.fixtures.loader import load_thread, load_edge_case
from yamibo_mcp.yamibo.parsers.thread_detail import _extract_post_meta, extract_author_only_total_pages, extract_forum_id_from_html, parse_thread_detail


# ── 每个帖子的人工预期值 ──────────────────────────────────────
# raw_title → 正确解析结果

EXPECTED = {
    572627: {
        # 【超时空辉夜姬】[ポテトルス] ray
        "group_name": None,
        "author_guess": "ポテトルス",
        "core_title_guess": "超时空辉夜姬",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    572617: {
        # 【金城まち】对残香施以獠牙 Kakukuroi汉化组
        "group_name": "Kakukuroi汉化组",
        "author_guess": "金城まち",
        "core_title_guess": "对残香施以獠牙",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    572458: {
        # 【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录
        "group_name": "提灯喵汉化组",
        "author_guess": "なもり",
        "core_title_guess": "摇曳百合",
        "chapter_name": "1",
        "chapter_index": 1.0,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    569610: {
        # [汉化工房九九组][2222楠]梦路明灯与魔法之书 13话
        "group_name": "汉化工房九九组",
        "author_guess": "2222楠",
        "core_title_guess": "梦路明灯与魔法之书",
        "chapter_name": "13话",
        "chapter_index": 13.0,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    571281: {
        # 【!】【汉化工房九九组】[花野ちあき]校内恋爱 13话其2
        "group_name": "汉化工房九九组",
        "author_guess": "花野ちあき",
        "core_title_guess": "校内恋爱",
        "chapter_name": "13话其2",
        "chapter_index": 13.2,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    572427: {
        # 【提灯喵汉化组】[梦幻魔法公主][コロネ店長]100天后就辞职的面包屋打工 51~60
        "group_name": "提灯喵汉化组",
        "author_guess": "梦幻魔法公主",
        "core_title_guess": "[コロネ店長]100天后就辞职的面包屋打工",
        "chapter_name": "51~60",
        "chapter_index": 51.0,
        "chapter_index_end": 60.0,
        "archive_status": "complete",
    },
    572530: {
        # 【春井夕】我会替你全部吃掉的 上篇 Kakukuroi汉化组
        "group_name": "Kakukuroi汉化组",
        "author_guess": "春井夕",
        "core_title_guess": "我会替你全部吃掉的",
        "chapter_name": "上篇",
        "chapter_index": 1.1,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
    572448: {
        # 【提灯喵汉化组】[原作:みかみてれん×作画:千種みのり]女孩们×吸血鬼 33:露露娜大人、获得新天地
        "group_name": "提灯喵汉化组",
        "author_guess": "原作:みかみてれん×作画:千種みのり",
        "core_title_guess": "女孩们×吸血鬼",
        "chapter_name": "33",
        "chapter_index": 33.0,
        "chapter_index_end": None,
        "archive_status": "complete",
    },
}


class TestThreadDetailParser:
    """帖子详情解析器测试 - 预期值由人工判断"""

    @staticmethod
    def _load_rich_text_sample() -> str:
        repo_root = Path(__file__).resolve().parents[3]
        matches = sorted(repo_root.glob("【授权转载】【个人翻译】*Powered by Discuz!.html"))
        assert matches, "sample html not found"
        return matches[0].read_text(encoding="utf-8", errors="ignore")

    # ── 核心字段逐项断言 ──────────────────────────────────────

    @pytest.mark.parametrize(
        "tid,field,expected",
        [
            (tid, f, v)
            for tid, fields in EXPECTED.items()
            for f, v in fields.items()
        ],
        ids=[f"{tid}.{f}" for tid, fields in EXPECTED.items() for f in fields],
    )
    def test_parsed_field_matches_expected(self, tid: int, field: str, expected):
        """每个解析字段应与人工判断的预期值一致"""
        # Arrange
        data = load_thread(tid)
        tp = data["title_parse"]

        # Act
        result = tp.get(field) if field != "archive_status" else data.get(field)

        # Assert
        assert result == expected, (
            f"[tid={tid}] {field}: expected={expected!r}, got={result!r}"
        )

    # ── 结构性断言 ────────────────────────────────────────────

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610, 571281, 572427, 572530, 572448],
        ids=str,
    )
    def test_thread_has_required_fields(self, tid):
        """每个帖子应包含必需的顶层字段"""
        # Arrange
        data = load_thread(tid)

        # Act / Assert
        assert data["tid"] == tid
        for field in ("display_title", "floors", "title_parse", "image_count"):
            assert field in data, f"[tid={tid}] missing field: {field}"

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610, 571281, 572427, 572530, 572448],
        ids=str,
    )
    def test_floor_count_matches_actual(self, tid):
        """floor_count 应与实际楼层数一致"""
        # Arrange
        data = load_thread(tid)

        # Act / Assert
        assert data["floor_count"] == len(data["floors"])

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610],
        ids=str,
    )
    def test_floor_no_is_sequential(self, tid):
        """楼层号应从 1 连续递增"""
        # Arrange
        data = load_thread(tid)

        # Act / Assert
        for i, floor in enumerate(data["floors"], 1):
            assert floor["floor_no"] == i

    def test_floor_has_required_fields(self):
        """每层楼应包含必需字段"""
        # Arrange
        data = load_thread(572617)

        # Act / Assert
        for floor in data["floors"]:
            for field in ("pid", "floor_no", "publisher", "has_images", "content"):
                assert field in floor, f"Missing field {field} in floor {floor.get('pid')}"

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610],
        ids=str,
    )
    def test_image_count_is_non_negative(self, tid):
        """图片数应为非负整数"""
        # Arrange
        data = load_thread(tid)

        # Act / Assert
        assert isinstance(data["image_count"], int)
        assert data["image_count"] >= 0

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610],
        ids=str,
    )
    def test_confidence_is_valid(self, tid):
        """置信度应在 0~1 之间"""
        # Arrange
        data = load_thread(tid)

        # Act
        confidence = data["title_parse"]["confidence"]

        # Assert
        assert isinstance(confidence, (int, float))
        assert 0 <= confidence <= 1

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610, 571281, 572427, 572530, 572448],
        ids=str,
    )
    def test_chapter_name_is_non_empty_string(self, tid):
        """章节名应为非空字符串"""
        # Arrange
        data = load_thread(tid)

        # Act
        chapter_name = data["title_parse"].get("chapter_name")

        # Assert
        assert isinstance(chapter_name, str), f"[tid={tid}] chapter_name is not a string: {chapter_name!r}"
        assert len(chapter_name) > 0

    def test_extract_forum_id_prefers_breadcrumb_forum(self):
        html = """
        <div id="pt">
          <a href="https://bbs.yamibo.com/">百合会</a>»
          <a href="https://bbs.yamibo.com/forum.php">论坛</a>›
          <a href="https://bbs.yamibo.com/forum.php?gid=2">江湖</a>›
          <a href="https://bbs.yamibo.com/forum-49-1.html">文學區</a>›
          <a href="https://bbs.yamibo.com/forum-55-1.html">轻小说/译文区</a>
        </div>
        <span id="thread_subject">示例标题</span>
        <div class="content">
          <a href="https://bbs.yamibo.com/forum-30-1.html">漫画区</a>
        </div>
        """
        assert extract_forum_id_from_html(html) == 55

    @pytest.mark.parametrize(
        "tid", [572627, 572617, 572458, 569610, 571281, 572427, 572530, 572448],
        ids=str,
    )
    def test_chapter_index_is_positive(self, tid):
        """章节索引应为正数"""
        # Arrange
        data = load_thread(tid)

        # Act
        chapter_index = data["title_parse"].get("chapter_index")

        # Assert
        assert isinstance(chapter_index, (int, float))
        assert chapter_index > 0

    # ── 特定帖子特征 ─────────────────────────────────────────

    def test_many_floors_thread(self):
        """572617 应有 13 层楼"""
        # Arrange
        data = load_thread(572617)

        # Act / Assert
        assert data["floor_count"] == 13
        assert len(data["floors"]) == 13

    def test_single_floor_thread(self):
        """569610 应只有 1 层楼"""
        # Arrange
        data = load_thread(569610)

        # Act / Assert
        assert data["floor_count"] == 1
        assert len(data["floors"]) == 1

    def test_parsed_floor_content_preserves_block_breaks(self):
        """楼层正文中的块级结构应保留成换行"""
        html = """
        <html><body>
          <span id="thread_subject">测试帖</span>
          <td id="postmessage_1001">
            仙台同学的价格正好五千元Episode 1
            <div>其实并没有非仙台同学不可的理由，市尾同学也可以，后藤同学也可以。</div>
            <div>一周一次，三个小时。</div>
          </td>
        </body></html>
        """
        summary = parse_thread_detail(html)
        assert summary.floors[0].content == (
            "仙台同学的价格正好五千元Episode 1\n"
            "其实并没有非仙台同学不可的理由，市尾同学也可以，后藤同学也可以。\n"
            "一周一次，三个小时。"
        )

    def test_rich_text_sample_floor_preserves_html_style(self):
        """样本贴应保留可展示的富文本样式"""
        html = self._load_rich_text_sample()
        summary = parse_thread_detail(html)
        first_floor = summary.floors[0]

        assert first_floor.rich_body_html is not None
        assert "font-size:" in first_floor.rich_body_html
        assert "text-align:left" in first_floor.rich_body_html
        assert "<strong>" in first_floor.rich_body_html or "<em>" in first_floor.rich_body_html
        assert "本帖最后由" not in first_floor.content
        assert "本帖最后由" not in first_floor.rich_body_html

    def test_font_color_is_preserved_in_rich_body_html(self):
        """font color 应映射为可展示的富文本样式"""
        html = """
        <html><body>
          <td id="postmessage_1">
            <div align="left"><font face="微软雅黑"><font size="5"><font color="#a0522d">标题</font></font></font></div>
            <font face="微软雅黑"><font size="3"><font color="#ff0000">译名：测试</font></font></font>
          </td>
        </body></html>
        """
        summary = parse_thread_detail(html)
        rich = summary.floors[0].rich_body_html or ""
        assert "#a0522d" in rich
        assert "#ff0000" in rich
        assert "标题" in rich
        assert "译名：测试" in rich

    def test_exported_thread_has_zip_path(self):
        """572530 应有导出路径"""
        # Arrange
        data = load_thread(572530)

        # Act / Assert
        assert data.get("export_path") is not None
        assert data["export_path"].endswith(".zip")

    def test_exported_thread_with_complex_author(self):
        """572448 应有导出路径和多作者"""
        # Arrange
        data = load_thread(572448)

        # Act / Assert
        assert data.get("export_path") is not None
        assert data["title_parse"]["group_name"] == "提灯喵汉化组"

    # ── 边界条件 ──────────────────────────────────────────────

    def test_empty_floor_edge_case(self):
        """空楼层帖子应返回空列表"""
        # Arrange
        data = load_edge_case("empty_floor")

        # Act / Assert
        assert data["floors"] == []
        assert data["floor_count"] == 0

    def test_missing_fields_edge_case(self):
        """缺失字段帖子应能被加载"""
        # Arrange
        data = load_edge_case("missing_fields")

        # Act / Assert
        assert "tid" in data
        assert "display_title" in data

    def test_malformed_edge_case(self):
        """畸形数据帖子应能被加载"""
        # Arrange
        data = load_edge_case("malformed")

        # Act / Assert
        assert "tid" in data

    def test_single_floor_edge_case(self):
        """单层楼边界帖子应正确解析"""
        # Arrange
        data = load_edge_case("single_floor")

        # Act / Assert
        assert data["floor_count"] == 1
        assert len(data["floors"]) == 1
        assert data["floors"][0]["floor_no"] == 1


class TestAuthorOnlyPagination:
    def test_extracts_total_pages_from_author_only_pagination(self):
        html = """
        <div class="pg">
          <strong>1</strong>
          <a href="forum.php?mod=viewthread&amp;tid=540745&amp;extra=&amp;authorid=229047&amp;page=2">2</a>
          <a href="forum.php?mod=viewthread&amp;tid=540745&amp;extra=&amp;authorid=229047&amp;page=3">3</a>
          <span title="共 3 页"> / 3 页</span>
        </div>
        """
        assert extract_author_only_total_pages(html, tid=540745, author_uid="229047") == 3

    def test_extract_post_meta_extracts_floor_publisher_uid(self):
        html = """
        <div id="post_1">
          <div class="authi"><a href="space-uid-229047.html">楼主</a></div>
          <em id="authorposton1">发表于 2026-06-14 12:00</em>
          <td id="postmessage_1">正文</td>
        </div>
        """
        meta = _extract_post_meta(html)
        assert meta[1]["publisher_uid"] == "229047"
