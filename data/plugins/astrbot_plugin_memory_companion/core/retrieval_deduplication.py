from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .models import (
    MemoryRecord,
    SearchResult,
    SessionContext,
    clean_text,
)
from .time_intent import TimeIntent


class RetrievalEngineDeduplication:
    """Deduplication; shared runtime resources belong to the facade."""

    def _with_slot_reason(self, reason: str, slot: str) -> str:
        if reason.startswith("slot="):
            return reason
        return f"slot={slot};{reason}"

    def _collapse_mutable_fact_slots(
        self,
        query: str,
        ctx: SessionContext,
        selected: list[SearchResult],
        slot_map: dict[str, list[SearchResult]],
        *,
        time_intent: TimeIntent | None = None,
    ) -> tuple[list[SearchResult], list[dict[str, str]], dict[str, list[SearchResult]]]:
        collapsed, blocked = self._collapse_mutable_fact_results(
            query, ctx, selected, time_intent=time_intent
        )
        if not blocked:
            return selected, blocked, slot_map
        kept_ids = {item.memory.id for item in collapsed if item.memory.id}
        cleaned: dict[str, list[SearchResult]] = {}
        for slot, items in slot_map.items():
            kept = [
                item
                for item in items
                if not item.memory.id or item.memory.id in kept_ids
            ]
            if kept:
                cleaned[slot] = kept
            else:
                cleaned[slot] = []
        return collapsed, blocked, cleaned

    def _collapse_redundant_source_slots(
        self,
        selected: list[SearchResult],
        slot_map: dict[str, list[SearchResult]],
    ) -> tuple[list[SearchResult], list[dict[str, str]], dict[str, list[SearchResult]]]:
        collapsed, blocked = self._collapse_redundant_source_results(selected)
        if not blocked:
            return selected, blocked, slot_map
        kept_ids = {item.memory.id for item in collapsed if item.memory.id}
        cleaned: dict[str, list[SearchResult]] = {}
        for slot, items in slot_map.items():
            cleaned[slot] = [
                item
                for item in items
                if not item.memory.id or item.memory.id in kept_ids
            ]
        return collapsed, blocked, cleaned

    def _collapse_redundant_source_results(
        self,
        results: list[SearchResult],
    ) -> tuple[list[SearchResult], list[dict[str, str]]]:
        if len(results) < 2:
            return results, []
        groups: dict[tuple[str, str, str], list[tuple[int, SearchResult]]] = {}
        for index, item in enumerate(results):
            key = self._redundant_source_key(item.memory)
            if key:
                groups.setdefault(key, []).append((index, item))
        drop_reasons: dict[str, str] = {}
        for key, entries in groups.items():
            if len(entries) < 2:
                continue
            keep_index, keep_item = max(
                entries,
                key=lambda entry: (
                    entry[1].score,
                    self._memory_time_rank(entry[1].memory),
                    -entry[0],
                ),
            )
            keep_id = keep_item.memory.id
            for _index, item in entries:
                memory_id = item.memory.id
                if not memory_id or memory_id == keep_id:
                    continue
                drop_reasons.setdefault(
                    memory_id,
                    f"source_summary_duplicate:key={key[0]}:{key[1]};kept={keep_id}",
                )
        if not drop_reasons:
            return results, []
        collapsed = [item for item in results if item.memory.id not in drop_reasons]
        blocked = [
            {
                "id": item.memory.id,
                "reason": drop_reasons[item.memory.id],
                "content": clean_text(item.memory.content, 120),
            }
            for item in results
            if item.memory.id in drop_reasons
        ]
        return collapsed, blocked

    def _redundant_source_key(
        self, memory: MemoryRecord
    ) -> tuple[str, str, str] | None:
        source_plugin = clean_text(memory.source_plugin, 80).lower()
        memory_type = clean_text(memory.memory_type, 120).lower()
        if source_plugin != "livingmemory" or not memory_type.startswith(
            "livingmemory_graph:"
        ):
            return None
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        source_memory_id = clean_text(metadata.get("source_memory_id"), 120)
        nested = metadata.get("livingmemory_metadata")
        if not source_memory_id and isinstance(nested, dict):
            source_memory_id = clean_text(nested.get("source_memory_id"), 120)
        if not source_memory_id:
            return None
        persona_id = clean_text(metadata.get("persona_id"), 120)
        if not persona_id and isinstance(nested, dict):
            persona_id = clean_text(nested.get("persona_id"), 120)
        session_id = clean_text(memory.session_id, 200)
        if not session_id and isinstance(nested, dict):
            session_id = clean_text(nested.get("session_id"), 200)
        scope_key = f"{memory.scope}:{session_id or memory.group_id}:{persona_id}"
        return ("livingmemory_graph_source", source_memory_id, scope_key)

    def _collapse_mutable_fact_results(
        self,
        query: str,
        ctx: SessionContext,
        results: list[SearchResult],
        *,
        time_intent: TimeIntent | None = None,
    ) -> tuple[list[SearchResult], list[dict[str, str]]]:
        compact_query = re.sub(r"\s+", "", clean_text(query, 1000)).lower()
        historical_anchor = any(
            marker in compact_query
            for marker in (
                "之前",
                "以前",
                "当时",
                "那时",
                "那时候",
                "那会儿",
                "去年",
                "前年",
                "当年",
                "曾经",
            )
        ) or bool(re.search(r"(?<!\d)(?:19|20)\d{2}(?:年|[-/.])", compact_query))
        if (
            (time_intent and time_intent.active)
            or historical_anchor
            or self._looks_like_contextual_recall_query(query)
        ):
            return results, []
        query_keys = self._mutable_fact_keys(query)
        if len(results) < 2 or not query_keys:
            return results, []

        groups: dict[tuple[str, str, str], list[tuple[int, SearchResult]]] = {}
        for index, item in enumerate(results):
            memory_keys = self._mutable_fact_keys_for_memory(item.memory)
            matched = query_keys & memory_keys
            if not matched:
                continue
            owner_key = self._mutable_fact_owner_key(item.memory, ctx)
            subject_key = self._mutable_fact_subject_key(item.memory)
            for key in matched:
                # Keep explicitly qualified properties (for example home and
                # office addresses) in separate assertion slots. A broad
                # family keyword alone is not evidence that one fact replaces
                # another.
                qualifier = self._mutable_fact_qualifier(item.memory, key)
                groups.setdefault((owner_key, subject_key, key, qualifier), []).append(
                    (index, item)
                )

        drop_reasons: dict[str, str] = {}
        for (_owner_key, _subject_key, key, _qualifier), entries in groups.items():
            if len(entries) < 2:
                continue
            keep_index, keep_item = max(
                entries,
                key=lambda entry: (
                    self._memory_time_rank(entry[1].memory),
                    entry[1].score,
                    -entry[0],
                ),
            )
            keep_id = keep_item.memory.id
            for _index, item in entries:
                memory_id = item.memory.id
                if not memory_id or memory_id == keep_id:
                    continue
                drop_reasons.setdefault(
                    memory_id,
                    f"mutable_fact_latest_only:key={key};kept={keep_id}",
                )

        if not drop_reasons:
            return results, []
        collapsed = [item for item in results if item.memory.id not in drop_reasons]
        blocked = [
            {
                "id": item.memory.id,
                "reason": drop_reasons[item.memory.id],
                "content": clean_text(item.memory.content, 120),
            }
            for item in results
            if item.memory.id in drop_reasons
        ]
        return collapsed, blocked

    def _mutable_fact_qualifier(self, memory: MemoryRecord, key: str) -> str:
        if key != "address":
            return ""
        text = clean_text(
            " ".join([memory.content, memory.evidence, " ".join(memory.tags or [])]),
            1200,
        ).lower()
        for qualifier, aliases in {
            "home": ("家庭地址", "家里地址", "住家地址", "住宅地址"),
            "office": ("公司地址", "办公地址", "工作地址", "单位地址"),
        }.items():
            if any(alias in text for alias in aliases):
                return qualifier
        return ""

    def _mutable_fact_keys_for_memory(self, memory: MemoryRecord) -> set[str]:
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        metadata_text = " ".join(
            clean_text(metadata.get(key), 120)
            for key in ("key", "fact_key", "slot", "topic", "title")
            if clean_text(metadata.get(key), 120)
        )
        text = " ".join(
            [
                memory.content,
                memory.evidence,
                " ".join(memory.tags or []),
                memory.memory_type,
                metadata_text,
            ]
        )
        return self._mutable_fact_keys(text)

    def _mutable_fact_keys(self, text: str) -> set[str]:
        compact = re.sub(r"\s+", "", clean_text(text, 1200)).lower()
        if not compact:
            return set()
        families = {
            "password": (
                "密码",
                "口令",
                "暗号",
                "验证码",
                "pin",
                "passcode",
                "password",
                "token",
                "apikey",
                "api_key",
                "密钥",
                "秘钥",
            ),
            "account": (
                "账号",
                "账户",
                "帐号",
                "用户名",
                "登录名",
                "user_id",
                "userid",
            ),
            "phone": ("手机号", "手机号码", "电话", "联系电话", "号码"),
            "email": ("邮箱", "电子邮件", "email", "e-mail"),
            "address": ("地址", "住址", "位置", "收件地址"),
            "name": ("昵称", "称呼", "名字", "姓名", "网名"),
        }
        keys: set[str] = set()
        for key, aliases in families.items():
            if any(alias in compact for alias in aliases):
                keys.add(key)
        return keys

    def _mutable_fact_owner_key(self, memory: MemoryRecord, ctx: SessionContext) -> str:
        if memory.visibility == "bot_self":
            return "bot:self"
        if memory.scope == "group" or memory.visibility == "group_public":
            owner = (
                memory.group_id or memory.session_id or ctx.group_id or ctx.session_id
            )
            return f"group:{clean_text(owner, 160)}"
        if memory.scope == "private" or memory.visibility == "private_pair":
            owner = ""
            for entity in (memory.subject, memory.object):
                if entity.kind == "user" and entity.id and entity.id != "self":
                    owner = entity.id
                    break
            owner = owner or memory.session_id or ctx.user_id or ctx.session_id
            return f"private:{clean_text(owner, 160)}"
        owner = memory.session_id or ctx.session_id or memory.group_id
        return f"{clean_text(memory.scope or ctx.scope, 40)}:{clean_text(owner, 160)}"

    def _mutable_fact_subject_key(self, memory: MemoryRecord) -> str:
        ids: list[str] = []
        for entity in (memory.subject, memory.object):
            if entity.kind == "user" and entity.id and entity.id != "self":
                ids.append(clean_text(entity.id, 160))
        if ids:
            return "user:" + ",".join(dict.fromkeys(ids))
        return "window"

    def _memory_time_rank(self, memory: MemoryRecord) -> float:
        for value in (memory.occurred_at, memory.updated_at, memory.created_at):
            text = clean_text(value, 80)
            if not text:
                continue
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except Exception:
                continue
        return 0.0

    def _memory_overlaps_time_window(
        self, memory: MemoryRecord, time_intent: TimeIntent
    ) -> bool:
        if not time_intent.active:
            return True
        window_start = self._parse_time(time_intent.start_at)
        window_end = self._parse_time(time_intent.end_at)
        if window_start is None or window_end is None:
            return True
        metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
        candidate_ranges: list[tuple[datetime, datetime]] = []
        start_at = self._parse_time(clean_text(metadata.get("start_at"), 80))
        end_at = self._parse_time(clean_text(metadata.get("end_at"), 80))
        if start_at is not None or end_at is not None:
            start = start_at or end_at
            end = end_at or start_at
            if start is not None and end is not None:
                if end <= start:
                    end = start + timedelta(days=1)
                candidate_ranges.append((start, end))
        for value in (memory.occurred_at, memory.created_at, memory.updated_at):
            dt = self._parse_time(value)
            if dt is not None:
                candidate_ranges.append((dt, dt + timedelta(seconds=1)))
        if not candidate_ranges:
            return False
        return any(
            start < window_end and end > window_start for start, end in candidate_ranges
        )

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        text = clean_text(value, 80)
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
