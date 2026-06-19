import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from yamibo_mcp.services.title_hints import (
    load_title_hints,
    update_title_hints,
    write_title_hints,
)


@dataclass(frozen=True)
class FakeSettings:
    project_root: Path
    data_dir: Path
    title_hints_path: Path
    common_scanlation_groups: list
    common_authors: list


def _make_settings(tmp_path: Path, *, groups=None, authors=None) -> FakeSettings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return FakeSettings(
        project_root=tmp_path,
        data_dir=data_dir,
        title_hints_path=data_dir / "title_hints.json",
        common_scanlation_groups=groups or [],
        common_authors=authors or [],
    )


class TestLoadTitleHints:
    def test_loads_from_existing_file(self, tmp_path):
        settings = _make_settings(tmp_path)
        hints_path = settings.data_dir / "title_hints.json"
        hints_path.write_text(json.dumps({
            "scanlation_groups": ["组A", "组B"],
            "authors": ["作者X"],
        }), encoding="utf-8")
        result = load_title_hints(settings)
        assert "组A" in result["scanlation_groups"]
        assert "作者X" in result["authors"]

    def test_creates_file_when_missing(self, tmp_path):
        settings = _make_settings(tmp_path, groups=["默认组"], authors=["默认作者"])
        result = load_title_hints(settings)
        assert "默认组" in result["scanlation_groups"]
        assert (settings.data_dir / "title_hints.json").exists()

    def test_handles_corrupt_json(self, tmp_path):
        settings = _make_settings(tmp_path)
        (settings.data_dir / "title_hints.json").write_text("NOT JSON", encoding="utf-8")
        result = load_title_hints(settings)
        assert isinstance(result["scanlation_groups"], list)


class TestWriteTitleHints:
    def test_writes_valid_json(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": ["组1"], "authors": ["作者1"]})
        data = json.loads((settings.data_dir / "title_hints.json").read_text(encoding="utf-8"))
        assert "组1" in data["scanlation_groups"]
        assert "updated_at" in data

    def test_deduplicates(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": ["A", "A", "B"], "authors": []})
        data = json.loads((settings.data_dir / "title_hints.json").read_text(encoding="utf-8"))
        assert data["scanlation_groups"].count("A") == 1


class TestUpdateTitleHints:
    def test_adds_new_group(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": ["旧组"], "authors": []})
        result = update_title_hints(settings, group_name="新组")
        assert "新组" in result["scanlation_groups"]
        assert "旧组" in result["scanlation_groups"]

    def test_adds_new_author(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": [], "authors": ["旧作者"]})
        result = update_title_hints(settings, author_guess="新作者")
        assert "新作者" in result["authors"]

    def test_none_values_not_added(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": ["A"], "authors": ["B"]})
        result = update_title_hints(settings, group_name=None, author_guess=None)
        assert result["scanlation_groups"] == ["A"]
        assert result["authors"] == ["B"]

    def test_deduplicates_on_update(self, tmp_path):
        settings = _make_settings(tmp_path)
        write_title_hints(settings, {"scanlation_groups": ["已有组"], "authors": []})
        result = update_title_hints(settings, group_name="已有组")
        assert result["scanlation_groups"].count("已有组") == 1
