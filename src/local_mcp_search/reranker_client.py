from __future__ import annotations

from dataclasses import dataclass
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
        scores: list[RerankScore] = []
        for item in response.get("results", []):
            try:
                scores.append(
                    RerankScore(
                        index=int(item["index"]),
                        score=float(item["relevance_score"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return scores
