from __future__ import annotations

import threading
import time

from .config import Settings
from .embedding_client import EmbeddingClient
from .exact_search import run_exact_search
from .index_store import IndexStore
from .models import SearchResult
from .reranker_client import RerankerClient
from .repo_overview import build_repo_overview
from .symbol_search import run_symbol_search


class RetrievalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.embedding_client = EmbeddingClient(settings)
        self.index_store = IndexStore(settings, self.embedding_client)
        self.reranker_client = RerankerClient(settings)
        self._reindex_lock = threading.RLock()
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def index_status(self) -> dict:
        with self._reindex_lock:
            status = self.index_store.status()
        status["auto_reindex_enabled"] = self.settings.auto_reindex_enabled
        status["watcher_running"] = self._watcher_thread is not None and self._watcher_thread.is_alive()
        status["reranker_enabled"] = self.reranker_client.enabled
        status["reranker_model"] = self.settings.reranker_model if self.reranker_client.enabled else None
        return status

    def reindex(self, mode: str = "auto") -> dict:
        with self._reindex_lock:
            return self.index_store.rebuild(mode=mode)

    def code_exact_search(
        self,
        query: str,
        *,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        max_results: int = 10,
    ) -> dict:
        results = run_exact_search(
            self.settings.workspace_root,
            query,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_results=max_results,
        )
        return {"results": [item.to_dict() for item in results]}

    def symbol_search(
        self,
        symbol: str,
        *,
        max_results: int = 10,
    ) -> dict:
        results = run_symbol_search(
            self.settings.workspace_root,
            symbol,
            max_results=max_results,
        )
        return {"results": [item.to_dict() for item in results]}

    def code_semantic_search(
        self,
        query: str,
        *,
        language: list[str] | None = None,
        max_results: int = 8,
    ) -> dict:
        with self._reindex_lock:
            results = self.index_store.semantic_search(
                query,
                doc_type="code",
                max_results=max_results,
                languages=language,
                candidate_count=self._candidate_count(max_results),
            )
        results = self._rerank_results(query, results, max_results=max_results)
        return {"results": [item.to_dict() for item in results]}

    def repo_overview(self, max_entries: int = 12) -> dict:
        return build_repo_overview(self.settings.workspace_root, max_entries=max_entries)

    def kb_search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> dict:
        with self._reindex_lock:
            results = self.index_store.semantic_search(
                query,
                doc_type="kb",
                max_results=max_results,
                candidate_count=self._candidate_count(max_results),
            )
        results = self._rerank_results(query, results, max_results=max_results)
        return {"results": [item.to_dict() for item in results]}

    def _candidate_count(self, max_results: int) -> int:
        if not self.reranker_client.enabled:
            return max_results
        return min(
            max(max_results, max_results * self.settings.reranker_candidate_multiplier),
            self.settings.reranker_max_candidates,
        )

    def _rerank_results(
        self,
        query: str,
        results: list[SearchResult],
        *,
        max_results: int,
    ) -> list[SearchResult]:
        if not self.reranker_client.enabled or len(results) <= 1:
            return results[:max_results]

        documents = [item.text or item.snippet for item in results]
        try:
            scores = self.reranker_client.rerank(
                query,
                documents,
                top_n=min(max_results, len(documents)),
            )
        except Exception:
            return results[:max_results]

        if not scores:
            return results[:max_results]

        reranked: list[SearchResult] = []
        used_indexes: set[int] = set()
        for score in scores:
            if score.index < 0 or score.index >= len(results):
                continue
            item = results[score.index]
            item.score = round(score.score, 4)
            item.why_matched = "semantic vector recall, reranked by reranker"
            reranked.append(item)
            used_indexes.add(score.index)

        if len(reranked) < max_results:
            reranked.extend(
                item
                for index, item in enumerate(results)
                if index not in used_indexes
            )
        return reranked[:max_results]

    def start_background_watcher(self) -> bool:
        if not self.settings.auto_reindex_enabled:
            return False
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            return True

        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            name="local-mcp-search-watcher",
            daemon=True,
        )
        self._watcher_thread.start()
        return True

    def stop_background_watcher(self) -> None:
        self._stop_event.set()
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=2)

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self.settings.auto_reindex_interval_seconds):
            try:
                with self._reindex_lock:
                    self.index_store.rebuild(mode="auto")
            except Exception:
                # Keep the watcher alive; inspection can be done via logs later if needed.
                continue
