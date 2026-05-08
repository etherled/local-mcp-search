from __future__ import annotations

from .spans import open_spans


def build_context_pack(
    workspace_root,
    results: list[dict],
    *,
    max_chars: int,
    context_lines: int = 8,
) -> dict:
    spans = []
    for item in dedupe_results(results):
        spans.append(
            {
                "path": item["path"],
                "line_start": max(int(item["line_start"]) - context_lines, 1),
                "line_end": int(item["line_end"]) + context_lines,
            }
        )

    merged_spans = merge_spans(spans)
    packed_items = []
    used_chars = 0
    source_chars = 0
    trimmed_chars = 0
    for span in merged_spans:
        try:
            opened = open_spans(workspace_root, [span], max_total_chars=max_chars)[0]
        except (FileNotFoundError, ValueError, UnicodeDecodeError):
            continue
        content = opened["content"]
        source_chars += len(content)
        if used_chars + len(content) > max_chars:
            remaining = max_chars - used_chars
            if remaining <= 0:
                trimmed_chars += len(content)
                break
            trimmed_chars += len(content) - remaining
            content = content[:remaining]
            opened["truncated"] = True
        used_chars += len(content)
        opened["content"] = content
        packed_items.append(opened)
        if used_chars >= max_chars:
            break

    return {
        "items": packed_items,
        "total_chars": used_chars,
        "source_chars": source_chars,
        "trimmed_chars": trimmed_chars,
        "requested_span_count": len(spans),
        "merged_span_count": len(merged_spans),
        "truncated": len(packed_items) < len(merged_spans),
    }


def dedupe_results(results: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, int]] = set()
    output = []
    for item in results:
        key = (item["path"], int(item["line_start"]), int(item["line_end"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def merge_spans(spans: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for span in spans:
        grouped.setdefault(span["path"], []).append(span)

    merged: list[dict] = []
    for path, items in grouped.items():
        current: dict | None = None
        for item in sorted(items, key=lambda value: int(value["line_start"])):
            if current is None:
                current = dict(item)
                continue
            if int(item["line_start"]) <= int(current["line_end"]) + 3:
                current["line_end"] = max(int(current["line_end"]), int(item["line_end"]))
            else:
                merged.append(current)
                current = dict(item)
        if current is not None:
            merged.append(current)
    return merged
