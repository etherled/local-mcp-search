from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class ChunkRecord:
    chunk_id: str
    doc_type: str
    path: str
    language: str | None
    symbol: str | None
    line_start: int
    line_end: int
    title: str | None
    section: str | None
    text: str
    embedding: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChunkRecord":
        return cls(**data)


@dataclass(slots=True)
class SearchResult:
    path: str
    line_start: int
    line_end: int
    symbol: str | None
    snippet: str
    score: float
    why_matched: str
    vector_score: float | None = None
    rerank_score: float | None = None
    title: str | None = None
    section: str | None = None
    chunk_id: str | None = None
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("text", None)
        return data
