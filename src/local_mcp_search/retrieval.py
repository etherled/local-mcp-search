from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError

logger = logging.getLogger("local_mcp_search.retrieval")

from .config import Settings
from .context_pack import build_context_pack
from .embedding_client import EmbeddingClient
from .exact_search import run_exact_search
from .index_store import IndexStore, is_git_repo
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

    # -- health helpers --------------------------------------------------

    @staticmethod
    def _compute_overall_health(
        embedding_health: dict,
        reranker_health: dict,
        index_health: dict,
    ) -> str:
        if not embedding_health["reachable"] and index_health.get("ok") is None:
            return "unhealthy"
        if (
            not embedding_health["reachable"]
            or not embedding_health["ok"]
            or (reranker_health.get("reachable") is False
                and reranker_health.get("reason") != "disabled")
            or index_health.get("stale")
        ):
            return "degraded"
        return "healthy"

    @staticmethod
    def _index_health_check(
        status: dict,
        embedding_health: dict,
    ) -> dict:
        if not status.get("index_exists"):
            if not embedding_health["reachable"]:
                return {
                    "ok": None,
                    "reason": "embedding_unavailable",
                    "message": "Cannot build index: embedding server unreachable.",
                }
            return {
                "ok": False,
                "reason": "missing",
                "message": "Index has not been built yet.",
            }
        if status.get("index_may_be_stale"):
            return {
                "ok": True,
                "stale": True,
                "changed_path_count": status.get("changed_path_count"),
                "message": f"Index may be stale: {status.get('changed_path_count', '?')} files changed.",
            }
        return {"ok": True, "stale": False}

    @staticmethod
    def _collect_issues(
        status: dict,
        embedding_health: dict,
        reranker_health: dict,
        index_health: dict,
    ) -> list[str]:
        issues: list[str] = []
        if not embedding_health["reachable"]:
            issues.append(
                "Embedding server is unreachable — code_semantic_search and kb_search will fail."
            )
        elif not embedding_health["ok"]:
            issues.append(
                f"Embedding model '{embedding_health['model_name']}' not found on server."
            )
        if (
            reranker_health.get("reachable") is False
            and reranker_health.get("reason") != "disabled"
        ):
            issues.append(
                "Reranker is enabled but unreachable — will fall back to vector scores."
            )
        if index_health.get("stale"):
            count = status.get("changed_path_count", "?")
            issues.append(f"Index is stale: {count} files changed since last index.")
        if not status.get("index_exists"):
            issues.append("Index has not been built — run reindex to enable semantic search.")
        return issues

    @staticmethod
    def _collect_actions(
        status: dict,
        embedding_health: dict,
        reranker_health: dict,
        index_health: dict,
    ) -> list[str]:
        actions: list[str] = []
        if not embedding_health["reachable"]:
            actions.append("Start your local embedding server (e.g. LM Studio) and load the embedding model.")
        elif not embedding_health["ok"]:
            actions.append(
                f"Load model '{embedding_health['model_name']}' in your embedding server."
            )
        if not status.get("index_exists"):
            if embedding_health["reachable"]:
                actions.append("reindex auto")
        elif status.get("index_may_be_stale"):
            actions.append("reindex auto")
        if (
            reranker_health.get("reachable") is False
            and reranker_health.get("reason") != "disabled"
        ):
            actions.append(
                "Check reranker server status, or set MCP_SEARCH_RERANKER_ENABLED=false to disable."
            )
        return actions

    # -- public API -----------------------------------------------------

    def index_status(self) -> dict:
        t0 = time.monotonic()
        logger.info("index_status: trying to acquire lock (0.5s timeout)...")
        reindex_in_progress = False
        if self._reindex_lock.acquire(timeout=0.5):
            try:
                status = self.index_store.status(quick=True)
            finally:
                self._reindex_lock.release()
        else:
            logger.warning("index_status: reindex in progress, returning shallow status")
            reindex_in_progress = True
            status = {
                "repo_root": str(self.settings.workspace_root),
                "index_path": str(self.index_store.db_dir),
                "index_exists": self.index_store.metadata_path.exists(),
                "reindex_in_progress": True,
                "note": "A reindex is currently running; freshness check skipped.",
            }
        t1 = time.monotonic()
        logger.info("index_status: status fetch took %.1fms (reindex_in_progress=%s)",
                    (t1 - t0) * 1000, reindex_in_progress)
        status["auto_reindex_enabled"] = self.settings.auto_reindex_enabled
        status["watcher_running"] = self._watcher_thread is not None and self._watcher_thread.is_alive()
        status["reranker_enabled"] = self.reranker_client.enabled
        status["reranker_model"] = self.settings.reranker_model if self.reranker_client.enabled else None

        embedding_health: dict = {"ok": False, "reachable": False, "error": "health probe skipped"}
        reranker_health: dict = {"ok": False, "reachable": False, "error": "health probe skipped"}

        logger.info("index_status: starting health probes (embedding + reranker)...")
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            emb_future: Future = pool.submit(self.embedding_client.health_probe)
            rerank_future: Future = pool.submit(self.reranker_client.health_probe)
            for name, fut in [("embedding", emb_future), ("reranker", rerank_future)]:
                try:
                    t_probe = time.monotonic()
                    result = fut.result(timeout=3)
                    elapsed_ms = (time.monotonic() - t_probe) * 1000
                    logger.info("index_status: %s health probe completed in %.1fms", name, elapsed_ms)
                    if name == "embedding":
                        embedding_health = result
                    else:
                        reranker_health = result
                except FutureTimeoutError:
                    logger.warning("index_status: %s health probe timed out after 3s", name)
                    if name == "embedding":
                        embedding_health = {"ok": False, "reachable": False, "error": "probe timed out after 3s"}
                    else:
                        reranker_health = {"ok": False, "reachable": False, "error": "probe timed out after 3s"}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        t2 = time.monotonic()
        logger.info("index_status: health probes took %.1fms total", (t2 - t1) * 1000)

        index_health = self._index_health_check(status, embedding_health)

        status["health"] = {
            "status": self._compute_overall_health(
                embedding_health, reranker_health, index_health
            ),
            "checks": {
                "index": index_health,
                "embedding": embedding_health,
                "reranker": reranker_health,
            },
            "issues": self._collect_issues(
                status, embedding_health, reranker_health, index_health
            ),
            "suggested_actions": self._collect_actions(
                status, embedding_health, reranker_health, index_health
            ),
        }

        return status

    def doctor(self) -> dict:
        status = self.index_status()
        repo_root = self.settings.workspace_root
        embedding_model = self.settings.embedding_model
        reranker_model = self.settings.reranker_model if self.reranker_client.enabled else None

        checks = {
            "workspace_exists": repo_root.exists(),
            "workspace_writable": repo_root.exists() and repo_root.is_dir(),
            "git_available": is_git_repo(repo_root),
            "index_dir_exists": self.settings.index_dir.exists(),
            "index_metadata_exists": self.index_store.metadata_path.exists(),
            "reranker_enabled": self.reranker_client.enabled,
        }

        summary = {
            "workspace_root": str(repo_root),
            "index_dir": str(self.settings.index_dir),
            "embedding_base_url": self.settings.embedding_base_url,
            "embedding_model": embedding_model,
            "reranker_base_url": self.settings.reranker_base_url if self.reranker_client.enabled else None,
            "reranker_model": reranker_model,
            "health_status": status.get("health", {}).get("status"),
            "checks": checks,
            "issues": list(status.get("health", {}).get("issues", [])),
            "suggested_actions": list(status.get("health", {}).get("suggested_actions", [])),
            "status": status,
        }

        if not checks["workspace_exists"]:
            summary["issues"].append("Workspace root does not exist.")
        if not checks["workspace_writable"]:
            summary["issues"].append("Workspace root is not a writable directory.")
        if not checks["index_dir_exists"]:
            summary["suggested_actions"].append("Run reindex auto to create the index directory.")
        if not self.reranker_client.enabled:
            summary["suggested_actions"].append("Reranker is disabled; semantic results will skip reranking.")
        if not status.get("index_exists"):
            summary["suggested_actions"].append("Run reindex auto after backend health is green.")

        return summary

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
