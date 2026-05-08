from __future__ import annotations

import os
from dataclasses import dataclass
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

    @classmethod
    def from_env(cls) -> "Settings":
        workspace_root = Path(
            os.environ.get("MCP_SEARCH_WORKSPACE_ROOT", os.getcwd())
        ).resolve()
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
            code_chunk_lines=_get_int("MCP_SEARCH_CODE_CHUNK_LINES", 120) or 120,
            code_chunk_overlap=_get_int("MCP_SEARCH_CODE_CHUNK_OVERLAP", 20) or 20,
            kb_chunk_chars=_get_int("MCP_SEARCH_KB_CHUNK_CHARS", 1_600) or 1_600,
            kb_chunk_overlap=_get_int("MCP_SEARCH_KB_CHUNK_OVERLAP", 200) or 200,
            auto_reindex_enabled=_get_bool("MCP_SEARCH_AUTO_REINDEX", False),
            auto_reindex_interval_seconds=_get_int(
                "MCP_SEARCH_AUTO_REINDEX_INTERVAL_SECONDS", 5
            )
            or 5,
        )


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
