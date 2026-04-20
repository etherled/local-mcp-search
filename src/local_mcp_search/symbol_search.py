from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import CODE_EXTENSIONS
from .models import SearchResult


SYMBOL_PATTERNS = [
    r"\bdef\s+{symbol}\b",
    r"\bclass\s+{symbol}\b",
    r"\bfunction\s+{symbol}\b",
    r"\binterface\s+{symbol}\b",
    r"\btype\s+{symbol}\b",
    r"\benum\s+{symbol}\b",
    r"\bconst\s+{symbol}\b",
    r"\blet\s+{symbol}\b",
    r"\bvar\s+{symbol}\b",
    r"\bexport\s+function\s+{symbol}\b",
    r"\bexport\s+class\s+{symbol}\b",
    r"\bexport\s+const\s+{symbol}\b",
]


def run_symbol_search(
    workspace_root: Path,
    symbol: str,
    *,
    max_results: int = 10,
) -> list[SearchResult]:
    if not symbol.strip():
        return []

    patterns = [pattern.format(symbol=symbol) for pattern in SYMBOL_PATTERNS]
    results: list[SearchResult] = []

    for pattern in patterns:
        if len(results) >= max_results:
            break
        results.extend(
            _run_pattern_search(
                workspace_root,
                pattern=pattern,
                max_results=max_results - len(results),
            )
        )

    deduped: list[SearchResult] = []
    seen: set[tuple[str, int, str]] = set()
    for item in results:
        key = (item.path, item.line_start, item.snippet)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped


def _run_pattern_search(
    workspace_root: Path,
    *,
    pattern: str,
    max_results: int,
) -> list[SearchResult]:
    command = ["rg", "--json", "-n", "-P", pattern, "."]
    for extension in CODE_EXTENSIONS:
        command.extend(["-g", f"*{extension}"])

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
        return []

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
        results.append(
            SearchResult(
                path=Path(data["path"]["text"]).as_posix(),
                line_start=data["line_number"],
                line_end=data["line_number"],
                symbol=None,
                snippet=text,
                score=1.0,
                why_matched="symbol-like declaration match via ripgrep",
            )
        )
    return results
