from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.storage.atomic import atomic_write_text


ANIME_FORUM_ID = 5
DEFAULT_VERSION = "1.2"
QUOTE_AUTHOR_PATTERNS = (
    re.compile(
        r"^\s*(?P<author>.+?)\s+在\s+"
        r"(?P<date>\d{4}[/-]\d{1,2}[/-]\d{1,2})\s+"
        r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*发表",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<author>.+?)\s+发表于\s+"
        r"(?P<date>\d{4}-\d{1,2}-\d{1,2})(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*原帖由\s+(?P<author>.+?)\s+(?:于|在).{0,80}?发表", re.IGNORECASE),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Obsidian Markdown files for archived anime forum threads.")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--tid", type=int, action="append", dest="tids")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sample-check", type=int, default=5)
    args = parser.parse_args()

    settings = load_settings()
    output_dir = args.output_dir or (settings.data_dir / "obsidian_anime")
    output_dir.mkdir(parents=True, exist_ok=True)

    results = generate_obsidian_files(
        data_dir=settings.data_dir,
        output_dir=output_dir,
        version=args.version,
        tids=set(args.tids or []),
        limit=args.limit,
    )
    sample = sample_database_threads(
        settings=settings,
        output_dir=output_dir,
        version=args.version,
        sample_size=args.sample_check,
    )
    report = {
        "ok": True,
        "output_dir": str(output_dir),
        "generated": len(results),
        "version": args.version,
        "sample_check": sample,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def generate_obsidian_files(
    *,
    data_dir: Path,
    output_dir: Path,
    version: str,
    tids: set[int],
    limit: int | None,
) -> list[Path]:
    generated: list[Path] = []
    thread_dirs = sorted((data_dir / "threads").iterdir(), key=lambda path: int(path.name) if path.name.isdigit() else -1)
    for thread_dir in thread_dirs:
        if not thread_dir.is_dir() or not thread_dir.name.isdigit():
            continue
        tid = int(thread_dir.name)
        if tids and tid not in tids:
            continue
        cleaned_path = thread_dir / f"rag_cleaned.{version}.json"
        if not cleaned_path.exists():
            continue
        cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
        if int(cleaned.get("forum_id") or 0) != ANIME_FORUM_ID:
            continue
        metadata = _load_json_if_exists(thread_dir / "metadata.json")
        markdown = render_obsidian_thread(cleaned=cleaned, metadata=metadata)
        output_path = output_dir / f"{tid}.md"
        atomic_write_text(output_path, markdown)
        generated.append(output_path)
        if limit is not None and len(generated) >= limit:
            break
    return generated


def render_obsidian_thread(*, cleaned: dict[str, Any], metadata: dict[str, Any] | None) -> str:
    tid = int(cleaned["tid"])
    title = str(cleaned.get("title") or tid)
    floors = sorted(cleaned.get("floors") or [], key=lambda floor: int(floor.get("floor_no") or 0))
    if not floors:
        floors = [_synthetic_empty_floor(cleaned)]
    metadata_floors = {
        int(floor.get("pid")): floor
        for floor in (metadata or {}).get("floors", [])
        if floor.get("pid") is not None
    }
    floor_lookup = _build_floor_lookup(floors)
    lines = _render_frontmatter(cleaned)
    lines.extend(["", f"# {title}", ""])

    for index, floor in enumerate(floors):
        floor_no = int(floor.get("floor_no") or 0)
        pid = int(floor.get("pid") or 0)
        publisher = str(floor.get("publisher") or "unknown")
        pub_time = _format_heading_time(floor.get("pub_time"))
        heading = f"## {floor_no}F · {_obsidian_user_link(publisher)}"
        if pub_time:
            heading += f" · {pub_time}"
        lines.extend([heading, ""])

        quote = str(floor.get("cleaned_quote_text") or "").strip()
        if quote:
            quote_author = _extract_quote_author(str(floor.get("raw_quote_preview") or ""))
            target_floor = _find_quoted_floor(
                quote_author=quote_author,
                raw_quote_preview=str(floor.get("raw_quote_preview") or ""),
                floor_lookup=floor_lookup,
            )
            lines.extend(_render_quote_callout(quote, quote_author=quote_author, target_floor=target_floor))
            lines.append("")

        body = str(floor.get("cleaned_text") or "").strip()
        if body:
            lines.extend([body, ""])
        elif floor.get("has_images"):
            lines.extend(["[image-only floor]", ""])
        else:
            lines.extend(["[empty floor]", ""])

        image_lines = _render_images(tid=tid, metadata_floor=metadata_floors.get(pid))
        if image_lines:
            lines.extend(image_lines)
            lines.append("")

        lines.append(f"^f{floor_no}")
        if index != len(floors) - 1:
            lines.extend(["", "---", ""])

    return "\n".join(lines).rstrip() + "\n"


def _render_frontmatter(cleaned: dict[str, Any]) -> list[str]:
    title = str(cleaned.get("title") or cleaned.get("tid"))
    category = str(cleaned.get("category") or "").strip()
    floor_count = int(cleaned.get("floor_count") or len(cleaned.get("floors") or []))
    tags = [category] if category else []
    fields: list[tuple[str, Any]] = [
        ("tid", int(cleaned["tid"])),
        ("title", title),
        ("aliases", [title]),
        ("publisher", str(cleaned.get("publisher") or "")),
        ("date", _frontmatter_date(cleaned.get("pub_time"))),
        ("reply_count", max(0, floor_count - 1)),
        ("board", "动漫区"),
        ("tags", tags),
        ("ai_summary", ""),
    ]
    lines = ["---"]
    for key, value in fields:
        if key == "date":
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return lines


def _synthetic_empty_floor(cleaned: dict[str, Any]) -> dict[str, Any]:
    return {
        "pid": 0,
        "floor_no": 1,
        "publisher": cleaned.get("publisher") or "unknown",
        "pub_time": cleaned.get("pub_time"),
        "has_images": False,
        "cleaned_text": "[empty archived thread]",
        "cleaned_quote_text": "",
        "raw_quote_preview": "",
    }


def _render_quote_callout(
    quote: str,
    *,
    quote_author: str | None,
    target_floor: int | None,
) -> list[str]:
    if target_floor is not None and quote_author:
        title = f"引用 [[#^f{target_floor}|{target_floor}F {_safe_display(quote_author)}]] 的发言："
    elif quote_author:
        title = f"引用 {_obsidian_user_link(quote_author)} 的发言："
    else:
        title = "引用内容："
    lines = [f"> [!quote] {title}"]
    for line in quote.splitlines():
        lines.append(f"> {line}" if line else ">")
    return lines


def _render_images(*, tid: int, metadata_floor: dict[str, Any] | None) -> list[str]:
    if not metadata_floor:
        return []
    lines: list[str] = []
    for slot in metadata_floor.get("image_slots") or []:
        local_path = slot.get("local_path")
        if not local_path:
            continue
        relpath = str(local_path)
        if relpath.startswith("images/"):
            obsidian_path = f"../threads/{tid}/{relpath}"
        else:
            obsidian_path = f"../{relpath}"
        lines.append(f"![[{obsidian_path}]]")
    return lines


def _build_floor_lookup(floors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup: list[dict[str, Any]] = []
    for floor in floors:
        lookup.append(
            {
                "floor_no": int(floor.get("floor_no") or 0),
                "publisher": str(floor.get("publisher") or ""),
                "pub_date": _date_key(floor.get("pub_time")),
            }
        )
    return lookup


def _find_quoted_floor(
    *,
    quote_author: str | None,
    raw_quote_preview: str,
    floor_lookup: list[dict[str, Any]],
) -> int | None:
    if not quote_author:
        return None
    quote_date = _extract_quote_date_key(raw_quote_preview)
    candidates = [row for row in floor_lookup if row["publisher"] == quote_author]
    if quote_date:
        dated = [row for row in candidates if row["pub_date"] == quote_date]
        if len(dated) == 1:
            return int(dated[0]["floor_no"])
    if len(candidates) == 1:
        return int(candidates[0]["floor_no"])
    return None


def _extract_quote_author(raw_quote_preview: str) -> str | None:
    preview = raw_quote_preview.strip()
    if not preview:
        return None
    first_line = preview.splitlines()[0].strip()
    for pattern in QUOTE_AUTHOR_PATTERNS:
        match = pattern.search(first_line)
        if match:
            author = re.sub(r"\s+", " ", match.group("author")).strip(" :：")
            return author or None
    return None


def _extract_quote_date_key(raw_quote_preview: str) -> str | None:
    first_line = raw_quote_preview.strip().splitlines()[0].strip() if raw_quote_preview.strip() else ""
    for pattern in QUOTE_AUTHOR_PATTERNS[:2]:
        match = pattern.search(first_line)
        if match:
            return match.group("date").replace("/", "-")
    return None


def _obsidian_user_link(username: str) -> str:
    username = username.strip() or "unknown"
    target = "用户_" + re.sub(r"[\[\]#^|/\\]", "_", username)
    if target == f"用户_{username}":
        return f"[[{target}]]"
    return f"[[{target}|{_safe_display(username)}]]"


def _safe_display(value: str) -> str:
    return value.replace("|", "｜").replace("[", "［").replace("]", "］")


def _frontmatter_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return '""'
    parsed = _parse_datetime(text)
    return parsed.isoformat() if parsed else text


def _format_heading_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = _parse_datetime(text)
    if parsed:
        return parsed.strftime("%Y-%m-%d %H:%M")
    return text


def _date_key(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = _parse_datetime(text)
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    match = re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text)
    return match.group(0).replace("/", "-") if match else None


def _parse_datetime(text: str) -> datetime | None:
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def sample_database_threads(*, settings: Any, output_dir: Path, version: str, sample_size: int) -> list[dict[str, Any]]:
    if sample_size <= 0:
        return []
    if settings.db_backend == "postgres":
        return _sample_database_threads_postgres(
            settings=settings,
            output_dir=output_dir,
            version=version,
            sample_size=sample_size,
        )
    return _sample_database_threads_sqlite(
        settings=settings,
        output_dir=output_dir,
        version=version,
        sample_size=sample_size,
    )


def _sample_database_threads_postgres(
    *,
    settings: Any,
    output_dir: Path,
    version: str,
    sample_size: int,
) -> list[dict[str, Any]]:
    from yamibo_mcp.db.connection import connect

    conn = connect(settings)
    try:
        rows = conn.execute(
            """
            SELECT t.tid, t.raw_title, t.category, COUNT(f.pid) AS floor_count
            FROM threads t
            LEFT JOIN floors f ON f.tid = t.tid
            WHERE t.forum_id = :forum_id
            GROUP BY t.tid, t.raw_title, t.category
            ORDER BY random()
            LIMIT :limit
            """,
            {"forum_id": ANIME_FORUM_ID, "limit": sample_size},
        ).fetchall()
    finally:
        conn.close()
    return [_sample_row_payload(row, settings=settings, output_dir=output_dir, version=version) for row in rows]


def _sample_database_threads_sqlite(
    *,
    settings: Any,
    output_dir: Path,
    version: str,
    sample_size: int,
) -> list[dict[str, Any]]:
    db_path = settings.db_path
    if not db_path.exists():
        return []
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    try:
        rows = raw.execute(
            """
            SELECT t.tid, t.raw_title, t.category, COUNT(f.pid) AS floor_count
            FROM threads t
            LEFT JOIN floors f ON f.tid = t.tid
            WHERE t.forum_id = ?
            GROUP BY t.tid, t.raw_title, t.category
            ORDER BY random()
            LIMIT ?
            """,
            (ANIME_FORUM_ID, sample_size),
        ).fetchall()
    finally:
        raw.close()
    return [_sample_row_payload(row, settings=settings, output_dir=output_dir, version=version) for row in rows]


def _sample_row_payload(row: Any, *, settings: Any, output_dir: Path, version: str) -> dict[str, Any]:
    tid = int(row["tid"])
    output_path = output_dir / f"{tid}.md"
    cleaned_path = settings.data_dir / "threads" / str(tid) / f"rag_cleaned.{version}.json"
    text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    return {
        "tid": tid,
        "title": row["raw_title"],
        "category": row["category"],
        "db_floor_count": int(row["floor_count"] or 0),
        "has_cleaned_json": cleaned_path.exists(),
        "output_exists": output_path.exists(),
        "has_frontmatter": text.startswith("---\n"),
        "has_user_links": "[[用户_" in text,
        "has_block_ids": "^f1" in text,
        "uses_requested_image_path": f"![[../threads/{tid}/images/" in text,
        "has_quote_callout": "> [!quote]" in text,
    }


if __name__ == "__main__":
    raise SystemExit(main())
