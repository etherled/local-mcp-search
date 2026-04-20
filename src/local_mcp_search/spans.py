from __future__ import annotations

from pathlib import Path


def open_spans(workspace_root: Path, items: list[dict], max_total_chars: int = 12_000) -> list[dict]:
    total_chars = 0
    output: list[dict] = []

    for item in items:
        rel_path = str(item["path"]).replace("\\", "/")
        line_start = int(item["line_start"])
        line_end = int(item["line_end"])
        target = safe_resolve(workspace_root, rel_path)
        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[max(line_start - 1, 0) : max(line_end, 0)]
        content = "\n".join(selected)
        total_chars += len(content)
        if total_chars > max_total_chars:
            raise ValueError("Requested spans exceed max_total_chars limit.")
        output.append(
            {
                "path": rel_path,
                "line_start": line_start,
                "line_end": line_end,
                "content": content,
            }
        )
    return output


def safe_resolve(workspace_root: Path, rel_path: str) -> Path:
    candidate = (workspace_root / rel_path).resolve()
    workspace_resolved = workspace_root.resolve()
    if candidate != workspace_resolved and workspace_resolved not in candidate.parents:
        raise ValueError(f"Path escapes workspace root: {rel_path}")
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(rel_path)
    return candidate
