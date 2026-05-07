from __future__ import annotations

import logging
import time
from typing import Any

from openai import OpenAI

from .config import Settings

logger = logging.getLogger("local_mcp_search.embedding_client")


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_api_key:
            raise ValueError(
                "EMBEDDING_API_KEY is required. Set it explicitly or start via run-local-mcp-search.ps1."
            )
        self._client = OpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout=settings.embedding_timeout_seconds,
        )
        self._model = settings.embedding_model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0]

    def health_probe(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "reachable": False,
            "model_found": False,
            "model_name": self._model,
            "latency_ms": 0,
            "error": None,
        }
        try:
            logger.info("health_probe(embedding): probing %s...", self._client.base_url)
            start = time.monotonic()
            response = self._client.get("models", cast_to=dict[str, Any])
            elapsed = time.monotonic() - start
            logger.info("health_probe(embedding): response in %.1fms", elapsed * 1000)
            result["reachable"] = True
            result["latency_ms"] = round(elapsed * 1000)
            model_ids = [m.get("id", "") for m in response.get("data", [])]
            if self._model in model_ids:
                result["model_found"] = True
                result["ok"] = True
            else:
                result["error"] = (
                    f"Model '{self._model}' not found on server. "
                    f"Available: {model_ids[:5]}"
                )
        except Exception as exc:
            logger.warning("health_probe(embedding): failed: %s", exc)
            result["error"] = str(exc)
        return result
