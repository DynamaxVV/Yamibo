import pytest

from yamibo_mcp.yamibo.title.normalizer import (
    normalize_display_title,
    normalize_series_key,
    strip_discuz_suffix,
)


class TestNormalizeDisplayTitle:
    def test_nfkc_normalization(self):
        result = normalize_display_title("ＴＥＳＴ")
        assert result == "TEST"

    def test_fullwidth_slash(self):
        result = normalize_display_title("a／b")
        assert result == "a/b"

    def test_fullwidth_pipe(self):
        result = normalize_display_title("a｜b")
        assert result == "a|b"

    def test_whitespace_compression(self):
        result = normalize_display_title("a  \t  b")
        assert result == "a b"

    def test_strip_surrounding_whitespace(self):
        result = normalize_display_title("  hello  ")
        assert result == "hello"


class TestNormalizeSeriesKey:
    def test_traditional_to_simplified(self):
        result = normalize_series_key("靈魂")
        assert "灵" in result

    def test_lowercases(self):
        result = normalize_series_key("ABC")
        assert result == result.lower()

    def test_removes_wrappers(self):
        result = normalize_series_key("《漫画名》")
        assert "《" not in result
        assert "》" not in result
        assert "漫画名" in result

    def test_removes_chapter_noise(self):
        result = normalize_series_key("漫画名 第13话")
        assert "第" not in result or "13" not in result

    def test_removes_punctuation(self):
        result = normalize_series_key("漫画！名？")
        assert "!" not in result
        assert "?" not in result

    def test_fullwidth_exclamation(self):
        result = normalize_series_key("漫画！")
        assert "!" not in result

    def test_removes_spaces(self):
        result = normalize_series_key("a b c")
        assert " " not in result

    def test_empty_string(self):
        assert normalize_series_key("") == ""


class TestStripDiscuzSuffix:
    def test_removes_suffix(self):
        title = "漫画名 - 中文百合漫画区 - 百合会 - Powered by Discuz!"
        result = strip_discuz_suffix(title)
        assert result == "漫画名"

    def test_no_suffix_unchanged(self):
        result = strip_discuz_suffix("纯标题")
        assert result == "纯标题"
