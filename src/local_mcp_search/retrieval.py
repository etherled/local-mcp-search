from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

logger = logging.getLogger("local_mcp_search.retrieval")

from .config import Settings
from .context_pack import build_context_pack
from .embedding_client import EmbeddingClient
from .exact_search import run_exact_search
from .index_store import (
    IndexStore,
    get_git_changed_paths,
    get_git_numstat,
    get_git_status_entries,
    is_git_repo,
)
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

    def _with_debug(self, payload: dict, debug: dict | None = None) -> dict:
        if self.settings.query_debug_enabled and debug is not None:
            payload["debug"] = debug
        return payload

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

        expected_dims = status.get("embedding_dimensions")
        live_dims = embedding_health.get("embedding_dimensions")
        if (
            status.get("index_exists")
            and expected_dims is not None
            and live_dims is not None
            and expected_dims != live_dims
        ):
            status["health"]["status"] = "degraded"
            status["health"]["issues"].append(
                f"Embedding dimension mismatch: index={expected_dims}, live={live_dims}."
            )
            status["health"]["suggested_actions"].append(
                "Run reindex full after changing embedding model."
            )

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
            "codex_in_path": shutil.which("codex") is not None,
        }
        checks["codex_mcp_matches_workspace"] = self._codex_mcp_matches_workspace()

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
        if checks["codex_in_path"] and checks["codex_mcp_matches_workspace"] is False:
            summary["issues"].append(
                "Codex MCP local-search target does not match this workspace wrapper."
            )
            summary["suggested_actions"].append(
                "Re-run cpx in this workspace to refresh the local-search registration."
            )
        if not self.reranker_client.enabled:
            summary["suggested_actions"].append("Reranker is disabled; semantic results will skip reranking.")
        if not status.get("index_exists"):
            summary["suggested_actions"].append("Run reindex auto after backend health is green.")
        expected_dims = status.get("embedding_dimensions")
        live_dims = status.get("health", {}).get("checks", {}).get("embedding", {}).get("embedding_dimensions")
        if (
            status.get("index_exists")
            and expected_dims is not None
            and live_dims is not None
            and expected_dims != live_dims
        ):
            summary["issues"].append(
                f"Embedding dimension mismatch: index={expected_dims}, live={live_dims}."
            )
            summary["suggested_actions"].append("Run reindex full after changing embedding model.")

        return summary

    def _codex_mcp_matches_workspace(self) -> bool | None:
        codex = shutil.which("codex")
        if not codex:
            return None
        wrapper = (self.settings.workspace_root / ".mcp-index" / "_mcp_server_wrapper.py").resolve()
        try:
            result = subprocess.run(
                [codex, "mcp", "get", "local-search", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return str(wrapper).lower() in result.stdout.lower()
        transport = payload.get("transport") if isinstance(payload, dict) else None
        args = transport.get("args") if isinstance(transport, dict) else None
        if not isinstance(args, list):
            return None
        normalized = [str(Path(arg).resolve()).lower() if Path(arg).exists() else str(arg).lower() for arg in args]
        return str(wrapper).lower() in normalized

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
        results, exact_debug = run_exact_search(
            self.settings,
            query,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            max_results=max_results,
        )
        return self._with_debug(
            {"results": [item.to_dict() for item in results]},
            {
                "query_type": "exact_search",
                **exact_debug,
            },
        )

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
        candidate_count = self._candidate_count(max_results)
        with self._reindex_lock:
            results = self.index_store.semantic_search(
                query,
                doc_type="code",
                max_results=max_results,
                languages=language,
                candidate_count=candidate_count,
            )
        reranked, rerank_debug = self._rerank_results(
            query,
            results,
            max_results=max_results,
        )
        return self._with_debug(
            {"results": [item.to_dict() for item in reranked]},
            {
                "query_type": "semantic_search",
                "doc_type": "code",
                "query": query,
                "language": language or [],
                "candidate_count_requested": candidate_count,
                "semantic_candidates_returned": len(results),
                "returned_results": len(reranked),
                "max_results": max_results,
                "rerank": rerank_debug,
            },
        )

    def repo_overview(self, max_entries: int = 12) -> dict:
        return build_repo_overview(self.settings, max_entries=max_entries)

    def kb_search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> dict:
        candidate_count = self._candidate_count(max_results)
        with self._reindex_lock:
            results = self.index_store.semantic_search(
                query,
                doc_type="kb",
                max_results=max_results,
                candidate_count=candidate_count,
            )
        reranked, rerank_debug = self._rerank_results(
            query,
            results,
            max_results=max_results,
        )
        return self._with_debug(
            {"results": [item.to_dict() for item in reranked]},
            {
                "query_type": "semantic_search",
                "doc_type": "kb",
                "query": query,
                "candidate_count_requested": candidate_count,
                "semantic_candidates_returned": len(results),
                "returned_results": len(reranked),
                "max_results": max_results,
                "rerank": rerank_debug,
            },
        )

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
    ) -> tuple[list[SearchResult], dict]:
        if not self.reranker_client.enabled:
            return results[:max_results], {
                "enabled": False,
                "attempted": False,
                "reason": "disabled",
                "input_candidates": len(results),
                "reranked_candidates": 0,
                "returned_results": min(len(results), max_results),
            }
        if len(results) <= 1:
            return results[:max_results], {
                "enabled": True,
                "attempted": False,
                "reason": "insufficient_candidates",
                "input_candidates": len(results),
                "reranked_candidates": 0,
                "returned_results": min(len(results), max_results),
            }

        documents = [item.text or item.snippet for item in results]
        try:
            scores = self.reranker_client.rerank(
                query,
                documents,
                top_n=min(max_results, len(documents)),
            )
        except Exception as exc:
            return results[:max_results], {
                "enabled": True,
                "attempted": True,
                "reason": "error",
                "error": str(exc),
                "input_candidates": len(results),
                "reranked_candidates": 0,
                "returned_results": min(len(results), max_results),
            }

        if not scores:
            return results[:max_results], {
                "enabled": True,
                "attempted": True,
                "reason": "empty_scores",
                "input_candidates": len(results),
                "reranked_candidates": 0,
                "returned_results": min(len(results), max_results),
            }

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
        final_results = reranked[:max_results]
        return final_results, {
            "enabled": True,
            "attempted": True,
            "reason": "ok",
            "input_candidates": len(results),
            "reranked_candidates": len(scores),
            "returned_results": len(final_results),
        }

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
        target_max_chars = max_chars or self.settings.context_pack_max_chars
        pack = build_context_pack(
            self.settings.workspace_root,
            search["results"],
            max_chars=target_max_chars,
        )
        pack["query"] = query
        pack["source_results"] = search["results"]
        if self.settings.query_debug_enabled:
            pack["debug"] = {
                "query_type": "context_pack",
                "query": query,
                "max_results": max_results,
                "max_chars_budget": target_max_chars,
                "source_result_count": len(search["results"]),
                "packed_item_count": len(pack["items"]),
                "packed_chars": pack["total_chars"],
                "source_chars": pack.get("source_chars"),
                "trimmed_chars": pack.get("trimmed_chars", 0),
                "truncated": pack["truncated"],
                "search": search.get("debug"),
            }
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
        target_max_chars = max_chars or self.settings.context_pack_max_chars
        pack = build_context_pack(
            self.settings.workspace_root,
            search["results"],
            max_chars=target_max_chars,
        )
        pack["query"] = query
        pack["source_results"] = search["results"]
        return pack

    def change_context(self, *, max_results: int = 30, max_chars: int | None = None) -> dict:
        def _build_change_context() -> dict:
            git_status_entries = get_git_status_entries(self.settings.workspace_root) or []
            git_status_by_path = {
                entry["path"]: entry
                for entry in git_status_entries
            }
            status_snapshot = self.index_store.status(quick=True)
            committed_since_index = get_git_changed_paths(
                self.settings.workspace_root,
                status_snapshot.get("last_indexed_commit"),
            ) or set()
            changed_paths: set[str] | None
            if is_git_repo(self.settings.workspace_root):
                changed_paths = set(git_status_by_path).union(committed_since_index)
            else:
                changed_paths = self.index_store.detect_changed_paths_public()

            if changed_paths is None:
                return {
                    "changed_paths": None,
                    "items": [],
                    "message": "Change detection unavailable; run reindex auto or inspect git status.",
                }

            git_numstat = get_git_numstat(self.settings.workspace_root) or {}

            items = []
            for rel_path in sorted(changed_paths):
                path = self.settings.workspace_root / rel_path
                status_entry = git_status_by_path.get(rel_path)
                change_scope = self._classify_change_scope(
                    rel_path,
                    path,
                    status_entry,
                    committed_since_index,
                )
                change_type = self._classify_change_type(rel_path, path, status_entry, change_scope)
                line_stats = git_numstat.get(rel_path) if change_scope == "worktree" else None
                risk = self._assess_change_risk(rel_path, change_type, line_stats, change_scope)
                items.append(
                    {
                        "path": rel_path,
                        "status": "deleted_or_missing" if not path.exists() or not path.is_file() else "changed",
                        "change_scope": change_scope,
                        "change_type": change_type,
                        "risk": risk,
                        "git_status": status_entry,
                        "diff_summary": line_stats,
                        "group": self._change_group(rel_path, change_type),
                    }
                )

            items.sort(
                key=lambda item: (
                    self._risk_rank(item["risk"]),
                    self._change_type_rank(item["change_type"]),
                    item["path"],
                )
            )
            limited_items = items[:max_results]
            grouped = self._group_change_items(limited_items)
            return {
                "changed_paths": sorted(changed_paths),
                "summary": {
                    "total_changed_paths": len(changed_paths),
                    "returned_items": len(limited_items),
                    "groups": {group: len(entries) for group, entries in grouped.items()},
                    "change_types": self._count_by_key(limited_items, "change_type"),
                    "change_scopes": self._count_by_key(limited_items, "change_scope"),
                    "risk_levels": self._count_by_key(limited_items, "risk"),
                },
                "items": limited_items,
                "grouped_items": grouped,
                "context": [],
            }

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_build_change_context)
            try:
                return future.result(timeout=5)
            except FutureTimeoutError:
                return {
                    "changed_paths": None,
                    "items": [],
                    "message": "Change context timed out; use repo://changes or retry with smaller max_results.",
                }
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _classify_change_scope(
        rel_path: str,
        path,
        status_entry: dict | None,
        committed_since_index: set[str],
    ) -> str:
        if status_entry is not None:
            return "worktree"
        if rel_path in committed_since_index:
            return "committed_since_index"
        if not path.exists():
            return "missing"
        return "manifest_only"

    @staticmethod
    def _classify_change_type(
        path_text: str,
        path,
        status_entry: dict | None,
        change_scope: str,
    ) -> str:
        if status_entry is not None:
            raw = status_entry.get("raw_status", "")
            if "R" in raw:
                return "renamed"
            if "A" in raw or status_entry.get("index_status") == "A":
                return "added"
            if "D" in raw or status_entry.get("worktree_status") == "D":
                return "deleted"
            if "M" in raw:
                return "modified"
            if "?" in raw:
                return "untracked"
        if change_scope == "committed_since_index":
            return "committed_since_index"
        if change_scope == "manifest_only":
            return "manifest_changed"
        if not path.exists():
            return "deleted"
        return "modified"

    @staticmethod
    def _assess_change_risk(
        rel_path: str,
        change_type: str,
        line_stats: dict | None,
        change_scope: str,
    ) -> str:
        lowered = rel_path.lower()
        if change_type in {"deleted", "renamed"}:
            return "high"
        if change_scope == "committed_since_index":
            return "medium"
        if any(
            token in lowered
            for token in ("server", "config", "launcher", "index_store", "retrieval", "cli")
        ):
            return "high"
        if line_stats:
            added = line_stats.get("added_lines") or 0
            deleted = line_stats.get("deleted_lines") or 0
            if added + deleted >= 200:
                return "high"
            if added + deleted >= 40:
                return "medium"
        if change_type in {"added", "untracked"}:
            return "medium"
        return "low"

    @staticmethod
    def _change_group(rel_path: str, change_type: str) -> str:
        lowered = rel_path.lower()
        if change_type in {"deleted", "renamed"}:
            return "high_attention"
        if lowered.endswith(".md") or lowered.endswith(".txt") or lowered.endswith(".rst"):
            return "docs"
        if "/test" in lowered or lowered.endswith("_test.py") or lowered.endswith(".spec.ts"):
            return "tests"
        if lowered.endswith((".json", ".toml", ".yaml", ".yml")):
            return "config"
        if lowered.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java")):
            return "code"
        return "other"

    @staticmethod
    def _risk_rank(value: str) -> int:
        return {"high": 0, "medium": 1, "low": 2}.get(value, 3)

    @staticmethod
    def _change_type_rank(value: str) -> int:
        return {
            "deleted": 0,
            "renamed": 1,
            "modified": 2,
            "added": 3,
            "untracked": 4,
            "committed_since_index": 5,
            "manifest_changed": 6,
        }.get(value, 5)

    @staticmethod
    def _group_change_items(items: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for item in items:
            grouped.setdefault(item["group"], []).append(item)
        return grouped

    @staticmethod
    def _count_by_key(items: list[dict], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = item.get(key)
            if not isinstance(value, str):
                continue
            counts[value] = counts.get(value, 0) + 1
        return counts

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
