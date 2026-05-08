from __future__ import annotations

import os
from dataclasses import dataclass
import json
from fnmatch import fnmatch
from pathlib import Path


DEFAULT_IGNORE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    "target",
    ".mcp-index",
    "__pycache__",
}

CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

KB_EXTENSIONS = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".adoc",
}

KB_DIR_HINTS = {"docs", "doc", "wiki", "adr", "runbooks", "knowledge", "kb"}
PROJECT_CONFIG_BASENAME = ".local-search.json"


@dataclass(slots=True)
class Settings:
    workspace_root: Path
    index_dir: Path
    embedding_base_url: str
    embedding_model: str
    embedding_api_key: str
    embedding_timeout_seconds: int
    embedding_dimensions: int | None
    reranker_enabled: bool
    reranker_base_url: str
    reranker_model: str
    reranker_api_key: str
    reranker_timeout_seconds: int
    reranker_candidate_multiplier: int
    reranker_max_candidates: int
    reranker_cache_enabled: bool
    reranker_cache_max_entries: int
    context_pack_max_chars: int
    max_file_bytes: int
    code_chunk_lines: int
    code_chunk_overlap: int
    kb_chunk_chars: int
    kb_chunk_overlap: int
    auto_reindex_enabled: bool
    auto_reindex_interval_seconds: int
    query_debug_enabled: bool
    project_ignore_dirs: tuple[str, ...]
    project_doc_dirs: tuple[str, ...]
    project_max_file_bytes: int | None
    project_languages: tuple[str, ...]
    project_config_path: Path | None

    @classmethod
    def from_env(cls) -> "Settings":
        workspace_root = Path(
            os.environ.get("MCP_SEARCH_WORKSPACE_ROOT", os.getcwd())
        ).resolve()
        project_config_path = workspace_root / PROJECT_CONFIG_BASENAME
        project_config = _load_project_config(project_config_path)
        index_dir = Path(
            os.environ.get("MCP_SEARCH_INDEX_DIR", workspace_root / ".mcp-index")
        ).resolve()
        return cls(
            workspace_root=workspace_root,
            index_dir=index_dir,
            embedding_base_url=os.environ.get(
                "EMBEDDING_BASE_URL", "http://127.0.0.1:8887/v1"
            ),
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "text-embedding-bge-base-zh"
            ),
            embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
            embedding_dimensions=_get_int("EMBEDDING_DIMENSIONS"),
            embedding_timeout_seconds=_get_int("EMBEDDING_TIMEOUT_SECONDS", 10) or 10,
            reranker_enabled=_get_bool("MCP_SEARCH_RERANKER_ENABLED", True),
            reranker_base_url=os.environ.get("RERANKER_BASE_URL", ""),
            reranker_model=os.environ.get("RERANKER_MODEL", ""),
            reranker_api_key=os.environ.get("RERANKER_API_KEY", ""),
            reranker_timeout_seconds=_get_int("RERANKER_TIMEOUT_SECONDS", 30) or 30,
            reranker_candidate_multiplier=_get_int(
                "MCP_SEARCH_RERANKER_CANDIDATE_MULTIPLIER", 6
            )
            or 6,
            reranker_max_candidates=_get_int("MCP_SEARCH_RERANKER_MAX_CANDIDATES", 80)
            or 80,
            reranker_cache_enabled=_get_bool("MCP_SEARCH_RERANKER_CACHE_ENABLED", True),
            reranker_cache_max_entries=_get_int(
                "MCP_SEARCH_RERANKER_CACHE_MAX_ENTRIES", 5_000
            )
            or 5_000,
            context_pack_max_chars=_get_int("MCP_SEARCH_CONTEXT_PACK_MAX_CHARS", 20_000)
            or 20_000,
            max_file_bytes=_get_int("MCP_SEARCH_MAX_FILE_BYTES", 300_000) or 300_000,
            code_chunk_lines=_get_int("MCP_SEARCH_CODE_CHUNK_LINES", 80) or 80,
            code_chunk_overlap=_get_int("MCP_SEARCH_CODE_CHUNK_OVERLAP", 20) or 20,
            kb_chunk_chars=_get_int("MCP_SEARCH_KB_CHUNK_CHARS", 1_600) or 1_600,
            kb_chunk_overlap=_get_int("MCP_SEARCH_KB_CHUNK_OVERLAP", 200) or 200,
            auto_reindex_enabled=_get_bool("MCP_SEARCH_AUTO_REINDEX", False),
            auto_reindex_interval_seconds=_get_int(
                "MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS", 5
            )
            or 5,
            query_debug_enabled=_get_bool("MCP_SEARCH_QUERY_DEBUG", False),
            project_ignore_dirs=tuple(
                _normalize_name_list(project_config.get("ignore_dirs", []))
            ),
            project_doc_dirs=tuple(
                _normalize_name_list(project_config.get("doc_dirs", []))
            ),
            project_max_file_bytes=_normalize_optional_int(
                project_config.get("max_file_bytes")
            ),
            project_languages=tuple(
                _normalize_name_list(project_config.get("languages", []))
            ),
            project_config_path=project_config_path if project_config_path.exists() else None,
        )

    @property
    def effective_ignore_dirs(self) -> set[str]:
        return DEFAULT_IGNORE_DIRS.union(self.project_ignore_dirs)

    @property
    def effective_doc_dirs(self) -> set[str]:
        return set(self.project_doc_dirs) if self.project_doc_dirs else set(KB_DIR_HINTS)

    @property
    def effective_max_file_bytes(self) -> int:
        if self.project_max_file_bytes is not None:
            return self.project_max_file_bytes
        return self.max_file_bytes

    @property
    def effective_languages(self) -> set[str] | None:
        if not self.project_languages:
            return None
        return set(self.project_languages)

    def is_path_ignored(self, rel_path: str) -> bool:
        parts = [part.lower() for part in rel_path.replace("\\", "/").split("/") if part]
        return any(part in self.effective_ignore_dirs for part in parts)

    def is_doc_path(self, rel_path: str) -> bool:
        parts = {part.lower() for part in rel_path.replace("\\", "/").split("/") if part}
        return bool(parts.intersection(self.effective_doc_dirs))

    def allows_language(self, language: str | None) -> bool:
        effective_languages = self.effective_languages
        if effective_languages is None:
            return True
        return language in effective_languages

    def matches_include_globs(self, rel_path: str, patterns: list[str] | None) -> bool:
        if not patterns:
            return True
        normalized = rel_path.replace("\\", "/")
        return any(fnmatch(normalized, pattern) for pattern in patterns)

    def matches_exclude_globs(self, rel_path: str, patterns: list[str] | None) -> bool:
        if not patterns:
            return False
        normalized = rel_path.replace("\\", "/")
        return any(fnmatch(normalized, pattern) for pattern in patterns)


def _get_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_project_config(path: Path) -> dict:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_name_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().strip("/\\").lower()
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _normalize_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
