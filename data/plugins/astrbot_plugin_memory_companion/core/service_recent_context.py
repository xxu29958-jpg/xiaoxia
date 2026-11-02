from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    SessionContext,
    clean_text,
    json_loads,
)
from .service_support import (
    _RECENT_FACT_ASSERTION_MARKERS,
    _RECENT_FACT_PATTERNS,
)


class MemoryCompanionServiceRecentContext:
    """Recent context; state and transaction ownership remain in the facade."""

    async def _previous_context_gap_minutes(self, ctx: SessionContext) -> float | None:
        rows = await self.store.recent_timeline(
            limit=1,
            scope=ctx.scope,
            session_id=ctx.session_id,
            entity_id=ctx.current_target_id,
        )
        if not rows:
            return None
        timestamp = str(rows[0].get("occurred_at") or rows[0].get("created_at") or "")
        previous = self._parse_utc_datetime(timestamp)
        if previous is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - previous).total_seconds() / 60)

    def _parse_utc_datetime(self, value: str) -> datetime | None:
        text = clean_text(value, 80)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _local_time_label(value: Any) -> str:
        text = clean_text(value, 80)
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    @staticmethod
    def _recent_fact_categories(text: Any) -> set[str]:
        compact = clean_text(text, 600)
        if not compact:
            return set()
        question_like = bool(
            re.search(r"[?？]|吗|是不是|有没有|什么时候|几点", compact)
        )
        self_marker = bool(re.search(r"我|俺|咱|本人", compact))
        if question_like and not self_marker:
            return set()
        if question_like and re.search(
            r"你.{0,12}(?:吃|洗澡|冲澡|上班|下班|加班|到家|回家|回宿舍|休息|睡觉|睡醒)",
            compact,
        ):
            return set()
        assertive = any(marker in compact for marker in _RECENT_FACT_ASSERTION_MARKERS)
        if not assertive and not re.search(r"(?:了|啦|过|着|中)[~～。.!！]*$", compact):
            return set()
        return {
            name for name, pattern in _RECENT_FACT_PATTERNS if pattern.search(compact)
        }

    @staticmethod
    def _recent_fact_direct_text(value: Any) -> str:
        text = clean_text(value, 600)
        if not text:
            return ""
        text = text.split("[引用链上下文]", 1)[0]
        return clean_text(text, 180)

    @staticmethod
    def _message_explicitly_shares_private_context(text: Any) -> bool:
        compact = re.sub(r"\s+", "", clean_text(text, 800)).lower()
        if not compact:
            return False
        if re.search(
            r"(?:不要|别|不许|禁止|不能|不可以|拒绝).{0,16}(?:私聊|私信|小窗|单聊)",
            compact,
        ):
            return False
        if re.search(
            r"(?:私聊|私信|小窗|单聊).{0,16}(?:不要|别|不许|禁止|不能|不可以|拒绝).{0,12}(?:群|公开|说|提|发|同步|带)",
            compact,
        ):
            return False
        private_reference = r"(?:私聊|私信|小窗|单聊|单独聊)"
        group_destination = r"(?:群里|群聊|这个群|大家|公开)"
        private_then_group = re.search(
            rf"{private_reference}.{{0,24}}(?:带到|拿到|同步到|转到|发到|贴到|放到|接到|搬到|公开给|告诉).{{0,8}}{group_destination}",
            compact,
        )
        group_then_private = re.search(
            rf"{group_destination}.{{0,16}}(?:继续|接着|说|聊|同步|公开|提起|提到).{{0,20}}{private_reference}",
            compact,
        )
        private_in_group = re.search(
            rf"{private_reference}.{{0,20}}(?:在|到).{{0,6}}{group_destination}.{{0,12}}(?:继续|接着|说|聊|同步|公开|提起|提到)",
            compact,
        )
        return bool(private_then_group or group_then_private or private_in_group)

    async def _recent_cross_window_context(self, ctx: SessionContext) -> str:
        if not self._context_bool(ctx, "cross_window_recent_continuity_enabled", True):
            return ""
        if ctx.scope not in {"private", "group"}:
            return ""
        current_session_id = clean_text(ctx.session_id, 200)
        current_user_id = clean_text(ctx.user_id, 120)
        current_bot_id = clean_text(ctx.bot_id, 120)
        current_platform = clean_text(ctx.platform, 80).lower()
        if (
            not current_session_id
            or not current_user_id
            or not current_bot_id
            or not current_platform
        ):
            return ""

        if ctx.scope == "private":
            if not self._context_bool(
                ctx, "cross_window_group_to_private_enabled", True
            ):
                return ""
            source_scope = "group"
            direction = "近期群聊到当前私聊"
        else:
            source_scope = "private"
            if not self._context_bool(
                ctx, "cross_window_private_to_group_enabled", True
            ):
                return ""
            if not self._message_explicitly_shares_private_context(ctx.message_text):
                return ""
            direction = "经当前用户本轮明确授权，从近期私聊到当前群聊"

        ttl_minutes = max(
            1, min(24 * 60, self._context_int(ctx, "cross_window_recent_minutes", 30))
        )
        event_limit = max(
            1, min(12, self._context_int(ctx, "cross_window_recent_event_limit", 6))
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=ttl_minutes)
        rows = await self.store.recent_cross_window_timeline(
            source_scope=source_scope,
            current_session_id=current_session_id,
            since_at=cutoff.isoformat(timespec="seconds"),
            limit=max(32, event_limit * 8),
        )
        if not rows:
            return ""

        selected: list[dict[str, Any]] = []
        for row in rows:
            raw_metadata = row.get("metadata")
            metadata = (
                raw_metadata
                if isinstance(raw_metadata, dict)
                else json_loads(raw_metadata, {})
            )
            if not isinstance(metadata, dict):
                continue
            if clean_text(metadata.get("owner_bot_id"), 120) != current_bot_id:
                continue
            if clean_text(metadata.get("platform"), 80).lower() != current_platform:
                continue
            if clean_text(metadata.get("participant_user_id"), 120) != current_user_id:
                continue

            event_type = clean_text(row.get("event_type"), 40)
            subject_id = clean_text(row.get("subject_id"), 120)
            if event_type == "user_message":
                if subject_id != current_user_id:
                    continue
                speaker = "当前用户"
            elif event_type == "bot_response":
                if subject_id not in {current_bot_id, "self"}:
                    continue
                speaker = "Bot"
            else:
                continue

            occurred_at = self._parse_utc_datetime(
                clean_text(row.get("occurred_at") or row.get("created_at"), 80)
            )
            if occurred_at is None or occurred_at < cutoff:
                continue
            visible = self._sanitize_visible_timeline_text(row.get("content"))
            snippet = self._recent_fact_direct_text(visible)
            snippet = self.injection._redact_sensitive_text(snippet)
            if not snippet:
                continue
            selected.append(
                {
                    "occurred_at": occurred_at,
                    "speaker": speaker,
                    "text": clean_text(snippet, 160),
                }
            )
            if len(selected) >= event_limit:
                break

        if not selected:
            return ""
        lines = [
            f"方向：{direction}；有效窗口：近 {ttl_minutes} 分钟；仅含当前用户与同一 Bot 的片段。"
        ]
        for item in reversed(selected):
            local_time = (
                item["occurred_at"]
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .strftime("%H:%M")
            )
            lines.append(f"{local_time} {item['speaker']}：{item['text']}")
        return clean_text(" | ".join(lines), 1100)

    async def _recent_fact_guard_context(self, ctx: SessionContext) -> str:
        if ctx.scope != "private" or not ctx.session_id or not ctx.user_id:
            return ""
        if not self._context_bool(ctx, "recent_fact_guard_enabled", True):
            return ""

        event_limit = max(
            8, min(80, self._context_int(ctx, "recent_fact_guard_event_limit", 24))
        )
        max_items = max(
            1, min(8, self._context_int(ctx, "recent_fact_guard_max_items", 4))
        )
        ttl_hours = max(
            1, min(24, self._context_int(ctx, "recent_fact_guard_hours", 3))
        )
        rows = await self.store.recent_timeline(
            limit=event_limit,
            scope=ctx.scope,
            session_id=ctx.session_id,
            entity_id=ctx.user_id,
        )
        if not rows:
            return ""

        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        events: list[dict[str, Any]] = []
        for row in reversed(rows):
            event_type = clean_text(row.get("event_type"), 40)
            if event_type not in {"user_message", "bot_response"}:
                continue
            if (
                event_type == "user_message"
                and clean_text(row.get("subject_id"), 120) != ctx.user_id
            ):
                continue
            if ctx.message_id and clean_text(row.get("message_id"), 120) == clean_text(
                ctx.message_id, 120
            ):
                continue
            occurred_at = self._parse_utc_datetime(
                clean_text(row.get("occurred_at") or row.get("created_at"), 80)
            )
            if occurred_at is None or occurred_at < cutoff:
                continue
            text = self._recent_fact_direct_text(
                self._sanitize_visible_timeline_text(row.get("content"))
            )
            if not text:
                continue
            categories = (
                self._recent_fact_categories(text)
                if event_type == "user_message"
                else {
                    name
                    for name, pattern in _RECENT_FACT_PATTERNS
                    if pattern.search(text)
                }
            )
            events.append(
                {
                    "event_type": event_type,
                    "text": text,
                    "categories": categories,
                    "occurred_at": occurred_at,
                }
            )

        user_facts: list[dict[str, Any]] = []
        for item in events:
            if item["event_type"] != "user_message" or not item["categories"]:
                continue
            user_facts.append(dict(item))
        if not user_facts:
            return ""

        selected_newest: list[dict[str, Any]] = []
        covered_categories: set[str] = set()
        for fact in reversed(user_facts):
            if fact["categories"].issubset(covered_categories):
                continue
            selected_newest.append(fact)
            covered_categories.update(fact["categories"])
            if len(selected_newest) >= max_items:
                break
        selected = list(reversed(selected_newest))

        lines = [
            f"以下是同一私聊近 {ttl_hours} 小时的原始事实锚点，不是长期摘要；有更新时以较新的明确说法为准："
        ]
        for fact in selected:
            local_time = (
                fact["occurred_at"]
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .strftime("%H:%M")
            )
            user_text = self.injection._redact_sensitive_text(fact["text"])
            line = f"- {local_time} 用户明确说：{user_text}"
            acknowledgements = [
                item
                for item in events
                if item["event_type"] == "bot_response"
                and fact["categories"] & item["categories"]
                and fact["occurred_at"]
                <= item["occurred_at"]
                <= fact["occurred_at"] + timedelta(minutes=10)
            ]
            if acknowledgements:
                acknowledgement = min(
                    acknowledgements, key=lambda item: item["occurred_at"]
                )
                bot_text = self.injection._redact_sensitive_text(
                    acknowledgement["text"]
                )
                line += f"；Bot 随后已围绕此事回应：{bot_text}"
            lines.append(line)
        lines.append(
            "回复前留意准备询问的状态是否已经有答案；若 Bot 已有针对性回应，更自然的处理是承认刚才没接上，而不是说‘没看到’。"
        )
        return clean_text("\n".join(lines), 900)
