from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import uuid
from hashlib import sha256
from pathlib import Path

import lancedb

from .chunking import chunk_code_text, chunk_kb_text, detect_doc_type, detect_language
from .config import Settings
from .embedding_client import EmbeddingClient
from .models import SearchResult

logger = logging.getLogger("local_mcp_search.index_store")

EMBED_BATCH_SIZE = 200


def _emit_progress(msg: str) -> None:
    stream = sys.stderr
    if stream is None or getattr(stream, "closed", False):
        return
    try:
        print(f"[reindex] {msg}", file=stream, flush=True)
    except (OSError, ValueError, UnicodeError):
        return


class IndexStore:
    def __init__(self, settings: Settings, embedding_client: EmbeddingClient) -> None:
        self.settings = settings
        self.embedding_client = embedding_client
        self.db_dir = settings.index_dir / "lancedb"
        self.metadata_path = settings.index_dir / "metadata.json"
        self.table_name = "chunks"

    def status(self, *, quick: bool = False) -> dict:
        if not self.metadata_path.exists():
            logger.info("status: no index found at %s", self.metadata_path)
            return {
                "repo_root": str(self.settings.workspace_root),
                "index_exists": False,
                "index_path": str(self.db_dir),
                "git_available": is_git_repo(self.settings.workspace_root),
            }

        t0 = time.monotonic()
        payload = self._read_metadata()
        t1 = time.monotonic()
        logger.info("status: _read_metadata took %.1fms", (t1 - t0) * 1000)
        if quick:
            logger.info("status: quick=True, skipping _detect_changed_paths")
            changed_count: int | None = None
            freshness_skipped = True
            git_available = (self.settings.workspace_root / ".git").exists()
        else:
            logger.info("status: detecting changed paths...")
            changed_paths = self._detect_changed_paths()
            t2 = time.monotonic()
            logger.info("status: _detect_changed_paths took %.1fms, %d paths changed",
                         (t2 - t1) * 1000,
                         len(changed_paths) if changed_paths else -1)
            changed_count = len(changed_paths) if changed_paths is not None else None
            freshness_skipped = False
            git_available = is_git_repo(self.settings.workspace_root)
        return {
            "repo_root": str(self.settings.workspace_root),
            "index_exists": True,
            "index_path": str(self.db_dir),
            "git_available": git_available,
            "indexed_at": payload.get("indexed_at"),
            "embedding_model": payload.get("embedding_model"),
            "embedding_dimensions": payload.get("embedding_dimensions"),
            "chunk_count": payload.get("chunk_count", 0),
            "code_chunk_count": payload.get("code_chunk_count", 0),
            "kb_chunk_count": payload.get("kb_chunk_count", 0),
            "last_indexed_commit": payload.get("last_indexed_commit"),
            "tracked_file_count": len(payload.get("file_manifest", {})),
            "index_may_be_stale": None if freshness_skipped else (changed_count is None or changed_count > 0),
            "changed_path_count": changed_count,
            "freshness_check_skipped": freshness_skipped,
            "suggested_action": "reindex auto to verify freshness"
            if freshness_skipped
            else ("reindex auto" if (changed_count is None or changed_count > 0) else None),
        }

    def rebuild(self, mode: str = "auto") -> dict:
        if mode not in {"auto", "full", "incremental"}:
            raise ValueError(f"Unsupported reindex mode: {mode}")

        if mode == "full":
            return self._full_rebuild()
        if mode == "incremental":
            return self._incremental_rebuild()

        if not self.metadata_path.exists():
            return self._full_rebuild()

        changed_paths = self._detect_changed_paths()
        if changed_paths is None:
            return self._full_rebuild()
        if not changed_paths:
            status = self.status()
            status["reindex_mode"] = "incremental"
            status["changed_paths"] = []
            return status
        return self._incremental_rebuild(changed_paths)

    def detect_changed_paths_public(self) -> set[str] | None:
        return self._detect_changed_paths()

    def semantic_search(
        self,
        query: str,
        *,
        doc_type: str,
        max_results: int,
        languages: list[str] | None = None,
        candidate_count: int | None = None,
    ) -> list[SearchResult]:
        if not self.metadata_path.exists():
            return []

        metadata = self._read_metadata()
        query_vector = self.embedding_client.embed_text(query)
        expected_dims = metadata.get("embedding_dimensions")
        if expected_dims and len(query_vector) != expected_dims:
            raise ValueError(
                f"Embedding dimension mismatch: query={len(query_vector)} index={expected_dims}"
            )

        table = self._open_table()
        query_builder = table.search(query_vector)
        filters = [f"doc_type = '{escape_sql_string(doc_type)}'"]
        if languages:
            language_filters = ", ".join(
                f"'{escape_sql_string(language)}'" for language in languages
            )
            filters.append(f"language IN ({language_filters})")
        query_builder = query_builder.where(" AND ".join(filters))
        limit = candidate_count or max_results
        rows = query_builder.limit(limit).to_list()

        results: list[SearchResult] = []
        for row in rows:
            distance = float(row.get("_distance", 0.0))
            score = 1.0 / (1.0 + max(distance, 0.0))
            vector_score = round(score, 4)
            results.append(
                SearchResult(
                    path=row["path"],
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_end"]),
                    symbol=row.get("symbol"),
                    snippet=short_snippet(row["text"]),
                    score=vector_score,
                    why_matched=f"semantic similarity against {doc_type} index",
                    vector_score=vector_score,
                    title=row.get("title"),
                    section=row.get("section"),
                    chunk_id=row.get("chunk_id"),
                    text=row.get("text"),
                )
            )
        return results

    def _full_rebuild(self) -> dict:
        _emit_progress("full rebuild — scanning files…")
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

        chunks = self._collect_chunks()
        _emit_progress(f"collected {len(chunks)} chunks, embedding…")
        rows = self._embed_chunks_to_rows(chunks)
        _emit_progress(f"embedding done, {len(rows)} rows — writing to lanceDB…")

        db = lancedb.connect(str(self.db_dir))
        db.create_table(self.table_name, data=rows, mode="overwrite")

        dimensions = len(rows[0]["vector"]) if rows else None
        metadata = self._build_metadata(
            dimensions=dimensions,
            file_manifest=build_file_manifest(
                self.settings.workspace_root,
                self.settings,
            ),
            chunk_count=len(rows),
            code_chunk_count=sum(1 for row in rows if row["doc_type"] == "code"),
            kb_chunk_count=sum(1 for row in rows if row["doc_type"] == "kb"),
        )
        self._write_metadata(metadata)
        status = self.status()
        status["reindex_mode"] = "full"
        return status

    def _incremental_rebuild(self, changed_paths: set[str] | None = None) -> dict:
        _emit_progress("incremental rebuild — detecting changes…")
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

        if not self.metadata_path.exists():
            _emit_progress("no existing index, falling back to full rebuild")
            return self._full_rebuild()

        changed_paths = changed_paths if changed_paths is not None else self._detect_changed_paths()
        if changed_paths is None:
            _emit_progress("cannot detect changes, falling back to full rebuild")
            return self._full_rebuild()
        if not changed_paths:
            _emit_progress("no changes detected, index is up to date")
            status = self.status()
            status["reindex_mode"] = "incremental"
            status["changed_paths"] = []
            return status

        previous_metadata = self._read_metadata()
        old_manifest_size = len(previous_metadata.get("file_manifest", {}))
        if old_manifest_size > 0 and len(changed_paths) > old_manifest_size * 0.5:
            _emit_progress(
                f"{len(changed_paths)}/{old_manifest_size} files changed (>50%), "
                "falling back to full rebuild"
            )
            return self._full_rebuild()

        _emit_progress(f"{len(changed_paths)} files changed, updating index…")
        table = self._open_table()
        for i, rel_path in enumerate(sorted(changed_paths)):
            table.delete(f"path = '{escape_sql_string(rel_path)}'")
            if (i + 1) % 200 == 0:
                _emit_progress(f"deleted old rows: {i + 1}/{len(changed_paths)}…")

        new_chunks = self._collect_chunks_for_paths(changed_paths)
        _emit_progress(f"collected {len(new_chunks)} new chunks, embedding…")
        rows = self._embed_chunks_to_rows(new_chunks)
        if rows:
            _emit_progress(f"embedding done, {len(rows)} rows — writing to lanceDB…")
            table.add(rows)

        dimensions = previous_metadata.get("embedding_dimensions")
        if dimensions is None and rows:
            dimensions = len(rows[0]["vector"])

        refreshed_manifest = build_file_manifest(
            self.settings.workspace_root,
            self.settings,
        )
        updated_metadata = self._build_metadata(
            dimensions=dimensions,
            file_manifest=refreshed_manifest,
            chunk_count=int(table.count_rows()),
            code_chunk_count=self._count_rows("doc_type = 'code'"),
            kb_chunk_count=self._count_rows("doc_type = 'kb'"),
        )
        self._write_metadata(updated_metadata)

        status = self.status()
        status["reindex_mode"] = "incremental"
        status["changed_paths"] = sorted(changed_paths)
        return status

    def _collect_chunks(self) -> list[dict]:
        chunks: list[dict] = []
        file_count = 0
        for path in iter_candidate_files(self.settings.workspace_root, self.settings):
            chunks.extend(self._chunk_file(path))
            file_count += 1
            if file_count % 500 == 0:
                _emit_progress(f"chunking: {file_count} files, {len(chunks)} chunks so far…")
        return chunks

    def _collect_chunks_for_paths(self, changed_paths: set[str]) -> list[dict]:
        chunks: list[dict] = []
        total = len(changed_paths)
        done = 0
        for rel_path in sorted(changed_paths):
            path = self.settings.workspace_root / rel_path
            if not path.exists() or not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > self.settings.effective_max_file_bytes:
                continue
            chunks.extend(self._chunk_file(path))
            done += 1
            if done % 500 == 0:
                _emit_progress(f"chunking changed files: {done}/{total}, {len(chunks)} chunks…")
        return chunks

    def _chunk_file(self, path: Path) -> list[dict]:
        doc_type = detect_doc_type(path, self.settings)
        if doc_type is None:
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                return []
        relative_path = path.relative_to(self.settings.workspace_root).as_posix()
        path_chunks = (
            chunk_code_text(path, text, self.settings)
            if doc_type == "code"
            else chunk_kb_text(path, text, self.settings)
        )
        chunks: list[dict] = []
        for item in path_chunks:
            item["chunk_id"] = str(uuid.uuid4())
            item["doc_type"] = doc_type
            item["path"] = relative_path
            item["language"] = detect_language(path)
            chunks.append(item)
        return chunks

    def _embed_chunks_to_rows(self, chunks: list[dict]) -> list[dict]:
        if not chunks:
            return []

        total = len(chunks)
        all_rows: list[dict] = []
        for batch_start in range(0, total, EMBED_BATCH_SIZE):
            batch_end = min(batch_start + EMBED_BATCH_SIZE, total)
            batch = chunks[batch_start:batch_end]
            texts = [item["text"] for item in batch]
            _emit_progress(f"embedding batch {batch_start + 1}-{batch_end}/{total}…")
            embeddings = self.embedding_client.embed_texts(texts)
            for chunk, embedding in zip(batch, embeddings):
                all_rows.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "doc_type": chunk["doc_type"],
                        "path": chunk["path"],
                        "language": chunk.get("language"),
                        "symbol": chunk.get("symbol"),
                        "line_start": chunk["line_start"],
                        "line_end": chunk["line_end"],
                        "title": chunk.get("title"),
                        "section": chunk.get("section"),
                        "text": chunk["text"],
                        "vector": embedding,
                    }
                )
        return all_rows

    def _detect_changed_paths(self) -> set[str] | None:
        if not self.metadata_path.exists():
            return None

        t0 = time.monotonic()
        metadata = self._read_metadata()
        old_manifest = metadata.get("file_manifest", {})
        _emit_progress(f"scanning {len(old_manifest)} tracked files for changes…")
        logger.info("_detect_changed_paths: building file manifest (old has %d entries)...",
                     len(old_manifest))
        current_manifest = build_file_manifest(
            self.settings.workspace_root,
            self.settings,
        )
        t1 = time.monotonic()
        _emit_progress(f"file scan done ({t1 - t0:.1f}s), comparing manifests…")
        logger.info("_detect_changed_paths: build_file_manifest took %.1fms, %d files indexed",
                     (t1 - t0) * 1000, len(current_manifest))
        manifest_changed = diff_manifests(old_manifest, current_manifest)
        logger.info("_detect_changed_paths: diff_manifests found %d changed", len(manifest_changed))

        if is_git_repo(self.settings.workspace_root):
            t2 = time.monotonic()
            git_changed = get_git_changed_paths(
                self.settings.workspace_root,
                metadata.get("last_indexed_commit"),
            )
            logger.info("_detect_changed_paths: get_git_changed_paths took %.1fms, %d changed",
                         (time.monotonic() - t2) * 1000,
                         len(git_changed) if git_changed else -1)
            if git_changed is None:
                return manifest_changed
            return manifest_changed.union(git_changed)

        return manifest_changed

    def _build_metadata(
        self,
        *,
        dimensions: int | None,
        file_manifest: dict[str, dict],
        chunk_count: int,
        code_chunk_count: int,
        kb_chunk_count: int,
    ) -> dict:
        return {
            "version": 2,
            "indexed_at": int(time.time()),
            "embedding_model": self.settings.embedding_model,
            "embedding_dimensions": dimensions,
            "workspace_root": str(self.settings.workspace_root),
            "chunk_count": chunk_count,
            "code_chunk_count": code_chunk_count,
            "kb_chunk_count": kb_chunk_count,
            "last_indexed_commit": get_git_head(self.settings.workspace_root),
            "file_manifest": file_manifest,
        }

    def _count_rows(self, where_clause: str) -> int:
        table = self._open_table()
        return len(
            table.search()
            .where(where_clause)
            .select(["chunk_id"])
            .limit(1_000_000)
            .to_list()
        )

    def _write_metadata(self, metadata: dict) -> None:
        self.metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_metadata(self) -> dict:
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def _open_table(self):
        db = lancedb.connect(str(self.db_dir))
        return db.open_table(self.table_name)


def iter_candidate_files(root: Path, settings: Settings):
    git_paths = get_git_indexed_paths(root)
    if git_paths is not None:
        for rel_path in sorted(git_paths):
            if settings.is_path_ignored(rel_path):
                continue
            path = root / rel_path
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > settings.effective_max_file_bytes:
                continue
            if not settings.allows_language(detect_language(path)) and not settings.is_doc_path(rel_path):
                continue
            yield path
        return

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_path = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if settings.is_path_ignored(rel_path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > settings.effective_max_file_bytes:
            continue
        if not settings.allows_language(detect_language(path)) and not settings.is_doc_path(rel_path):
            continue
        yield path


def build_file_manifest(root: Path, settings: Settings) -> dict[str, dict]:
    manifest: dict[str, dict] = {}
    file_count = 0
    t_start = time.monotonic()
    for path in iter_candidate_files(root, settings):
        rel_path = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
            digest = file_sha256(path)
        except OSError:
            continue
        manifest[rel_path] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "sha256": digest,
        }
        file_count += 1
        if file_count % 500 == 0:
            t_now = time.monotonic()
            _emit_progress(f"file scan: {file_count} files, {t_now - t_start:.1f}s elapsed")
            logger.info("build_file_manifest: %d files scanned, total elapsed %.1fms",
                         file_count, (t_now - t_start) * 1000)
    return manifest


def diff_manifests(old: dict[str, dict], new: dict[str, dict]) -> set[str]:
    changed: set[str] = set()
    all_paths = set(old) | set(new)
    for path in all_paths:
        old_entry = old.get(path)
        new_entry = new.get(path)
        if old_entry is None or new_entry is None:
            changed.add(path)
            continue
        if manifest_signature(old_entry) != manifest_signature(new_entry):
            changed.add(path)
    return changed


def manifest_signature(entry: dict) -> tuple:
    if "sha256" in entry:
        return (entry.get("size"), entry.get("sha256"))
    return (entry.get("mtime_ns"), entry.get("size"))


def is_git_repo(root: Path) -> bool:
    return run_git(root, ["rev-parse", "--show-toplevel"]) is not None


def get_git_head(root: Path) -> str | None:
    result = run_git(root, ["rev-parse", "HEAD"])
    if result is None:
        return None
    return result.strip() or None


def get_git_changed_paths(root: Path, last_indexed_commit: str | None) -> set[str] | None:
    head = get_git_head(root)
    if head is None:
        return None

    changed: set[str] = set()
    if last_indexed_commit and last_indexed_commit != head:
        diff_output = run_git(root, ["diff", "--name-only", "-z", f"{last_indexed_commit}..HEAD"])
        if diff_output is None:
            return None
        changed.update(normalize_git_paths(diff_output))

    return changed


def get_git_status_entries(root: Path) -> list[dict] | None:
    output = run_git(root, ["status", "--porcelain=v1", "-z"])
    if output is None:
        return None

    entries: list[dict] = []
    parts = output.split("\0")
    index = 0
    while index < len(parts):
        raw = parts[index]
        index += 1
        if not raw:
            continue
        if len(raw) < 4:
            continue
        xy = raw[:2]
        path_text = raw[3:].replace("\\", "/")
        original_path: str | None = None
        if "R" in xy or "C" in xy:
            if index < len(parts):
                original_path = path_text
                path_text = parts[index].replace("\\", "/")
                index += 1
        entries.append(
            {
                "path": path_text,
                "original_path": original_path,
                "index_status": xy[0],
                "worktree_status": xy[1],
                "raw_status": xy,
            }
        )
    return entries


def get_git_numstat(root: Path) -> dict[str, dict] | None:
    output = run_git(root, ["diff", "--numstat", "--"])
    if output is None:
        return None

    stats: dict[str, dict] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path_text = parts[0], parts[1], parts[2]
        path_text = path_text.replace("\\", "/")
        stats[path_text] = {
            "added_lines": _parse_numstat_value(added_raw),
            "deleted_lines": _parse_numstat_value(deleted_raw),
        }
    return stats


def normalize_git_paths(output: str) -> set[str]:
    paths: set[str] = set()
    parts = output.split("\0") if "\0" in output else output.splitlines()
    for line in parts:
        path = line.strip().replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def get_git_indexed_paths(root: Path) -> set[str] | None:
    output = run_git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if output is None:
        return None
    return normalize_git_paths(output)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(root: Path, args: list[str]) -> str | None:
    t0 = time.monotonic()
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        logger.warning("run_git: git %s timed out after 5s", args[0])
        return None
    elapsed = (time.monotonic() - t0) * 1000
    if elapsed > 1000:
        logger.info("run_git: git %s took %.1fms", args[0], elapsed)
    if completed.returncode != 0:
        return None
    return completed.stdout


def short_snippet(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def escape_sql_string(value: str) -> str:
    return value.replace("'", "''")


def _parse_numstat_value(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None
