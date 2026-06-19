from __future__ import annotations

import json
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
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        settings.llm_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.llm_api_key}",
        },
        method="POST",
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return {"model": settings.llm_model, "content": content, "raw": data}
