from __future__ import annotations

from .retrieval import RetrievalService


def maybe_start_watcher(service: RetrievalService) -> bool:
    return service.start_background_watcher()
