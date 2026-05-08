from __future__ import annotations

from pathlib import Path
import re

from .config import CODE_EXTENSIONS, KB_EXTENSIONS, Settings


SYMBOL_BOUNDARY_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|enum)\s+([A-Za-z_][\w$]*)"
    r"|^\s*(?:export\s+)?const\s+([A-Za-z_][\w$]*)\s*="
)


def detect_doc_type(path: Path, settings: Settings) -> str | None:
    suffix = path.suffix.lower()
    rel_path = path.relative_to(settings.workspace_root).as_posix()
    if suffix in KB_EXTENSIONS or settings.is_doc_path(rel_path):
        return "kb"
    if suffix in CODE_EXTENSIONS and settings.allows_language(detect_language(path)):
        return "code"
    return None


def detect_language(path: Path) -> str | None:
    return CODE_EXTENSIONS.get(path.suffix.lower())


def chunk_code_text(path: Path, text: str, settings: Settings) -> list[dict]:
    lines = text.splitlines()
    if not lines:
        return []

    symbol_chunks = chunk_code_by_symbols(path, lines, settings)
    if symbol_chunks:
        return symbol_chunks

    size = settings.code_chunk_lines
    overlap = min(settings.code_chunk_overlap, max(size - 1, 0))
    step = max(size - overlap, 1)

    chunks: list[dict] = []
    for start_idx in range(0, len(lines), step):
        end_idx = min(start_idx + size, len(lines))
        window = lines[start_idx:end_idx]
        if not window:
            continue
        chunk_text = "\n".join(window).strip()
        if not chunk_text:
            continue
        chunks.append(
            {
                "path": str(path),
                "line_start": start_idx + 1,
                "line_end": end_idx,
                "symbol": infer_symbol_hint(window),
                "text": chunk_text,
            }
        )
        if end_idx >= len(lines):
            break
    return chunks


def chunk_code_by_symbols(path: Path, lines: list[str], settings: Settings) -> list[dict]:
    if path.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
        return []

    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = SYMBOL_BOUNDARY_RE.search(line)
        if not match:
            continue
        name = match.group(1) or match.group(2)
        starts.append((index, name))

    if len(starts) < 2:
        return []

    chunks: list[dict] = []
    max_lines = max(settings.code_chunk_lines, 40)
    for position, (start_idx, symbol) in enumerate(starts):
        next_start = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        end_idx = min(next_start, start_idx + max_lines)
        chunk_text = "\n".join(lines[start_idx:end_idx]).strip()
        if not chunk_text:
            continue
        chunks.append(
            {
                "path": str(path),
                "line_start": start_idx + 1,
                "line_end": end_idx,
                "symbol": symbol,
                "text": chunk_text,
            }
        )
    return chunks


def chunk_kb_text(path: Path, text: str, settings: Settings) -> list[dict]:
    lines = text.splitlines()
    if not lines:
        return []

    sections: list[tuple[str | None, list[str], int]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    current_start_line = 1

    for line_no, line in enumerate(lines, start=1):
        if line.lstrip().startswith("#"):
            if current_lines:
                sections.append((current_title, current_lines, current_start_line))
            current_title = line.lstrip("#").strip() or None
            current_lines = [line]
            current_start_line = line_no
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, current_lines, current_start_line))

    chunks: list[dict] = []
    max_chars = settings.kb_chunk_chars
    overlap = settings.kb_chunk_overlap

    for title, section_lines, start_line in sections:
        section_text = "\n".join(section_lines).strip()
        if not section_text:
            continue

        cursor = 0
        while cursor < len(section_text):
            end = min(cursor + max_chars, len(section_text))
            chunk_text = section_text[cursor:end].strip()
            if chunk_text:
                line_start = start_line
                line_end = start_line + len(section_lines) - 1
                chunks.append(
                    {
                        "path": str(path),
                        "line_start": line_start,
                        "line_end": line_end,
                        "title": title,
                        "section": title,
                        "text": chunk_text,
                    }
                )
            if end >= len(section_text):
                break
            cursor = max(end - overlap, cursor + 1)
    return chunks


def infer_symbol_hint(lines: list[str]) -> str | None:
    for line in lines[:20]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("def ", "class ", "function ", "interface ", "type ")):
            name = stripped.split()[1]
            return name.split("(")[0].split("{")[0].strip(":")
        if stripped.startswith(("export function ", "export class ")):
            parts = stripped.split()
            if len(parts) >= 3:
                return parts[2].split("(")[0].split("{")[0]
        if stripped.startswith("const ") and "=" in stripped:
            return stripped.split("=", 1)[0].replace("const", "").strip()
    return None
