import pytest

from yamibo_mcp.yamibo.cleaners.content_cleaner import clean_content


class TestCleanContent:
    def test_removes_attachment_size_hint(self):
        result = clean_content("图片 (1.2MB, 下载次数: 5)")
        assert "下载次数" not in result
        assert "图片" in result

    def test_removes_download_prompt(self):
        result = clean_content("下载附件 保存到相册")
        assert result == ""

    def test_removes_upload_timestamp(self):
        result = clean_content("2026-6-10 18:28 上传")
        assert result == ""

    def test_removes_last_edited_banner(self):
        result = clean_content("本帖最后由 zyq102 于 2026-6-11 23:30 编辑")
        assert result == ""

    def test_removes_standalone_image_filename(self):
        result = clean_content("photo.jpg")
        assert result == ""

    def test_removes_standalone_unicode_image_filename(self):
        result = clean_content("i-0016-尾页.jpg")
        assert result == ""

    def test_removes_standalone_spaced_unicode_image_filename(self):
        result = clean_content("『映画　けいおん！』ＢＤＤＶＤが７月１８日発売決定.jpg")
        assert result == ""

    def test_preserves_inline_image_reference(self):
        result = clean_content("看这个图 lol.jpg 太搞笑了")
        assert "lol.jpg" in result

    def test_removes_compact_attachment_residue_from_short_line(self):
        text = (
            "正文很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长 "
            "i-000a.jpg (842.99 KB, 下载次数: 15) 下载附件 保存到相册 2026-6-11 22:57 上传"
        )
        result = clean_content(text)
        assert "i-000a.jpg" not in result
        assert "下载附件" not in result
        assert "保存到相册" not in result
        assert "上传" not in result
        assert "正文很长" in result

    def test_removes_inline_attachment_suffix_appended_to_prose(self):
        text = "不存在什么误读的空间其实076.png(372 Bytes, 下载次数: 79)"
        result = clean_content(text)
        assert result == "不存在什么误读的空间其实"

    def test_removes_quoted_attachment_timestamp_count_line(self):
        result = clean_content("> 2009-1-7 13:22, 下载次数: 3")
        assert result == ""

    def test_removes_boundary_prefixed_attachment_suffix_with_empty_download_count(self):
        text = "气。生气气.jpg(118.57 KB, 下载次数: )"
        result = clean_content(text)
        assert result == "气。"

    def test_normalizes_nbsp(self):
        result = clean_content("hello\xa0world")
        assert result == "hello world"

    def test_normalizes_crlf(self):
        result = clean_content("line1\r\nline2\rline3")
        assert "\r" not in result
        assert "line1" in result

    def test_collapses_multiple_blank_lines(self):
        result = clean_content("a\n\n\n\nb")
        assert result == "a\n\nb"

    def test_strips_indentation_after_newlines(self):
        result = clean_content("a\n    b\n\tc")
        assert result == "a\nb\nc"

    def test_splits_episode_heading_from_body(self):
        result = clean_content("Episode 6今天是开学日")
        assert result == "Episode 6\n今天是开学日"

    def test_strips_trailing_whitespace_per_line(self):
        result = clean_content("hello   \nworld  ")
        assert result == "hello\nworld"

    def test_strips_surrounding_whitespace(self):
        result = clean_content("  \n hello \n  ")
        assert result == "hello"

    def test_empty_string(self):
        result = clean_content("")
        assert result == ""

    def test_preserves_normal_text(self):
        text = "这是正常的漫画正文内容"
        result = clean_content(text)
        assert result == text
