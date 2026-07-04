from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


class StoragePaths:
    def __init__(self, data_dir: Path, *, export_dir: Path | None = None, novel_txt_export_dir: Path | None = None):
        self.data_dir = data_dir
        self._export_dir = export_dir
        self._novel_txt_export_dir = novel_txt_export_dir

    def series_dir(self) -> Path:
        return self.data_dir / "series"

    def series_detail_dir(self, series_id: int) -> Path:
        return self.series_dir() / str(series_id)

    def series_index(self) -> Path:
        return self.series_dir() / "index.md"

    def series_chapters(self, series_id: int) -> Path:
        return self.series_detail_dir(series_id) / "chapters.json"

    def thread_dir(self, tid: int) -> Path:
        return self.data_dir / "threads" / str(tid)

    def thread_context(self, tid: int) -> Path:
        return self.thread_dir(tid) / "context.md"

    def thread_metadata(self, tid: int) -> Path:
        return self.thread_dir(tid) / "metadata.json"

    def thread_images_dir(self, tid: int) -> Path:
        return self.thread_dir(tid) / "images"

    def thread_rag_cleaned_json(self, tid: int, version: str) -> Path:
        return self.thread_dir(tid) / f"rag_cleaned.{version}.json"

    def thread_rag_cleaned_markdown(self, tid: int, version: str) -> Path:
        return self.thread_dir(tid) / f"rag_cleaned.{version}.md"

    def thread_rag_chunks_preview_jsonl(self, tid: int, version: str) -> Path:
        return self.thread_dir(tid) / f"rag_chunks.preview.{version}.jsonl"

    def thread_rag_materialized_marker(self, tid: int, version: str) -> Path:
        return self.thread_dir(tid) / f"rag_materialized.{version}.json"

    def shared_asset_path(self, url: str) -> Path:
        parsed = urlparse(url)
        host = (parsed.netloc or "unknown-host").replace(":", "_")
        raw_parts = [part for part in parsed.path.split("/") if part not in {"", ".", ".."}]
        safe_parts = [part[:120] for part in raw_parts] or ["asset.bin"]
        return self.data_dir / "shared" / host / Path(*safe_parts)

    def staging_job_dir(self, job_id: str) -> Path:
        return self.data_dir / "staging" / "jobs" / job_id

    def staging_job_images_dir(self, job_id: str) -> Path:
        return self.staging_job_dir(job_id) / "images"

    def exports_dir(self) -> Path:
        return self._export_dir or (self.data_dir / "exports")

    def novel_txt_exports_dir(self) -> Path:
        return self._novel_txt_export_dir or (self.data_dir / "novel_exports")

    def thread_export_zip(self, tid: int, *, series_dirname: str | None = None, zip_basename: str | None = None) -> Path:
        base = self.exports_dir()
        if series_dirname:
            base = base / series_dirname
        return base / (zip_basename or f"thread_{tid}.zip")

    def thread_export_txt(self, tid: int, *, txt_basename: str | None = None) -> Path:
        return self.novel_txt_exports_dir() / (txt_basename or f"thread_{tid}.txt")

    def thread_export_txt_manifest(self, tid: int, *, txt_basename: str | None = None) -> Path:
        txt_path = self.thread_export_txt(tid, txt_basename=txt_basename)
        return txt_path.with_suffix(txt_path.suffix + ".export.json")
