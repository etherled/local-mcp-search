from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import SearchResult


def run_exact_search(
    workspace_root: Path,
    query: str,
    *,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_results: int = 10,
) -> tuple[list[SearchResult], dict]:
    if not query.strip():
        return [], {
            "engine": "ripgrep",
            "query": query,
            "returned_results": 0,
            "max_results": max_results,
            "include_globs": include_globs or [],
            "exclude_globs": exclude_globs or [],
            "fallback_used": False,
        }

    command = ["rg", "--json", "-n", "-S", query, "."]
    for pattern in include_globs or []:
        command.extend(["-g", pattern])
    for pattern in exclude_globs or []:
        command.extend(["-g", f"!{pattern}"])

    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except FileNotFoundError:
        results = python_fallback_search(
            workspace_root,
            query=query,
            max_results=max_results,
        )
        return results, {
            "engine": "python-fallback",
            "query": query,
            "returned_results": len(results),
            "max_results": max_results,
            "include_globs": include_globs or [],
            "exclude_globs": exclude_globs or [],
            "fallback_used": True,
        }

    results: list[SearchResult] = []
    for line in completed.stdout.splitlines():
        if len(results) >= max_results:
            break
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") != "match":
            continue
        data = item["data"]
        submatches = data.get("submatches", [])
        text = data["lines"]["text"].rstrip()
        results.append(
            SearchResult(
                path=Path(data["path"]["text"]).as_posix(),
                line_start=data["line_number"],
                line_end=data["line_number"],
                symbol=None,
                snippet=text,
                score=1.0,
                why_matched="exact string match via ripgrep",
            )
        )
    return results, {
        "engine": "ripgrep",
        "query": query,
        "returned_results": len(results),
        "max_results": max_results,
        "include_globs": include_globs or [],
        "exclude_globs": exclude_globs or [],
        "fallback_used": False,
    }


def python_fallback_search(
    workspace_root: Path,
    *,
    query: str,
    max_results: int,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    query_lower = query.lower()
    for path in workspace_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if query_lower not in line.lower():
                continue
            results.append(
                SearchResult(
                    path=path.relative_to(workspace_root).as_posix(),
                    line_start=line_number,
                    line_end=line_number,
                    symbol=None,
                    snippet=line.strip(),
                    score=1.0,
                    why_matched="exact substring match via python fallback",
                )
            )
            if len(results) >= max_results:
                return results
    return results
