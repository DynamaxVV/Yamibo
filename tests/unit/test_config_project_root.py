from __future__ import annotations

from pathlib import Path

from yamibo_mcp.config import _project_root


def test_project_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv('YAMIBO_PROJECT_ROOT', str(tmp_path))
    assert _project_root() == tmp_path.resolve()


def test_project_root_falls_back_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv('YAMIBO_PROJECT_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    assert _project_root() == tmp_path.resolve()
