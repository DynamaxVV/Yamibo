"""Validate answer citations before publishing any assistant answer text."""
from __future__ import annotations

import re

from yamibo_mcp.application.assistant_evidence_queries import validate_source_receipts

from .store import Store


SOURCE_MARKER = re.compile(r"\[来源:([^\]\n]*)\]")
RECEIPT_ID = re.compile(r"[a-f0-9]{32}")
FORUM_LINK = re.compile(r"https?://(?:www\.)?bbs\.yamibo\.com\b|/threads/\d+\b|[?&](?:ptid|tid|pid)=\d+", re.IGNORECASE)
MAX_ANSWER_CITATIONS = 100


class AnswerCitationError(ValueError):
    """The candidate answer must not be stored or shown as a checked answer."""


def check_answer_citations(store: Store, run_id: str, answer: str) -> list[dict]:
    """Check provenance, not the semantic truth of the model's interpretation.

    Greetings, clarifications, candidate lists and operation status can have no
    sources. Once this Run reads original posts, its answer must cite at least
    one of those reads. A citation always belongs to this Run, never its history.
    """
    ids = list(dict.fromkeys(SOURCE_MARKER.findall(answer)))
    # Only server-rendered source markers are accepted, not model-invented
    # links or similar-looking alternate citation syntax.
    stripped = SOURCE_MARKER.sub("", answer)
    if re.search(r"\[来源\s*[:：]", stripped) or FORUM_LINK.search(answer):
        raise AnswerCitationError("请使用完整的 [来源:receipt_id] 标记；原帖链接由服务端校验后生成，不要自行拼接。")
    if len(ids) > MAX_ANSWER_CITATIONS or any(not RECEIPT_ID.fullmatch(value) for value in ids):
        raise AnswerCitationError("引用格式无效；请使用工具返回的 [来源:receipt_id]，最多 100 个来源。")
    with store.reader() as conn:
        has_reads = conn.execute(
            "SELECT 1 FROM chat_source_receipts WHERE run_id = ? LIMIT 1", (run_id,)
        ).fetchone() is not None
    if has_reads and not ids:
        raise AnswerCitationError("本次已读取讨论原文，回答必须用 [来源:receipt_id] 标明实际采用的来源。")
    if not ids:
        return []
    result = validate_source_receipts(run_id=run_id, receipt_ids=ids, settings=store.settings)
    if not result.ok or not result.data or not result.data.get("valid"):
        code = result.error.code if result.error else "RECEIPT_VALIDATION_FAILED"
        raise AnswerCitationError(
            f"引用未通过服务端检查（{code}）；只引用本次 Run 已读取且未变化的来源，必要时重新读取。"
        )
    return result.data["sources"]
