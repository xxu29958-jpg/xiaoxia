from __future__ import annotations

from typing import Any

from quart import request

from .core.bridge import serialize_memory
from .core.identity import normalize_session_context_fields, parse_scope_from_session
from .core.models import SessionContext, clean_text
from .page_api_support import (
    logger,
)


class PluginPageApiQueries:
    """Queries; shared runtime resources belong to the facade."""

    async def search(self):
        payload = await self._json()
        query = clean_text(payload.get("query"), 500)
        if not query:
            return self._err("missing query", 400)
        raw_context = {
            "session_id": clean_text(payload.get("session_id"), 200),
            "scope": clean_text(payload.get("scope"), 40).lower() or "unknown",
            "platform": clean_text(payload.get("platform"), 80),
            "user_id": clean_text(payload.get("user_id"), 120),
            "group_id": clean_text(payload.get("group_id"), 120),
        }
        requested_bot_id = clean_text(payload.get("bot_id"), 120)
        context_mode = (
            clean_text(
                payload.get("context_mode") or payload.get("search_mode"),
                20,
            ).lower()
            or "auto"
        )
        context_mode = {"context": "session", "window": "session"}.get(
            context_mode, context_mode
        )
        if context_mode not in {"auto", "all", "session"}:
            return self._err("invalid context_mode", 400)
        parsed_scope, parsed_target = parse_scope_from_session(
            raw_context["session_id"]
        )
        has_context_intent = (
            raw_context["scope"] in {"private", "group"}
            or any(raw_context[key] for key in ("session_id", "user_id", "group_id"))
            or bool(requested_bot_id)
        )
        if context_mode != "all":
            if raw_context["scope"] not in {"unknown", "private", "group"}:
                return self._err("invalid search scope", 400)
            if (
                parsed_scope in {"private", "group"}
                and raw_context["scope"] in {"private", "group"}
                and raw_context["scope"] != parsed_scope
            ):
                return self._err("search scope conflicts with session_id", 400)
            if (
                parsed_scope == "private"
                and parsed_target
                and raw_context["user_id"]
                and raw_context["user_id"] != parsed_target
            ):
                return self._err("private search target conflicts with session_id", 400)
            if (
                parsed_scope == "group"
                and parsed_target
                and raw_context["group_id"]
                and raw_context["group_id"] != parsed_target
            ):
                return self._err("group search target conflicts with session_id", 400)
            if raw_context["user_id"] and raw_context["group_id"]:
                return self._err(
                    "search context cannot contain both user_id and group_id", 400
                )
            if raw_context["scope"] == "private" and raw_context["group_id"]:
                return self._err("private search context cannot contain group_id", 400)
            if raw_context["scope"] == "group" and raw_context["user_id"]:
                return self._err("group search context cannot contain user_id", 400)
        normalized = normalize_session_context_fields(**raw_context)
        if context_mode != "all":
            if normalized["scope"] == "private" and normalized["group_id"]:
                return self._err("private search context cannot contain group_id", 400)
            if normalized["scope"] == "group" and normalized["user_id"]:
                return self._err("group search context cannot contain user_id", 400)
        has_session_context = (
            bool(normalized["session_id"])
            or (normalized["scope"] == "private" and bool(normalized["user_id"]))
            or (normalized["scope"] == "group" and bool(normalized["group_id"]))
        )
        if context_mode == "session" and not has_session_context:
            return self._err("missing valid private or group search context", 400)
        if context_mode == "auto" and has_context_intent and not has_session_context:
            return self._err("incomplete private or group search context", 400)
        admin_read_all = context_mode == "all" or (
            context_mode == "auto" and not has_context_intent
        )
        if admin_read_all:
            normalized = {
                "session_id": "",
                "scope": "unknown",
                "platform": "",
                "user_id": "",
                "group_id": "",
            }
            requested_bot_id = ""
        ctx = SessionContext(
            session_id=normalized["session_id"],
            scope=normalized["scope"],
            platform=normalized["platform"],
            user_id=normalized["user_id"],
            user_name=clean_text(payload.get("user_name"), 80),
            group_id=normalized["group_id"],
            group_name=clean_text(payload.get("group_name"), 80),
            bot_id=requested_bot_id,
            message_text=query,
        )
        top_k = max(1, min(50, self._int(payload.get("top_k"), 8)))
        slot_map: dict[str, list[Any]] = {}
        search_context_slots = getattr(
            self.plugin.service, "search_context_slots", None
        )
        if callable(search_context_slots):
            results, blocked, slot_map = await search_context_slots(
                query,
                ctx,
                top_k,
                admin_read_all=admin_read_all,
            )
        else:
            results, blocked = await self.plugin.service.search_with_diagnostics(
                query,
                ctx,
                top_k,
                admin_read_all=admin_read_all,
            )
            slot_map = {"stable_memory": results} if results else {}
        slot_limits: dict[str, int] = {}
        slot_limit_builder = getattr(self.plugin.service, "_slot_limits", None)
        if callable(slot_limit_builder):
            slot_limits = slot_limit_builder(top_k, query=query)
        capped_slots: set[str] = set()
        cap_builder = getattr(self.plugin.service, "_slot_capped_slots", None)
        if callable(cap_builder):
            capped_slots = set(cap_builder(query, bot_id=requested_bot_id) or set())
        orchestration_enabled = callable(search_context_slots)
        config = getattr(self.plugin.service, "config", None)
        config_bool = getattr(config, "bool", None)
        if callable(config_bool):
            orchestration_enabled = orchestration_enabled and bool(
                config_bool("context_orchestration.enabled", True)
            )
        return self._ok(
            {
                "results": [
                    serialize_memory(item.memory, item.score, item.reason)
                    for item in results
                ],
                "blocked": blocked[:30],
                "retrieval": {
                    "top_k": top_k,
                    "orchestration_enabled": orchestration_enabled,
                    "slot_counts": {
                        clean_text(slot, 60): len(items)
                        for slot, items in slot_map.items()
                        if isinstance(items, list) and items
                    },
                    "slot_limits": slot_limits if orchestration_enabled else {},
                    "capped_slots": sorted(capped_slots)
                    if orchestration_enabled
                    else [],
                },
                "search_context": {
                    "mode": "all" if admin_read_all else "session",
                    "scope": normalized["scope"],
                    "session_id": normalized["session_id"],
                    "user_id": normalized["user_id"],
                    "group_id": normalized["group_id"],
                    "bot_id": requested_bot_id,
                },
            }
        )

    async def timeline(self):
        try:
            rows = await self.plugin.service.store.recent_timeline(
                limit=self._query_int("limit", 30),
                scope=clean_text(request.args.get("scope", ""), 40),
                session_id=clean_text(request.args.get("session_id", ""), 200),
                entity_id=clean_text(request.args.get("entity_id", ""), 120),
            )
            return self._ok({"items": rows})
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] timeline 端点异常: %s", exc, exc_info=True
            )
            return self._err("timeline_unavailable", 500)

    async def relations(self):
        try:
            rows = await self.plugin.service.store.list_relationships(
                limit=self._query_int("limit", 50),
                entity_id=clean_text(request.args.get("entity_id", ""), 120),
                scope=clean_text(request.args.get("scope", ""), 40),
                session_id=clean_text(request.args.get("session_id", ""), 200),
                group_id=clean_text(request.args.get("group_id", ""), 120),
            )
            return self._ok({"items": rows})
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] relations 端点异常: %s", exc, exc_info=True
            )
            return self._err("relations_unavailable", 500)

    async def graph(self):
        try:
            rows = await self.plugin.service.store.list_knowledge_edges(
                limit=self._query_int("limit", 50),
                scope=clean_text(request.args.get("scope", ""), 40),
                session_id=clean_text(request.args.get("session_id", ""), 200),
                group_id=clean_text(request.args.get("group_id", ""), 120),
                node=clean_text(request.args.get("node") or request.args.get("q"), 160),
            )
            return self._ok({"items": rows})
        except Exception as exc:
            logger.warning("[MemoryCompanion] graph 端点异常: %s", exc, exc_info=True)
            return self._err("graph_unavailable", 500)

    async def threads(self):
        try:
            rows = await self.plugin.service.store.list_cross_window_threads(
                status=clean_text(request.args.get("status", "open"), 40) or "open",
                limit=self._query_int("limit", 30),
                session_id=clean_text(request.args.get("session_id", ""), 200),
            )
            return self._ok({"items": rows})
        except Exception as exc:
            logger.warning("[MemoryCompanion] threads 端点异常: %s", exc, exc_info=True)
            return self._err("threads_unavailable", 500)

    async def thread_status(self):
        payload = await self._json()
        thread_id = clean_text(payload.get("id"), 120)
        status = clean_text(payload.get("status"), 40).lower()
        if not thread_id:
            return self._err("missing thread id", 400)
        if status not in {"open", "closed"}:
            return self._err("thread status must be open or closed", 400)
        ok = await self.plugin.service.store.update_cross_window_thread_status(
            thread_id,
            status,
        )
        return self._ok({"updated": ok})

    async def logs(self):
        try:
            rows = await self.plugin.service.store.recent_injection_logs(
                limit=self._query_int("limit", 20),
                scope=clean_text(request.args.get("scope", ""), 40),
                session_id=clean_text(request.args.get("session_id", ""), 200),
            )
            all_ids: list[str] = []
            for row in rows:
                all_ids.extend(
                    clean_text(mid, 120)
                    for mid in (row.get("selected_memory_ids") or [])[:12]
                )
            records_map = (
                await self.plugin.service.store.get_memories_by_ids(all_ids)
                if all_ids
                else {}
            )
            for row in rows:
                selected = []
                for memory_id in (row.get("selected_memory_ids") or [])[:12]:
                    record = records_map.get(clean_text(memory_id, 120))
                    if record:
                        selected.append(serialize_memory(record))
                row["selected_memories"] = selected
            return self._ok({"items": rows})
        except Exception as exc:
            logger.warning("[MemoryCompanion] logs 端点异常: %s", exc, exc_info=True)
            return self._err("logs_unavailable", 500)
