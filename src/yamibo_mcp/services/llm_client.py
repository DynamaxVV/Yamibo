from __future__ import annotations

import json
import urllib.error
import urllib.request

from yamibo_mcp.config import Settings


class LLMRequestError(ValueError):
    pass


def openai_compatible_chat(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
) -> dict[str, object]:
    if not settings.llm_api_key:
        raise LLMRequestError("llm api key is not configured")

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    request = urllib.request.Request(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.llm_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMRequestError(f"{exc.code} {exc.reason}: {detail[:500]}") from exc

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMRequestError("openai-compatible response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise LLMRequestError("openai-compatible response missing message")
    content = message.get("content")
    if content is None:
        raise LLMRequestError("openai-compatible response missing content")
    return {"model": str(data.get("model") or settings.llm_model), "content": str(content), "raw": data}
