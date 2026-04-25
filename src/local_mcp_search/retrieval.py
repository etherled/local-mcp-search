from __future__ import annotations

import threading
import time

from .config import Settings
from .context_pack import build_context_pack
from .embedding_client import EmbeddingClient
from .exact_search import run_exact_search
from .index_store import IndexStore
from .models import SearchResult
from .outline import build_file_outline
from .reranker_client import RerankerClient
from .repo_overview import build_repo_overview
from .spans import open_spans
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
            item.rerank_score = round(score.score, 4)
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

    def code_context_pack(
        self,
        query: str,
        *,
        language: list[str] | None = None,
        max_results: int = 8,
        max_chars: int | None = None,
    ) -> dict:
        search = self.code_semantic_search(
            query,
            language=language,
            max_results=max_results,
        )
        pack = build_context_pack(
            self.settings.workspace_root,
            search["results"],
            max_chars=max_chars or self.settings.context_pack_max_chars,
        )
        pack["query"] = query
        pack["source_results"] = search["results"]
        return pack

    def file_outline(self, path: str, *, max_items: int = 80) -> dict:
        return build_file_outline(
            self.settings.workspace_root,
            path,
            max_items=max_items,
        )

    def symbol_context(
        self,
        symbol: str,
        *,
        max_results: int = 8,
        max_chars: int | None = None,
    ) -> dict:
        definitions = self.symbol_search(symbol, max_results=max_results)["results"]
        references = self.code_exact_search(symbol, max_results=max_results)["results"]
        combined = definitions + [
            item
            for item in references
            if (item["path"], item["line_start"], item["line_end"])
            not in {
                (definition["path"], definition["line_start"], definition["line_end"])
                for definition in definitions
            }
        ]
        pack = build_context_pack(
            self.settings.workspace_root,
            combined,
            max_chars=max_chars or self.settings.context_pack_max_chars,
        )
        pack["symbol"] = symbol
        pack["definitions"] = definitions
        pack["references"] = references
        return pack

    def doc_answer_context(
        self,
        query: str,
        *,
        max_results: int = 6,
        max_chars: int | None = None,
    ) -> dict:
        search = self.kb_search(query, max_results=max_results)
        pack = build_context_pack(
            self.settings.workspace_root,
            search["results"],
            max_chars=max_chars or self.settings.context_pack_max_chars,
        )
        pack["query"] = query
        pack["source_results"] = search["results"]
        return pack

    def change_context(self, *, max_results: int = 30, max_chars: int | None = None) -> dict:
        changed_paths = self.index_store.detect_changed_paths_public()
        if changed_paths is None:
            return {
                "changed_paths": None,
                "items": [],
                "message": "Change detection unavailable; run reindex auto or inspect git status.",
            }

        items = []
        for rel_path in sorted(changed_paths)[:max_results]:
            path = self.settings.workspace_root / rel_path
            if not path.exists() or not path.is_file():
                items.append({"path": rel_path, "status": "deleted_or_missing"})
                continue
            try:
                outline = build_file_outline(self.settings.workspace_root, rel_path, max_items=30)
            except Exception:
                outline = {"path": rel_path, "items": []}
            items.append({"path": rel_path, "status": "changed", "outline": outline["items"]})

        opened = []
        remaining_chars = max_chars or self.settings.context_pack_max_chars
        for item in items:
            if item["status"] != "changed":
                continue
            try:
                span = open_spans(
                    self.settings.workspace_root,
                    [{"path": item["path"], "line_start": 1, "line_end": 80}],
                    max_total_chars=remaining_chars,
                )[0]
            except Exception:
                continue
            remaining_chars -= len(span["content"])
            opened.append(span)
            if remaining_chars <= 0:
                break

        return {
            "changed_paths": sorted(changed_paths),
            "items": items,
            "context": opened,
        }

    def dependency_overview(self, *, max_files: int = 12) -> dict:
        candidates = [
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            "poetry.lock",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "settings.gradle",
            "composer.json",
            "Gemfile",
            "Dockerfile",
            "docker-compose.yml",
        ]
        found = []
        for rel_path in candidates:
            path = self.settings.workspace_root / rel_path
            if path.exists() and path.is_file():
                found.append(rel_path)
            if len(found) >= max_files:
                break
        context = open_spans(
            self.settings.workspace_root,
            [{"path": path, "line_start": 1, "line_end": 120} for path in found],
            max_total_chars=self.settings.context_pack_max_chars,
        )
        return {"files": found, "context": context}

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
