from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings

logger = logging.getLogger("local_mcp_search.embedding_client")


def _build_no_proxy_handler() -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({})


def _read_http_error_detail(exc: urllib.error.HTTPError) -> str:
    cached = getattr(exc, "_local_search_detail", None)
    if isinstance(cached, str):
        return cached
    try:
        detail = exc.read().decode(errors="replace")
    except Exception:
        detail = ""
    try:
        setattr(exc, "_local_search_detail", detail)
    except Exception:
        pass
    return detail


class EmbeddingClient:
    # bge-base-zh has a 512-token context window; in embedding mode llama-server
    # forces n_batch == n_ubatch == 512. We therefore stay conservative on input
    # size and degrade gracefully if one chunk still trips the server limit.
    _MAX_INPUT_CHARS = 800
    _MIN_INPUT_CHARS = 200

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
            detail = _read_http_error_detail(exc)[:500]
            logger.warning("embed_texts: %s %s — body: %s", url, exc, detail)
            raise
        except Exception as exc:
            logger.warning("embed_texts: %s failed: %s", url, exc)
            raise
        data = json.loads(resp.read().decode())
        return [item["embedding"] for item in data["data"]]

    @staticmethod
    def _is_input_too_large_error(exc: urllib.error.HTTPError) -> bool:
        detail = _read_http_error_detail(exc)
        if not detail:
            return False
        detail_lower = detail.lower()
        return "too large to process" in detail_lower or "current batch size: 512" in detail_lower

    @staticmethod
    def _shrink_text(text: str) -> str:
        if len(text) <= EmbeddingClient._MIN_INPUT_CHARS:
            return text
        target_len = max(EmbeddingClient._MIN_INPUT_CHARS, int(len(text) * 0.75))
        split_at = text.rfind("\n", 0, target_len)
        if split_at >= EmbeddingClient._MIN_INPUT_CHARS:
            return text[:split_at]
        return text[:target_len]

    def _embed_one_with_fallback(self, text: str) -> list[float]:
        candidate = text[: self._MAX_INPUT_CHARS]
        while True:
            try:
                return self._request([candidate])[0]
            except urllib.error.HTTPError as exc:
                if not self._is_input_too_large_error(exc):
                    raise
                smaller = self._shrink_text(candidate)
                if len(smaller) >= len(candidate):
                    raise
                logger.warning(
                    "embed_texts: shrinking oversized input from %s to %s chars",
                    len(candidate),
                    len(smaller),
                )
                candidate = smaller

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        truncated = [t[: self._MAX_INPUT_CHARS] for t in texts]
        try:
            return self._request(truncated)
        except urllib.error.HTTPError as exc:
            if not self._is_input_too_large_error(exc):
                raise
            logger.warning(
                "embed_texts: batch request exceeded server token limit; retrying one input at a time"
            )
            return [self._embed_one_with_fallback(text) for text in truncated]

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0]

    def health_probe(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "reachable": False,
            "model_found": False,
            "model_name": self._model,
            "embedding_dimensions": None,
            "latency_ms": 0,
            "error": None,
        }
        try:
            logger.info("health_probe(embedding): probing %s...", self._base_url)
            start = time.monotonic()
            vectors = self._request(["ping"])
            elapsed = time.monotonic() - start
            logger.info("health_probe(embedding): response in %.1fms", elapsed * 1000)
            result["reachable"] = True
            result["model_found"] = True
            result["latency_ms"] = round(elapsed * 1000)
            result["ok"] = True
            if vectors:
                result["embedding_dimensions"] = len(vectors[0])
        except Exception as exc:
            logger.warning("health_probe(embedding): failed: %s", exc)
            result["error"] = str(exc)
        return result
