from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from yamibo_mcp.config import Settings
from yamibo_mcp.services.llm_client import LLMRequestError


@dataclass(frozen=True)
class EmbeddingProvider:
    settings: Settings

    @property
    def model(self) -> str:
        return self.settings.rag_embedding_model

    @property
    def dimensions(self) -> int:
        return self.settings.rag_embedding_dimensions

    def should_send_dimensions(self) -> bool:
        return self.model.startswith("text-embedding-3") and self.dimensions > 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.settings.rag_api_key:
            raise LLMRequestError("rag api key is not configured")
        if not texts:
            return []
        payload = {
            "model": self.model,
            "input": texts[0] if len(texts) == 1 else texts,
        }
        if self.should_send_dimensions():
            payload["dimensions"] = self.dimensions
        request = urllib.request.Request(
            self.settings.rag_base_url.rstrip("/") + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.rag_api_key}",
            },
            method="POST",
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [list(item["embedding"]) for item in data["data"]]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return EmbeddingProvider(settings)
