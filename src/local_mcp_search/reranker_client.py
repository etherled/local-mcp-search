from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .config import Settings

logger = logging.getLogger("local_mcp_search.reranker_client")


def _build_no_proxy_handler() -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({})


@dataclass(slots=True)
class RerankScore:
    index: int
    score: float


class RerankerClient:
    def __init__(self, settings: Settings) -> None:
        self.enabled = (
            settings.reranker_enabled
            and bool(settings.reranker_base_url)
            and bool(settings.reranker_model)
        )
        self.cache_enabled = settings.reranker_cache_enabled
        self.cache_max_entries = settings.reranker_cache_max_entries
        self._cache: dict[str, float] = {}
        self._model = settings.reranker_model
        self._base_url = settings.reranker_base_url.rstrip("/")
        self._timeout = settings.reranker_timeout_seconds
        self._opener = urllib.request.build_opener(_build_no_proxy_handler())

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[RerankScore]:
        if not self.enabled or not documents:
            return []

        cached_scores: dict[int, float] = {}
        missing_indexes: list[int] = []
        missing_documents: list[str] = []
        if self.cache_enabled:
            for index, document in enumerate(documents):
                key = self._cache_key(query, document)
                cached = self._cache.get(key)
                if cached is None:
                    missing_indexes.append(index)
                    missing_documents.append(document)
                else:
                    cached_scores[index] = cached
        else:
            missing_indexes = list(range(len(documents)))
            missing_documents = documents

        fresh_scores: dict[int, float] = {}
        if missing_documents:
            fresh_scores = self._fetch_scores(query, missing_documents, top_n=len(missing_documents))
            if self.cache_enabled:
                for local_index, score in fresh_scores.items():
                    original_index = missing_indexes[local_index]
                    self._remember(self._cache_key(query, documents[original_index]), score)

        combined = [
            RerankScore(index=index, score=score)
            for index, score in {**cached_scores, **{
                missing_indexes[index]: score
                for index, score in fresh_scores.items()
                if 0 <= index < len(missing_indexes)
            }}.items()
        ]
        combined.sort(key=lambda item: item.score, reverse=True)
        return combined[:top_n]

    def _fetch_scores(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> dict[int, float]:
        url = f"{self._base_url}/rerank"
        body = json.dumps({"query": query, "texts": documents}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = self._opener.open(req, timeout=self._timeout)
            data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(errors="replace")[:500]
            except Exception:
                pass
            logger.warning("_fetch_scores: %s %s — body: %s", url, exc, detail)
            return {}
        except Exception as exc:
            logger.warning("_fetch_scores: %s failed: %s", url, exc)
            return {}
        results: list[dict] = data if isinstance(data, list) else data.get("results", [])
        scores: dict[int, float] = {}
        for item in results:
            try:
                scores[int(item["index"])] = float(item["score"])
            except (KeyError, TypeError, ValueError):
                continue
        return scores

    def _cache_key(self, query: str, document: str) -> str:
        payload = f"{self._model}\n{query}\n{document}"
        return sha256(payload.encode("utf-8")).hexdigest()

    def _remember(self, key: str, score: float) -> None:
        if len(self._cache) >= self.cache_max_entries:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = score

    def health_probe(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": True,
                "reachable": False,
                "reason": "disabled",
                "model_name": self._model,
                "latency_ms": 0,
                "error": None,
            }
        result: dict[str, Any] = {
            "ok": False,
            "reachable": False,
            "model_found": False,
            "model_name": self._model,
            "latency_ms": 0,
            "error": None,
        }
        try:
            logger.info("health_probe(reranker): probing %s...", self._base_url)
            start = time.monotonic()
            req = urllib.request.Request(
                f"{self._base_url}/rerank",
                data=json.dumps({"query": "ping", "texts": ["ping"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = self._opener.open(req, timeout=self._timeout)
            elapsed = time.monotonic() - start
            logger.info("health_probe(reranker): response in %.1fms", elapsed * 1000)
            result["reachable"] = True
            result["latency_ms"] = round(elapsed * 1000)
            result["ok"] = True
        except Exception as exc:
            logger.warning("health_probe(reranker): failed: %s", exc)
            result["error"] = str(exc)
        return result
