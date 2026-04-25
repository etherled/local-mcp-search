from __future__ import annotations

import re
from pathlib import Path

from .spans import safe_resolve


OUTLINE_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w$]*)")),
    ("python_function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")),
    ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][\w$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_][\w$]*)\s*=")),
    ("const", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_][\w$]*)\s*=")),
    ("route", re.compile(r"\b(?:app|router)\.(get|post|put|patch|delete)\s*\(")),
]


def build_file_outline(
    workspace_root: Path,
    path: str,
    *,
    max_items: int = 80,
) -> dict:
    target = safe_resolve(workspace_root, path)
    lines = target.read_text(encoding="utf-8").splitlines()
    items: list[dict] = []

    for line_number, line in enumerate(lines, start=1):
        for kind, pattern in OUTLINE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            name = match.group(1) if match.groups() else line.strip()
            if kind == "python_function":
                kind = "function"
            items.append(
                {
                    "kind": kind,
                    "name": name,
                    "line": line_number,
                    "preview": line.strip(),
                }
            )
            break
        if len(items) >= max_items:
            break

    return {
        "path": str(path).replace("\\", "/"),
        "line_count": len(lines),
        "items": items,
    }
