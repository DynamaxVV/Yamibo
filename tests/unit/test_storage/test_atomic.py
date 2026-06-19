from pathlib import Path

from yamibo_mcp.storage.atomic import atomic_write_text


class TestAtomicWriteText:
    def test_writes_content_to_file(self, tmp_path):
        # Arrange
        target = tmp_path / "output.txt"
        # Act
        atomic_write_text(target, "hello world")
        # Assert
        assert target.read_text(encoding="utf-8") == "hello world"
        assert not (tmp_path / "output.txt.tmp").exists()

    def test_creates_parent_directories(self, tmp_path):
        # Arrange
        target = tmp_path / "deep" / "nested" / "dir" / "file.md"
        # Act
        atomic_write_text(target, "# Title")
        # Assert
        assert target.read_text(encoding="utf-8") == "# Title"

    def test_overwrites_existing_file(self, tmp_path):
        # Arrange
        target = tmp_path / "overwrite.txt"
        target.write_text("old content", encoding="utf-8")
        # Act
        atomic_write_text(target, "new content")
        # Assert
        assert target.read_text(encoding="utf-8") == "new content"
