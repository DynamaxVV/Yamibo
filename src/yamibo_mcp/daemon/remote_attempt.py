from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_remote_details(details: Any) -> dict[str, Any]:
    """Keep only diagnostic metadata; never persist cookies, credentials, or HTML."""
    if not isinstance(details, dict):
        return {}
    allowed = {
        "url", "status_code", "page_type", "retryable", "last_error_type",
        "attempts", "timeout_seconds", "duration_ms", "candidate_tier",
        "forum_delay_ms", "network_delay_ms", "prompt_text",
    }
    result = {key: value for key, value in details.items() if key in allowed and value is not None}
    result.pop("html", None)
    result.pop("html_snippet", None)
    return result


def outcome_for_error(error_code: str | None, message: str | None = None) -> str:
    text = f"{error_code or ''} {message or ''}".lower()
    if "444" in text or "http_444" in text:
        return "http_444"
    if "soft_block" in text or "soft block" in text or "captcha" in text or "cf challenge" in text:
        return "soft_block"
    if "429" in text or "rate" in text and "limit" in text:
        return "rate_limited"
    if "permission" in text or "login" in text:
        return "permission_required"
    if "timeout" in text:
        return "timeout"
    if "connection" in text or "remote_fetch" in text:
        return "connection_error"
    return "error"


def build_attempt(*, source: str, node: str | None = None, account_id: str | None = None,
                  retry_hint: int | None = None, **fields: Any) -> dict[str, Any]:
    attempt: dict[str, Any] = {
        "source": source,
        "started_at": fields.pop("started_at", now_iso()),
        "node": node,
        "account_id": account_id,
        "selection_retry": retry_hint,
        "outcome": fields.pop("outcome", "pending"),
    }
    attempt.update({key: value for key, value in fields.items() if value is not None})
    return attempt
