from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Settings
from .models import SearchResult


def run_exact_search(
    settings: Settings,
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
            cwd=str(settings.workspace_root),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except FileNotFoundError:
        results = python_fallback_search(
            settings,
            query=query,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
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
        text = data["lines"]["text"].rstrip()
        rel_path = Path(data["path"]["text"]).as_posix()
        if settings.is_path_ignored(rel_path):
            continue
        if settings.matches_exclude_globs(rel_path, exclude_globs):
            continue
        if not settings.matches_include_globs(rel_path, include_globs):
            continue
        results.append(
            SearchResult(
                path=rel_path,
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
    settings: Settings,
    *,
    query: str,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    max_results: int,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    query_lower = query.lower()
    for path in settings.workspace_root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(settings.workspace_root).as_posix()
        if settings.is_path_ignored(rel_path):
            continue
        if settings.matches_exclude_globs(rel_path, exclude_globs):
            continue
        if not settings.matches_include_globs(rel_path, include_globs):
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
                    path=rel_path,
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
