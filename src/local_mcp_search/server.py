from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .retrieval import RetrievalService
from .spans import open_spans as read_spans
from .watcher import maybe_start_watcher

settings = Settings.from_env()
service = RetrievalService(settings)
maybe_start_watcher(service)
mcp = FastMCP(
    name="local-mcp-search",
    instructions=(
        "Local retrieval server for the current workspace. "
        "Prefer code_exact_search when the user mentions concrete strings such as symbol names, error text, config keys, routes, file names, or identifiers. "
        "Use symbol_search when the user specifically wants function, class, interface, type, enum, or constant definitions. "
        "Use repo_overview early when you need a cheap map of the project structure, likely entrypoints, and key docs. "
        "Use code_semantic_search when the user asks for similar logic, related implementations, patterns, or rough functionality without exact text. "
        "Use kb_search for design docs, ADRs, runbooks, plans, and project knowledge. "
        "After any search tool returns candidate paths and line ranges, use open_spans to fetch precise local context instead of opening full files. "
        "Use index_status to inspect index freshness and reindex only when the index is missing or stale."
    ),
    json_response=True,
)


@mcp.tool(
    description=(
        "Inspect local index status before relying on semantic search. "
        "Use this when you need to know whether the index exists, whether git-aware tracking is active, "
        "what commit was indexed, or whether the background watcher is running."
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
        "Choose this first for symbol names, class names, function names, config keys, route strings, environment variables, "
        "SQL fragments, error messages, filenames, or any query containing concrete text. "
        "Do not use this as the first choice for broad semantic requests like 'find similar logic' or 'where is the retry pattern implemented'."
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
        "Search the codebase for semantically similar implementations when the user asks for related logic, examples, patterns, "
        "or functionality without exact text. "
        "Use this for requests like 'find similar retry logic', 'where do we build a tool result object', or "
        "'show examples of command execution with logging'. "
        "Do not prefer this over code_exact_search when the query already contains an exact identifier or string."
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
        "Use this after code_exact_search, code_semantic_search, or kb_search to inspect the most relevant local context. "
        "Prefer this over opening whole files. "
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
