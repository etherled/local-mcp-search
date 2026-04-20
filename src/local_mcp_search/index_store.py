from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import lancedb

from .chunking import chunk_code_text, chunk_kb_text, detect_doc_type, detect_language
from .config import DEFAULT_IGNORE_DIRS, Settings
from .embedding_client import EmbeddingClient
from .models import SearchResult


class IndexStore:
    def __init__(self, settings: Settings, embedding_client: EmbeddingClient) -> None:
        self.settings = settings
        self.embedding_client = embedding_client
        self.db_dir = settings.index_dir / "lancedb"
        self.metadata_path = settings.index_dir / "metadata.json"
        self.table_name = "chunks"

    def status(self) -> dict:
        if not self.metadata_path.exists():
            return {
                "repo_root": str(self.settings.workspace_root),
                "index_exists": False,
                "index_path": str(self.db_dir),
                "git_available": is_git_repo(self.settings.workspace_root),
            }

        payload = self._read_metadata()
        return {
            "repo_root": str(self.settings.workspace_root),
            "index_exists": True,
            "index_path": str(self.db_dir),
            "git_available": is_git_repo(self.settings.workspace_root),
            "indexed_at": payload.get("indexed_at"),
            "embedding_model": payload.get("embedding_model"),
            "embedding_dimensions": payload.get("embedding_dimensions"),
            "chunk_count": payload.get("chunk_count", 0),
            "code_chunk_count": payload.get("code_chunk_count", 0),
            "kb_chunk_count": payload.get("kb_chunk_count", 0),
            "last_indexed_commit": payload.get("last_indexed_commit"),
            "tracked_file_count": len(payload.get("file_manifest", {})),
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

    def semantic_search(
        self,
        query: str,
        *,
        doc_type: str,
        max_results: int,
        languages: list[str] | None = None,
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
        rows = query_builder.limit(max_results).to_list()

        results: list[SearchResult] = []
        for row in rows:
            distance = float(row.get("_distance", 0.0))
            score = 1.0 / (1.0 + max(distance, 0.0))
            results.append(
                SearchResult(
                    path=row["path"],
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_end"]),
                    symbol=row.get("symbol"),
                    snippet=short_snippet(row["text"]),
                    score=round(score, 4),
                    why_matched=f"semantic similarity against {doc_type} index",
                    title=row.get("title"),
                    section=row.get("section"),
                    chunk_id=row.get("chunk_id"),
                )
            )
        return results

    def _full_rebuild(self) -> dict:
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

        chunks = self._collect_chunks()
        rows = self._embed_chunks_to_rows(chunks)

        db = lancedb.connect(str(self.db_dir))
        db.create_table(self.table_name, data=rows, mode="overwrite")

        dimensions = len(rows[0]["vector"]) if rows else None
        metadata = self._build_metadata(
            dimensions=dimensions,
            file_manifest=build_file_manifest(
                self.settings.workspace_root,
                self.settings.max_file_bytes,
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
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

        if not self.metadata_path.exists():
            return self._full_rebuild()

        changed_paths = changed_paths if changed_paths is not None else self._detect_changed_paths()
        if changed_paths is None:
            return self._full_rebuild()
        if not changed_paths:
            status = self.status()
            status["reindex_mode"] = "incremental"
            status["changed_paths"] = []
            return status

        table = self._open_table()
        for rel_path in sorted(changed_paths):
            table.delete(f"path = '{escape_sql_string(rel_path)}'")

        new_chunks = self._collect_chunks_for_paths(changed_paths)
        rows = self._embed_chunks_to_rows(new_chunks)
        if rows:
            table.add(rows)

        previous_metadata = self._read_metadata()
        dimensions = previous_metadata.get("embedding_dimensions")
        if dimensions is None and rows:
            dimensions = len(rows[0]["vector"])

        refreshed_manifest = build_file_manifest(
            self.settings.workspace_root,
            self.settings.max_file_bytes,
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
        for path in iter_candidate_files(self.settings.workspace_root, self.settings.max_file_bytes):
            chunks.extend(self._chunk_file(path))
        return chunks

    def _collect_chunks_for_paths(self, changed_paths: set[str]) -> list[dict]:
        chunks: list[dict] = []
        for rel_path in sorted(changed_paths):
            path = self.settings.workspace_root / rel_path
            if not path.exists() or not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > self.settings.max_file_bytes:
                continue
            chunks.extend(self._chunk_file(path))
        return chunks

    def _chunk_file(self, path: Path) -> list[dict]:
        doc_type = detect_doc_type(path)
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
        texts = [item["text"] for item in chunks]
        embeddings = self.embedding_client.embed_texts(texts) if texts else []
        rows: list[dict] = []
        for chunk, embedding in zip(chunks, embeddings):
            rows.append(
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
        return rows

    def _detect_changed_paths(self) -> set[str] | None:
        if not self.metadata_path.exists():
            return None

        metadata = self._read_metadata()
        old_manifest = metadata.get("file_manifest", {})
        current_manifest = build_file_manifest(
            self.settings.workspace_root,
            self.settings.max_file_bytes,
        )
        manifest_changed = diff_manifests(old_manifest, current_manifest)

        if is_git_repo(self.settings.workspace_root):
            git_changed = get_git_changed_paths(
                self.settings.workspace_root,
                metadata.get("last_indexed_commit"),
            )
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


def iter_candidate_files(root: Path, max_file_bytes: int):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in DEFAULT_IGNORE_DIRS for part in path.parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            continue
        yield path


def build_file_manifest(root: Path, max_file_bytes: int) -> dict[str, dict]:
    manifest: dict[str, dict] = {}
    for path in iter_candidate_files(root, max_file_bytes):
        rel_path = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        manifest[rel_path] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return manifest


def diff_manifests(old: dict[str, dict], new: dict[str, dict]) -> set[str]:
    changed: set[str] = set()
    all_paths = set(old) | set(new)
    for path in all_paths:
        if old.get(path) != new.get(path):
            changed.add(path)
    return changed


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
        diff_output = run_git(root, ["diff", "--name-only", f"{last_indexed_commit}..HEAD"])
        if diff_output is None:
            return None
        changed.update(normalize_git_paths(diff_output))

    staged_output = run_git(root, ["diff", "--name-only", "--cached"])
    unstaged_output = run_git(root, ["diff", "--name-only"])
    untracked_output = run_git(root, ["ls-files", "--others", "--exclude-standard"])
    deleted_output = run_git(root, ["ls-files", "--deleted"])

    for output in (staged_output, unstaged_output, untracked_output, deleted_output):
        if output is None:
            return None
        changed.update(normalize_git_paths(output))

    return changed


def normalize_git_paths(output: str) -> set[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        path = line.strip().replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def run_git(root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except FileNotFoundError:
        return None
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
