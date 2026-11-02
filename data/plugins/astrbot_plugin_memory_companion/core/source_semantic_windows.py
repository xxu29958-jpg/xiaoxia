from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .source_semantic_common import _source_partition, _source_position


def build_semantic_window(
    anchor: Mapping[str, Any],
    *,
    char_start: int,
    char_end: int,
    previous: Mapping[str, Any] | None = None,
    following: Mapping[str, Any] | None = None,
    max_chars: int = 1800,
) -> dict[str, Any]:
    """Build one bounded neighbor window and exact owner-relative spans."""
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("semantic_window_limit_invalid")
    anchor_text = anchor.get("text")
    if not isinstance(anchor_text, str):
        raise ValueError("semantic_source_text_unavailable")
    if (
        isinstance(char_start, bool)
        or not isinstance(char_start, int)
        or char_start < 0
        or isinstance(char_end, bool)
        or not isinstance(char_end, int)
        or char_end < char_start
        or char_end > len(anchor_text)
    ):
        raise ValueError("semantic_anchor_invalid")
    anchor_piece = anchor_text[char_start:char_end]
    if len(anchor_piece) > max_chars:
        raise ValueError("semantic_anchor_exceeds_window")
    anchor_position = _source_position(anchor)
    anchor_partition = _source_partition(anchor)
    neighbors: list[tuple[str, Mapping[str, Any]]] = []
    for side, item in (("previous", previous), ("following", following)):
        if item is None:
            continue
        if (
            not isinstance(item.get("text"), str)
            or _source_partition(item) != anchor_partition
        ):
            raise ValueError("semantic_window_partition_mismatch")
        position = _source_position(item)
        if side == "previous" and not position < anchor_position:
            raise ValueError("semantic_window_order_invalid")
        if side == "following" and not position > anchor_position:
            raise ValueError("semantic_window_order_invalid")
        neighbors.append((side, item))

    context_budget = max(0, max_chars - len(anchor_piece) - len(neighbors))
    if len(neighbors) == 1:
        allocations = [context_budget]
    else:
        allocations = [context_budget // 2, context_budget - context_budget // 2]
    selected: dict[str, tuple[int, int, str, Mapping[str, Any]]] = {}
    for (side, item), budget in zip(neighbors, allocations):
        text = str(item["text"])
        size = min(len(text), budget)
        if side == "previous":
            start, end = len(text) - size, len(text)
        else:
            start, end = 0, size
        if size:
            selected[side] = (start, end, text[start:end], item)

    dependencies: list[dict[str, Any]] = []
    chunks: list[str] = []
    for side in ("previous", "anchor", "following"):
        if side == "anchor":
            source = anchor
            start, end, piece = char_start, char_end, anchor_piece
            role = "anchor"
        elif side in selected:
            start, end, piece, source = selected[side]
            role = "context"
        else:
            continue
        dependencies.append(
            {
                "source_id": str(source.get("source_id") or source.get("id") or ""),
                "source_version": str(source.get("source_version") or ""),
                "char_start": start,
                "char_end": end,
                "role": role,
            }
        )
        chunks.append(piece)
    if any(not dep["source_id"] or not dep["source_version"] for dep in dependencies):
        raise ValueError("semantic_dependency_invalid")
    return {
        "input_text": "\n".join(chunks),
        "dependencies": dependencies,
        "anchor_source_id": str(anchor.get("source_id") or anchor.get("id") or ""),
        "anchor_source_version": str(anchor.get("source_version") or ""),
        "char_start": char_start,
        "char_end": char_end,
    }
