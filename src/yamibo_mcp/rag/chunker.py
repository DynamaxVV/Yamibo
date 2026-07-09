from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from yamibo_mcp.config import Settings
from yamibo_mcp.server.resource_uris import thread_posts_uri, thread_summary_uri


_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    tid: int
    pid: int | None
    floor_no: int | None
    chunk_type: str
    forum_id: int | None
    content_kind: str | None
    series_id: int | None
    series_key: str | None
    chapter_index: float | None
    publisher: str | None
    pub_time: str | None
    title: str | None
    metadata_text: str
    text: str
    text_hash: str
    source_uri: str
    source_tid: int
    source_pid: int | None
    source_floor_no: int | None
    cleaner_version: str
    chunker_version: str
    materializer_version: str
    source_hash: str
    generated_at: str
    quality_flags: list[str]


def build_rag_chunks(*, thread_row, title_row, floor_rows: list, settings: Settings) -> list[RagChunk]:
    from yamibo_mcp.rag.anime_dry_run import build_anime_dry_run_thread

    result = build_anime_dry_run_thread(thread_row=thread_row, floor_rows=floor_rows, structured_cleaning=True)
    tid = int(thread_row["tid"])
    chunks: list[RagChunk] = []
    for chunk in result.chunks:
        chunks.append(
            RagChunk(
                chunk_id=chunk.chunk_id,
                tid=chunk.tid,
                pid=chunk.pid,
                floor_no=chunk.floor_no,
                chunk_type=chunk.chunk_type,
                forum_id=thread_row["forum_id"],
                content_kind=thread_row["content_kind"],
                series_id=thread_row["series_id"],
                series_key=title_row["series_key"] if title_row else None,
                chapter_index=title_row["chapter_index"] if title_row else None,
                publisher=thread_row.get("publisher"),
                pub_time=thread_row.get("pub_time"),
                title=thread_row.get("display_title") or thread_row.get("raw_title"),
                metadata_text="",
                text=chunk.text,
                text_hash=chunk.chunk_id,
                source_uri=thread_summary_uri(tid) if chunk.floor_no is None else f"{thread_posts_uri(tid)}#floor={chunk.floor_no}",
                source_tid=tid,
                source_pid=chunk.pid,
                source_floor_no=chunk.floor_no,
                cleaner_version="anime-cleaner-1.2",
                chunker_version="anime-chunker-1.2",
                materializer_version="anime-rag-materializer-1.2",
                source_hash=getattr(result, "source_hash", ""),
                generated_at=getattr(result, "generated_at", ""),
                quality_flags=list(chunk.clean_rules),
            )
        )
    return chunks


def build_rag_chunks_from_preview_rows(*, thread_row, title_row, preview_rows: list[dict]) -> list[RagChunk]:
    tid = int(thread_row["tid"])
    title = thread_row.get("display_title") or thread_row.get("raw_title")
    series_key = title_row.get("series_key") if title_row else None
    chapter_index = title_row.get("chapter_index") if title_row else None
    chunks: list[RagChunk] = []
    for row in preview_rows:
        floor_no = row.get("floor_no")
        source_floor_no = row.get("source_floor_no", floor_no)
        pid = row.get("pid")
        source_pid = row.get("source_pid", pid)
        materializer_version = str(row.get("materializer_version") or "1.2")
        if not materializer_version.startswith("anime-rag-materializer-"):
            materializer_version = f"anime-rag-materializer-{materializer_version}"
        chunks.append(
            RagChunk(
                chunk_id=str(row["chunk_id"]),
                tid=tid,
                pid=None if pid is None else int(pid),
                floor_no=None if floor_no is None else int(floor_no),
                chunk_type=str(row.get("chunk_type") or "floor"),
                forum_id=thread_row["forum_id"],
                content_kind=thread_row["content_kind"],
                series_id=thread_row["series_id"],
                series_key=series_key,
                chapter_index=chapter_index,
                publisher=thread_row.get("publisher"),
                pub_time=thread_row.get("pub_time"),
                title=title,
                metadata_text="",
                text=str(row.get("text") or ""),
                text_hash=str(row.get("chunk_id") or ""),
                source_uri=thread_summary_uri(tid) if source_floor_no is None else f"{thread_posts_uri(tid)}#floor={source_floor_no}",
                source_tid=int(row.get("source_tid") or tid),
                source_pid=None if source_pid is None else int(source_pid),
                source_floor_no=None if source_floor_no is None else int(source_floor_no),
                cleaner_version=str(row.get("cleaner_version") or "anime-cleaner-1.2"),
                chunker_version=str(row.get("chunker_version") or "anime-chunker-1.2"),
                materializer_version=materializer_version,
                source_hash=str(row.get("source_hash") or ""),
                generated_at=str(row.get("generated_at") or ""),
                quality_flags=[str(flag) for flag in (row.get("quality_flags") or [])],
            )
        )
    return chunks


def split_text_for_embedding(text: str, *, max_chunk_chars: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    paragraphs = [part.strip() for part in _PARAGRAPH_BREAK_RE.split(normalized) if part.strip()]
    if not paragraphs:
        paragraphs = [normalized]

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chunk_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= max_chunk_chars:
            current = paragraph
            continue
        chunks.extend(_hard_split(paragraph, max_chunk_chars=max_chunk_chars))
    if current:
        chunks.append(current)
    return chunks


def split_novel_text(text: str, *, max_chunk_chars: int) -> list[str]:
    return split_text_for_embedding(text, max_chunk_chars=max_chunk_chars)


def _hard_split(text: str, *, max_chunk_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_chunk_chars:
            parts.append(remaining)
            break
        split_at = max(
            remaining.rfind(mark, 0, max_chunk_chars)
            for mark in ("。", "！", "？", "\n", "，", ",", " ")
        )
        if split_at <= 0:
            split_at = max_chunk_chars
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [part for part in parts if part]


def _thread_metadata_text(thread_row, title_row) -> str:
    pieces = [
        thread_row["display_title"] or thread_row["raw_title"] or "",
        title_row["group_name"] if title_row else "",
        title_row["author_guess"] if title_row else "",
        title_row["core_title_guess"] if title_row else "",
        title_row["series_key"] if title_row else "",
        title_row["chapter_name"] if title_row else "",
        title_row["chapter_title"] if title_row else "",
        thread_row["publisher"] or "",
        thread_row["category"] or "",
    ]
    return "\n".join(piece.strip() for piece in pieces if piece and str(piece).strip())


def _make_chunk(
    *,
    chunk_id: str,
    tid: int,
    pid: int | None,
    floor_no: int | None,
    chunk_type: str,
    thread_row,
    title_row,
    publisher: str | None,
    pub_time: str | None,
    title: str | None,
    metadata_text: str,
    text: str,
    source_uri: str,
) -> RagChunk:
    text_hash = hashlib.sha256(
        "\n".join(
            [
                chunk_id,
                title or "",
                metadata_text,
                text,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return RagChunk(
        chunk_id=chunk_id,
        tid=tid,
        pid=pid,
        floor_no=floor_no,
        chunk_type=chunk_type,
        forum_id=thread_row["forum_id"],
        content_kind=thread_row["content_kind"],
        series_id=thread_row["series_id"],
        series_key=title_row["series_key"] if title_row else None,
        chapter_index=title_row["chapter_index"] if title_row else None,
        publisher=publisher,
        pub_time=pub_time,
        title=title,
        metadata_text=metadata_text,
        text=text,
        text_hash=text_hash,
        source_uri=source_uri,
        source_tid=tid,
        source_pid=pid,
        source_floor_no=floor_no,
        cleaner_version="anime-cleaner-1.2",
        chunker_version="anime-chunker-1.2",
        materializer_version="anime-rag-materializer-1.2",
        source_hash="",
        generated_at="",
        quality_flags=[],
    )
