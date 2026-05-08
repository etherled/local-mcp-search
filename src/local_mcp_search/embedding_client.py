from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from .config import Settings

logger = logging.getLogger("local_mcp_search.embedding_client")


def _build_no_proxy_handler() -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({})


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.embedding_base_url.rstrip("/")
        self._model = settings.embedding_model
        self._timeout = settings.embedding_timeout_seconds
        self._opener = urllib.request.build_opener(_build_no_proxy_handler())

    def _request(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._base_url}/embeddings"
        body = json.dumps({"model": self._model, "input": texts}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = self._opener.open(req, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(errors="replace")[:500]
            except Exception:
                pass
            logger.warning("embed_texts: %s %s — body: %s", url, exc, detail)
            raise
        except Exception as exc:
            logger.warning("embed_texts: %s failed: %s", url, exc)
            raise
        data = json.loads(resp.read().decode())
        return [item["embedding"] for item in data["data"]]

    # bge-base-zh has a 512-token context window; in embedding mode llama-server
    # forces n_batch == n_ubatch == 512, so a single chunk that tokenizes to
    # >512 tokens hard-fails with HTTP 500. We truncate by character count as a
    # safe upper bound: 1000 chars (≈ <500 tokens for mixed Chinese/code).
    _MAX_INPUT_CHARS = 1000

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        truncated = [t[: self._MAX_INPUT_CHARS] for t in texts]
        return self._request(truncated)

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
            logger.info("health_probe(embedding): probing %s...", self._base_url)
            start = time.monotonic()
            self._request(["ping"])
            elapsed = time.monotonic() - start
            logger.info("health_probe(embedding): response in %.1fms", elapsed * 1000)
            result["reachable"] = True
            result["latency_ms"] = round(elapsed * 1000)
            result["ok"] = True
        except Exception as exc:
            logger.warning("health_probe(embedding): failed: %s", exc)
            result["error"] = str(exc)
        return result
