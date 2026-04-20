from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import CODE_EXTENSIONS, DEFAULT_IGNORE_DIRS, KB_EXTENSIONS


def build_repo_overview(workspace_root: Path, max_entries: int = 12) -> dict:
    top_level_dirs: list[str] = []
    top_level_files: list[str] = []
    extension_counter: Counter[str] = Counter()
    doc_candidates: list[str] = []
    code_candidates: list[str] = []
    file_count = 0

    for path in workspace_root.rglob("*"):
        if any(part in DEFAULT_IGNORE_DIRS for part in path.parts):
            continue
        if path.is_dir() and path.parent == workspace_root:
            top_level_dirs.append(path.name)
            continue
        if not path.is_file():
            continue

        file_count += 1
        rel_path = path.relative_to(workspace_root).as_posix()
        suffix = path.suffix.lower()
        extension_counter[suffix or "<no_ext>"] += 1

        if path.parent == workspace_root:
            top_level_files.append(path.name)

        lowered_name = path.name.lower()
        if suffix in KB_EXTENSIONS or lowered_name in {
            "readme.md",
            "architecture.md",
            "contributing.md",
            "package.json",
            "pyproject.toml",
            "cargo.toml",
            "requirements.txt",
            "go.mod",
        }:
            doc_candidates.append(rel_path)

        if suffix in CODE_EXTENSIONS and (
            "main" in lowered_name
            or "app" in lowered_name
            or "server" in lowered_name
            or "index" in lowered_name
            or "cli" in lowered_name
        ):
            code_candidates.append(rel_path)

    common_extensions = [
        {"extension": ext, "count": count}
        for ext, count in extension_counter.most_common(max_entries)
    ]

    return {
        "workspace_root": str(workspace_root),
        "file_count": file_count,
        "top_level_dirs": sorted(top_level_dirs)[:max_entries],
        "top_level_files": sorted(top_level_files)[:max_entries],
        "common_extensions": common_extensions,
        "doc_entrypoints": sorted(set(doc_candidates))[:max_entries],
        "likely_code_entrypoints": sorted(set(code_candidates))[:max_entries],
    }
