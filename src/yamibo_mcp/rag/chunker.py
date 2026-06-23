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


def build_rag_chunks(
    *,
    thread_row,
    title_row,
    floor_rows: list,
    settings: Settings,
) -> list[RagChunk]:
    tid = int(thread_row["tid"])
    content_kind = thread_row["content_kind"]
    metadata_text = _thread_metadata_text(thread_row, title_row)
    title_text = (thread_row["display_title"] or thread_row["raw_title"] or "").strip()

    chunks: list[RagChunk] = []
    if title_text:
        chunks.append(
            _make_chunk(
                chunk_id=f"thread:{tid}:title",
                tid=tid,
                pid=None,
                floor_no=None,
                chunk_type="thread_title",
                thread_row=thread_row,
                title_row=title_row,
                publisher=thread_row["publisher"],
                pub_time=thread_row["pub_time"],
                title=title_text,
                metadata_text=metadata_text,
                text=title_text,
                source_uri=thread_summary_uri(tid),
            )
        )

    for floor_row in floor_rows:
        floor_text = (floor_row["content"] or "").strip()
        if not floor_text:
            continue
        floor_metadata = metadata_text
        if floor_row["quote_text"]:
            floor_metadata = f"{floor_metadata}\n引用: {floor_row['quote_text']}".strip()
        if floor_row["reply_text"]:
            floor_metadata = f"{floor_metadata}\n回复: {floor_row['reply_text']}".strip()

        floor_no = int(floor_row["floor_no"])
        pid = int(floor_row["pid"])
        source_uri = f"{thread_posts_uri(tid)}#floor={floor_no}"

        if content_kind == "novel":
            parts = split_novel_text(
                floor_text,
                max_chunk_chars=settings.rag_max_chunk_chars,
            )
        else:
            parts = [floor_text]

        for part_index, part in enumerate(parts, start=1):
            if len(part.strip()) < settings.rag_min_chunk_chars:
                continue
            chunks.append(
                _make_chunk(
                    chunk_id=f"thread:{tid}:floor:{floor_no}:part:{part_index}",
                    tid=tid,
                    pid=pid,
                    floor_no=floor_no,
                    chunk_type="floor",
                    thread_row=thread_row,
                    title_row=title_row,
                    publisher=floor_row["publisher"],
                    pub_time=floor_row["pub_time"],
                    title=title_text,
                    metadata_text=floor_metadata,
                    text=part.strip(),
                    source_uri=source_uri,
                )
            )
    return chunks


def split_novel_text(text: str, *, max_chunk_chars: int) -> list[str]:
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
    )
