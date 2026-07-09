from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from yamibo_mcp.config import load_settings
from yamibo_mcp.storage.atomic import atomic_write_text
from yamibo_mcp.storage.paths import StoragePaths
from yamibo_mcp.yamibo.cleaners.content_cleaner import clean_content
from yamibo_mcp.yamibo.parsers.thread_detail import normalize_rich_body_html

_RICH_ATTACHMENT_MARKERS = ("下载附件", "保存到相册", "下载次数:", "上传")


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-clean archived context.md and metadata.json in place.")
    parser.add_argument("--tid", type=int, action="append", dest="tids", help="Only process the given tid. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    parser.add_argument("--contexts-only", action="store_true", help="Only clean context.md files.")
    parser.add_argument("--metadata-only", action="store_true", help="Only clean metadata.json files.")
    args = parser.parse_args()
    if args.contexts_only and args.metadata_only:
        raise SystemExit("--contexts-only and --metadata-only are mutually exclusive")

    settings = load_settings()
    paths = StoragePaths(settings.data_dir)
    metadata_paths = _iter_metadata_paths(paths, tids=args.tids)
    processed = 0
    context_updates = 0
    metadata_updates = 0

    for metadata_path in metadata_paths:
        processed += 1
        changed_context, changed_metadata = _process_thread(
            metadata_path,
            dry_run=args.dry_run,
            clean_context=not args.metadata_only,
            clean_metadata=not args.contexts_only,
        )
        context_updates += int(changed_context)
        metadata_updates += int(changed_metadata)

    print(
        json.dumps(
            {
                "processed": processed,
                "context_updated": context_updates,
                "metadata_updated": metadata_updates,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _iter_metadata_paths(paths: StoragePaths, *, tids: list[int] | None) -> list[Path]:
    if tids:
        return [paths.thread_metadata(tid) for tid in sorted(set(int(tid) for tid in tids))]
    return sorted(paths.data_dir.glob("threads/*/metadata.json"))


def _process_thread(
    metadata_path: Path,
    *,
    dry_run: bool,
    clean_context: bool,
    clean_metadata: bool,
) -> tuple[bool, bool]:
    if not metadata_path.exists():
        return False, False
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    context_path = metadata_path.with_name("context.md")
    context_changed = False
    if clean_context and context_path.exists():
        original_context = context_path.read_text(encoding="utf-8")
        cleaned_context = _clean_context_markdown(original_context)
        context_changed = cleaned_context != original_context
        if context_changed and not dry_run:
            atomic_write_text(context_path, cleaned_context)

    metadata_changed = _clean_metadata_payload(metadata) if clean_metadata else False
    if metadata_changed and not dry_run:
        atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")

    return context_changed, metadata_changed


def _clean_context_markdown(markdown: str) -> str:
    frontmatter, body = _split_frontmatter(markdown)
    cleaned_body = clean_content(body)
    if not cleaned_body.endswith("\n"):
        cleaned_body += "\n"
    return f"{frontmatter}{cleaned_body}" if frontmatter else cleaned_body


def _split_frontmatter(markdown: str) -> tuple[str, str]:
    if not markdown.startswith("---\n"):
        return "", markdown
    marker = "\n---\n"
    end = markdown.find(marker, 4)
    if end < 0:
        return "", markdown
    frontmatter_end = end + len(marker)
    return markdown[:frontmatter_end], markdown[frontmatter_end:]


def _clean_metadata_payload(metadata: dict[str, Any]) -> bool:
    changed = False
    for floor in metadata.get("floors") or []:
        for key in ("content", "quote_text", "reply_text"):
            value = floor.get(key)
            if isinstance(value, str):
                cleaned = clean_content(value)
                if cleaned != value:
                    floor[key] = cleaned
                    changed = True
        rich_body = floor.get("rich_body_html")
        if rich_body is not None:
            rich_body_text = str(rich_body)
            if not any(marker in rich_body_text for marker in _RICH_ATTACHMENT_MARKERS):
                continue
            cleaned_rich_body = normalize_rich_body_html(rich_body_text)
            if cleaned_rich_body != rich_body:
                floor["rich_body_html"] = cleaned_rich_body
                changed = True
    return changed


if __name__ == "__main__":
    raise SystemExit(main())
