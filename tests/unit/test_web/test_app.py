from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from yamibo_mcp.web.app import WebHandler


def test_safe_resolve_rejects_path_escape(tmp_path: Path):
    handler = object.__new__(WebHandler)

    assert handler._safe_resolve(tmp_path, "../escape.txt") is None


def test_safe_resolve_accepts_nested_file(tmp_path: Path):
    handler = object.__new__(WebHandler)

    resolved = handler._safe_resolve(tmp_path, "threads/42/context.md")

    assert resolved == (tmp_path / "threads" / "42" / "context.md").resolve()


def test_guess_content_type_handles_known_text_and_font_suffixes(tmp_path: Path):
    handler = object.__new__(WebHandler)

    assert handler._guess_content_type(tmp_path / "index.html") == "text/html; charset=utf-8"
    assert handler._guess_content_type(tmp_path / "index.js") == "text/javascript; charset=utf-8"
    assert handler._guess_content_type(tmp_path / "font.woff2") == "font/woff2"
    assert handler._guess_content_type(tmp_path / "font.ttf") == "font/ttf"


def test_serve_fonts_reads_from_data_dir(tmp_path: Path):
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    font_file = fonts_dir / "文黑体.TTF"
    font_file.write_bytes(b"font-bytes")

    handler = object.__new__(WebHandler)
    handler.settings = SimpleNamespace(data_dir=tmp_path)
    captured = {}

    def _send_bytes(body, content_type, status=None):
        captured["body"] = body
        captured["content_type"] = content_type
        captured["status"] = status

    handler._send_bytes = _send_bytes
    handler._send_text = lambda *args, **kwargs: captured.update({"text": args[0], "status": args[1]})

    handler._serve_fonts("/fonts/%E6%96%87%E9%BB%91%E4%BD%93.TTF")

    assert captured["body"] == b"font-bytes"
    assert captured["content_type"] == "font/ttf"
