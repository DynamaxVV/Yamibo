from __future__ import annotations

from pathlib import Path


def thread_context_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/context"


def thread_metadata_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/metadata"


def thread_export_uri(tid: int) -> str:
    return f"yamibo://threads/{tid}/export"


def series_index_uri() -> str:
    return "yamibo://series/index"


def series_chapters_uri(series_id: int) -> str:
    return f"yamibo://series/{series_id}/chapters"


def parse_resource_uri(uri: str) -> tuple[str, int | None, str]:
    # 资源 URI 先收敛成固定格式，避免 Web/Server/Worker 各自拼路径。
    prefix = "yamibo://"
    if not uri.startswith(prefix):
        raise ValueError(f"unsupported resource uri: {uri}")
    remainder = uri[len(prefix) :]
    parts = remainder.split("/")
    if len(parts) == 3 and parts[0] == "threads" and parts[1].isdigit():
        return parts[0], int(parts[1]), parts[2]
    if len(parts) == 2 and parts[0] == "series" and parts[1] == "index":
        return parts[0], None, parts[1]
    if len(parts) == 3 and parts[0] == "series" and parts[1].isdigit() and parts[2] == "chapters":
        return parts[0], int(parts[1]), parts[2]
    raise ValueError(f"unsupported resource uri: {uri}")


def guess_content_type(kind: str) -> str:
    if kind == "context":
        return "text/markdown"
    if kind == "metadata":
        return "application/json"
    if kind == "export":
        return "application/zip"
    if kind == "index":
        return "text/markdown"
    if kind == "chapters":
        return "application/json"
    raise ValueError(f"unsupported resource kind: {kind}")


def build_resource_payload(*, uri: str, path: Path, content_type: str) -> dict[str, object]:
    return {
        "uri": uri,
        "content_type": content_type,
        "path": str(path),
        "exists": path.exists(),
    }
