import pytest

from yamibo_mcp.storage.paths import StoragePaths


class TestSeriesDir:
    def test_returns_data_dir_series(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.series_dir()
        # Assert
        assert result == tmp_path / "series"


class TestSeriesDetailDir:
    def test_returns_series_dir_id(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.series_detail_dir(42)
        # Assert
        assert result == tmp_path / "series" / "42"


class TestThreadDir:
    def test_returns_data_dir_threads_tid(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.thread_dir(12345)
        # Assert
        assert result == tmp_path / "threads" / "12345"


class TestThreadImagesDir:
    def test_returns_thread_dir_images(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.thread_images_dir(12345)
        # Assert
        assert result == tmp_path / "threads" / "12345" / "images"


class TestStagingJobDir:
    def test_returns_staging_jobs_jobid(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.staging_job_dir("sync_abc123")
        # Assert
        assert result == tmp_path / "staging" / "jobs" / "sync_abc123"


class TestExportsDir:
    def test_uses_export_dir_when_provided(self, tmp_path):
        # Arrange
        custom = tmp_path / "custom_exports"
        paths = StoragePaths(tmp_path, export_dir=custom)
        # Act
        result = paths.exports_dir()
        # Assert
        assert result == custom

    def test_defaults_to_data_dir_exports(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.exports_dir()
        # Assert
        assert result == tmp_path / "exports"


class TestThreadExportZip:
    def test_without_series_dirname(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.thread_export_zip(999)
        # Assert
        assert result == tmp_path / "exports" / "thread_999.zip"

    def test_with_series_dirname(self, tmp_path):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.thread_export_zip(999, series_dirname="my_series")
        # Assert
        assert result == tmp_path / "exports" / "my_series" / "thread_999.zip"

    @pytest.mark.parametrize(
        "zip_basename,expected_name",
        [
            pytest.param(None, "thread_999.zip", id="default_basename"),
            pytest.param("custom.zip", "custom.zip", id="custom_basename"),
        ],
    )
    def test_zip_basename(self, tmp_path, zip_basename, expected_name):
        # Arrange
        paths = StoragePaths(tmp_path)
        # Act
        result = paths.thread_export_zip(999, zip_basename=zip_basename)
        # Assert
        assert result.name == expected_name
