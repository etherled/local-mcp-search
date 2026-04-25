from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from openai import OpenAI

from .config import Settings


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
            and bool(settings.reranker_api_key)
        )
        self.cache_enabled = settings.reranker_cache_enabled
        self.cache_max_entries = settings.reranker_cache_max_entries
        self._cache: dict[str, float] = {}
        self._model = settings.reranker_model
        self._client: OpenAI | None = None
        if self.enabled:
            self._client = OpenAI(
                api_key=settings.reranker_api_key,
                base_url=settings.reranker_base_url,
                timeout=settings.reranker_timeout_seconds,
            )

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int,
    ) -> list[RerankScore]:
        if not self.enabled or self._client is None or not documents:
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
        response = self._client.post(
            "/rerank",
            body={
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
            cast_to=dict[str, Any],
        )
        scores: dict[int, float] = {}
        for item in response.get("results", []):
            try:
                scores[int(item["index"])] = float(item["relevance_score"])
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
