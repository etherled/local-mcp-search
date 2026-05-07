from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .retrieval import RetrievalService
from .spans import open_spans as read_spans
from .watcher import maybe_start_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)

settings = Settings.from_env()
service = RetrievalService(settings)
maybe_start_watcher(service)
mcp = FastMCP(
    name="local-mcp-search",
    instructions=(
        "local-search is your PRIMARY interface for exploring this codebase. "
        "ALWAYS prefer local-search tools over Read, Grep, or Bash for code exploration. "
        "MUST use file_outline BEFORE reading any file to understand its structure first. "
        "After search tools return candidate paths, MUST use open_spans to fetch precise context — do NOT open entire files with Read. "

        "Routing rules: "
        "Concrete identifiers (symbol names, class names, function names, error text, config keys, routes, file names, env vars, SQL) → code_exact_search FIRST. "
        "Function/class/interface/type/enum/constant definitions → symbol_search. "
        "Similar logic, patterns, or rough functionality without exact text → code_semantic_search. "
        "Ready-to-read compact context for a task → code_context_pack (preferred over chaining multiple tools). "
        "Project structure / entrypoints → repo_overview (use early). "
        "Design docs, ADRs, plans, runbooks → kb_search. "
        "Project-policy, architecture, setup questions → doc_answer_context. "
        "Changing a named function/class/type/constant → symbol_context. "
        "Reviewing uncommitted changes or resuming work → change_context. "
        "Understanding runtime/framework/build system → dependency_overview. "
        "Index freshness and backend health → index_status (also reports health of embedding/reranker backends)."
    ),
    json_response=True,
)


@mcp.tool(
    description=(
        "Inspect local index status and backend health before relying on semantic search. "
        "Use this when you need to know whether the index exists, whether git-aware tracking is active, "
        "what commit was indexed, whether the background watcher is running, "
        "or whether the embedding/reranker backends are reachable and healthy."
    )
)
def index_status() -> dict:
    """Inspect whether the local semantic index exists, is fresh, and is tracking the current workspace."""
    return service.index_status()


@mcp.tool(
    description=(
        "Rebuild the local semantic index for code and knowledge files. "
        "Use mode=auto by default. Use mode=full when the index is missing, corrupt, or the chunking/embedding setup changed. "
        "Use mode=incremental when you know only a few files changed and want to refresh affected paths."
    )
)
def reindex(mode: str = "auto") -> dict:
    """Refresh the semantic index. Prefer auto unless you specifically need a full rebuild."""
    return service.reindex(mode=mode)


@mcp.tool(
    description=(
        "Search the codebase for exact or near-exact text matches. "
        "MUST use this FIRST when the user mentions ANY concrete string: symbol names, class names, "
        "function names, config keys, route strings, environment variables, SQL fragments, error messages, "
        "or filenames. Do NOT use code_semantic_search for these — it wastes tokens and is less accurate. "
        "Only use code_semantic_search when there is NO concrete identifier in the query."
    )
)
def code_exact_search(
    query: str,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_results: int = 10,
) -> dict:
    """Find exact or near-exact code matches for concrete text queries."""
    return service.code_exact_search(
        query,
        include_globs=include_globs,
        exclude_globs=exclude_globs,
        max_results=max_results,
    )


@mcp.tool(
    description=(
        "Search for likely symbol definitions such as functions, classes, interfaces, types, enums, and constants. "
        "Prefer this when the user names a specific symbol and wants to know where it is defined, not just where it is mentioned. "
        "Use code_exact_search instead when you need all occurrences of a concrete string."
    )
)
def symbol_search(
    symbol: str,
    max_results: int = 10,
) -> dict:
    """Find likely declaration sites for a named symbol."""
    return service.symbol_search(symbol, max_results=max_results)


@mcp.tool(
    description=(
        "Search the codebase for semantically similar implementations. "
        "ONLY use this when the query contains NO concrete identifier or string. "
        "If you have ANY concrete string (function name, class name, error text, config key, etc.), "
        "MUST use code_exact_search instead. This tool is strictly for vague queries like "
        "'find similar retry logic' or 'where is the pattern for building tool results'."
    )
)
def code_semantic_search(
    query: str,
    language: list[str] | None = None,
    max_results: int = 8,
) -> dict:
    """Find related code patterns or semantically similar implementations."""
    return service.code_semantic_search(
        query,
        language=language,
        max_results=max_results,
    )


@mcp.tool(
    description=(
        "Build a compact code context pack for implementation, debugging, or review. "
        "This is OFTEN YOUR BEST FIRST TOOL for implementation tasks — it combines semantic recall, "
        "reranking, deduplication, adjacent span merging, and a character budget in one call. "
        "Prefer this over manually chaining code_semantic_search and open_spans."
    )
)
def code_context_pack(
    query: str,
    language: list[str] | None = None,
    max_results: int = 8,
    max_chars: int | None = None,
) -> dict:
    """Return compact relevant code snippets for a task."""
    return service.code_context_pack(
        query,
        language=language,
        max_results=max_results,
        max_chars=max_chars,
    )


@mcp.tool(
    description=(
        "Search project knowledge files such as markdown docs, ADRs, plans, notes, and runbooks. "
        "Use this for design rationale, architecture decisions, workflows, requirements, implementation plans, or operational guidance. "
        "Do not use this when the user is clearly asking about executable code locations."
    )
)
def kb_search(
    query: str,
    max_results: int = 5,
) -> dict:
    """Find relevant documentation and project knowledge for design or operational questions."""
    return service.kb_search(query, max_results=max_results)


@mcp.tool(
    description=(
        "Build compact answer context from project knowledge files such as README, docs, ADRs, notes, and runbooks. "
        "Use this when the user asks a project-policy, architecture, setup, deployment, or workflow question and you need cited local context."
    )
)
def doc_answer_context(
    query: str,
    max_results: int = 6,
    max_chars: int | None = None,
) -> dict:
    """Return compact documentation snippets suitable for answering a project question."""
    return service.doc_answer_context(
        query,
        max_results=max_results,
        max_chars=max_chars,
    )


@mcp.tool(
    description=(
        "Return a lightweight outline for a single source file: functions, classes, interfaces, types, "
        "constants, and route-like declarations. MUST call this BEFORE reading any file with Read — "
        "it tells you what's inside so you can request only the relevant spans."
    )
)
def file_outline(path: str, max_items: int = 80) -> dict:
    """Summarize important declarations in one file."""
    return service.file_outline(path, max_items=max_items)


@mcp.tool(
    description=(
        "Gather compact context for a named symbol by combining definitions, references, and opened spans. "
        "ALWAYS use this before modifying or explaining a specific function, class, interface, type, "
        "enum, constant, or config key — it saves 3-5 separate tool calls."
    )
)
def symbol_context(
    symbol: str,
    max_results: int = 8,
    max_chars: int | None = None,
) -> dict:
    """Return definitions, references, and compact snippets for a symbol."""
    return service.symbol_context(
        symbol,
        max_results=max_results,
        max_chars=max_chars,
    )


@mcp.tool(
    description=(
        "Summarize changed files and provide compact context for current git/manifest changes. "
        "Use this before reviewing, continuing interrupted work, or estimating impact of local edits."
    )
)
def change_context(max_results: int = 30, max_chars: int | None = None) -> dict:
    """Return compact context for changed files in the current workspace."""
    return service.change_context(max_results=max_results, max_chars=max_chars)


@mcp.tool(
    description=(
        "Summarize dependency and build configuration files such as package.json, pyproject.toml, go.mod, Dockerfile, and compose files. "
        "Use this early when you need to understand the project's runtime, package manager, framework, or build system."
    )
)
def dependency_overview(max_files: int = 12) -> dict:
    """Return compact dependency/build configuration context."""
    return service.dependency_overview(max_files=max_files)


@mcp.tool(
    description=(
        "Return a lightweight overview of the current repository, including top-level directories, common file types, likely documentation entrypoints, "
        "and likely code entrypoints. "
        "Use this at the start of a task when you need a cheap project map before deeper searches."
    )
)
def repo_overview(max_entries: int = 12) -> dict:
    """Summarize the repository structure and likely entrypoints."""
    return service.repo_overview(max_entries=max_entries)


@mcp.tool(
    name="open_spans",
    description=(
        "Open precise file ranges returned by search tools. "
        "MUST use this instead of Read when you have specific line ranges to inspect. "
        "Do NOT open entire files with Read — always prefer this tool for targeted file reading. "
        "Pass only a small number of high-confidence spans to keep context compact."
    ),
)
def open_spans_tool(items: list[dict]) -> dict:
    """Fetch exact local snippets for selected search hits."""
    return {"items": read_spans(settings.workspace_root, items)}


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
