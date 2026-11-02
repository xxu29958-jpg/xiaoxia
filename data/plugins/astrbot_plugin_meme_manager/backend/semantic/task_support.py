from __future__ import annotations

from .models import (
    REVIEW_CATEGORY,
    SemanticImage,
)

"""语义化任务状态机：有限并发、可暂停、可重试、可断点续传。"""


def _revision_original_category(
    item: SemanticImage,
    existing_categories: set[str],
) -> str:
    """返回仍然存在的自动重分类前原分类，旧数据缺字段时查历史记录。"""
    direct = str(item.reclassified_from_category or "").strip()
    if (
        direct
        and direct != item.category
        and direct != REVIEW_CATEGORY
        and direct in existing_categories
    ):
        return direct
    for record in reversed(item.reclassification_history):
        candidate = str(record.get("from_category") or "").strip()
        if (
            candidate
            and candidate != item.category
            and candidate != REVIEW_CATEGORY
            and candidate in existing_categories
        ):
            return candidate
    return ""


def _revision_category_choice(
    *,
    current_category: str,
    original_category: str,
    category_fit: str,
    suggested_category: str,
    selectable_categories: set[str],
) -> tuple[str, str]:
    """把模型分类结果转换成前端可直接复核的目标分类和动作。"""
    current = str(current_category or "").strip()
    original = str(original_category or "").strip()
    fit = str(category_fit or "uncertain").strip()
    suggested = str(suggested_category or "").strip()
    if (
        fit == "conflict"
        and suggested
        and suggested != current
        and suggested in selectable_categories
    ):
        action = "return_original" if suggested == original else "move_to_other"
        return suggested, action
    if current != REVIEW_CATEGORY and fit in {"match", "uncertain"}:
        return current, "keep_current"
    return "", "manual_required"
