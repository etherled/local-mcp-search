from __future__ import annotations

from openai import OpenAI

from .config import Settings


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.embedding_api_key:
            raise ValueError(
                "EMBEDDING_API_KEY is required. Set it explicitly or start via run-local-mcp-search.ps1."
            )
        self._client = OpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
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
