from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from yamibo_mcp.config import Settings
from yamibo_mcp.services.llm_client import LLMRequestError


LOG = logging.getLogger(__name__)
_EMBEDDING_BATCH_SIZE = 32


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
        batch_count = (len(texts) + _EMBEDDING_BATCH_SIZE - 1) // _EMBEDDING_BATCH_SIZE
        embeddings: list[list[float]] = []
        for batch_index, start in enumerate(range(0, len(texts), _EMBEDDING_BATCH_SIZE), start=1):
            batch_texts = texts[start : start + _EMBEDDING_BATCH_SIZE]
            embeddings.extend(
                self._embed_text_batch(
                    batch_texts,
                    batch_index=batch_index,
                    batch_count=batch_count,
                )
            )
        return embeddings

    def _embed_text_batch(
        self,
        texts: list[str],
        *,
        batch_index: int,
        batch_count: int,
    ) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts[0] if len(texts) == 1 else texts,
        }
        if self.should_send_dimensions():
            payload["dimensions"] = self.dimensions
        if getattr(self.settings, "rag_debug_indexing", False):
            LOG.warning(
                "[RAG-DEBUG] embedding request url=%s batch=%s/%s model=%s input_count=%s dimensions=%s payload_keys=%s",
                self.settings.rag_base_url.rstrip("/") + "/embeddings",
                batch_index,
                batch_count,
                self.model,
                len(texts),
                payload.get("dimensions"),
                sorted(payload.keys()),
            )
        request = urllib.request.Request(
            self.settings.rag_base_url.rstrip("/") + "/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.rag_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
                body = response.read().decode("utf-8")
                if getattr(self.settings, "rag_debug_indexing", False):
                    LOG.warning(
                        "[RAG-DEBUG] embedding response status=%s batch=%s/%s body_preview=%s",
                        getattr(response, "status", None),
                        batch_index,
                        batch_count,
                        _truncate(body),
                    )
                data = json.loads(body)
        except urllib.error.HTTPError as exc:
            body = _read_error_body(exc)
            if getattr(self.settings, "rag_debug_indexing", False):
                LOG.warning(
                    "[RAG-DEBUG] embedding http error code=%s reason=%s batch=%s/%s body_preview=%s",
                    exc.code,
                    exc.reason,
                    batch_index,
                    batch_count,
                    _truncate(body),
                )
            raise LLMRequestError(f"embedding request failed with HTTP {exc.code}: {exc.reason}") from exc
        except Exception as exc:
            if getattr(self.settings, "rag_debug_indexing", False):
                LOG.warning(
                    "[RAG-DEBUG] embedding request error type=%s batch=%s/%s error=%r",
                    exc.__class__.__name__,
                    batch_index,
                    batch_count,
                    exc,
                )
            raise

        if "data" not in data:
            if getattr(self.settings, "rag_debug_indexing", False):
                LOG.warning(
                    "[RAG-DEBUG] embedding response missing data field batch=%s/%s keys=%s body_preview=%s",
                    batch_index,
                    batch_count,
                    sorted(data.keys()),
                    _truncate(json.dumps(data, ensure_ascii=False)),
                )
            raise LLMRequestError(f"embedding response missing data field: keys={sorted(data.keys())}")
        try:
            return [list(item["embedding"]) for item in data["data"]]
        except KeyError as exc:
            if getattr(self.settings, "rag_debug_indexing", False):
                LOG.warning(
                    "[RAG-DEBUG] embedding response item missing embedding field batch=%s/%s keys=%s body_preview=%s",
                    batch_index,
                    batch_count,
                    sorted(data.keys()),
                    _truncate(json.dumps(data, ensure_ascii=False)),
                )
            raise LLMRequestError("embedding response missing embedding field") from exc


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _truncate(value: str, *, limit: int = 800) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return EmbeddingProvider(settings)
