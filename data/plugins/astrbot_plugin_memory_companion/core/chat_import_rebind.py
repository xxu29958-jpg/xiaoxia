from __future__ import annotations

from typing import Any

from .astrbot_compat import logger
from .identity import parse_scope_from_session
from .models import (
    clean_text,
)


class HistoricalChatImporterRebind:
    """Rebind; shared runtime resources belong to the facade."""

    async def _rebind_relationship_observations(
        self,
        *,
        batch_id: str,
        old_user_id: str,
        user_id: str,
        user_name: str,
        observation_count: int,
    ) -> dict[str, Any]:
        api = self._private_api()
        rebind = (
            getattr(api, "rebind_historical_relationship_observations", None)
            if api is not None
            else None
        )
        if not callable(rebind):
            if observation_count <= 0:
                return {"status": "not_needed", "moved": 0}
            return {
                "status": "unsupported",
                "moved": 0,
                "message": "陪伴插件版本暂不支持同步修正关系候选，请在关系网页人工核对",
            }
        try:
            result = rebind(
                batch_id=batch_id,
                old_user_id=old_user_id,
                user_id=user_id,
                user_name=user_name,
            )
            if hasattr(result, "__await__"):
                result = await result
            details = result if isinstance(result, dict) else {}
            reason = clean_text(details.get("reason"), 80)

            def result_count(key: str) -> int:
                try:
                    return max(0, int(details.get(key) or 0))
                except (TypeError, ValueError):
                    return 0

            untraceable_confirmed = result_count("untraceable_confirmed")
            warning_parts: list[str] = []
            if untraceable_confirmed:
                warning_parts.append(
                    f"发现 {untraceable_confirmed} 条旧版已确认关系记忆缺少批次标识，"
                    "无法安全自动移动，请在陪伴插件关系页人工核对"
                )
            pending_unmoved = max(
                0,
                result_count("matched")
                - result_count("moved")
                - result_count("deduplicated"),
            )
            confirmed_unmoved = max(
                0,
                result_count("confirmed_matched")
                - result_count("confirmed_moved")
                - result_count("confirmed_deduplicated"),
            )
            if pending_unmoved or confirmed_unmoved:
                warning_parts.append(
                    f"目标关系资料容量不足，仍有 {pending_unmoved + confirmed_unmoved} 条记录保留在原用户下"
                )
            if observation_count > 0 and reason and not warning_parts:
                warning_parts.append(
                    "旧批次记录过关系候选，但当前未找到可按批次自动移动的项目，请人工核对"
                )
            warning = "；".join(warning_parts)
            return {
                **details,
                "status": "completed_with_warnings" if warning else "completed",
                **({"message": warning} if warning else {}),
            }
        except Exception as exc:
            message = clean_text(exc, 180)
            logger.warning(
                "[MemoryCompanion] 导入批次关系候选归属同步失败: batch=%s error=%s",
                batch_id,
                exc,
            )
            return {
                "status": "failed",
                "moved": 0,
                "message": f"关系候选未能同步修正: {message}",
            }

    async def rebind_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = clean_text(payload.get("batch_id"), 120)
        user_id = clean_text(payload.get("user_id"), 120)
        session_id = clean_text(payload.get("session_id"), 200)
        bot_id = clean_text(payload.get("bot_id"), 120)
        platform = clean_text(payload.get("platform"), 40)
        user_name = clean_text(payload.get("user_name"), 120)
        bot_name = clean_text(payload.get("bot_name"), 120)
        if not batch_id:
            raise ValueError("batch_id is required")
        if not user_id or not session_id or not bot_id:
            raise ValueError("user_id、session_id 和 bot_id 均不能为空")
        if user_id == bot_id:
            raise ValueError("目标用户 ID 和 Bot ID 不能相同")
        parsed_scope, parsed_target = parse_scope_from_session(session_id)
        if parsed_scope != "private":
            raise ValueError("目标会话必须是有效的私聊会话")
        if clean_text(parsed_target, 120) != user_id:
            raise ValueError("目标用户 ID 与私聊会话不一致")
        session_platform = (
            clean_text(session_id.split(":", 1)[0], 40) if ":" in session_id else ""
        )
        platform = platform or session_platform
        if not platform:
            raise ValueError("无法从目标会话识别平台")
        if session_platform and session_platform.casefold() != platform.casefold():
            raise ValueError("目标平台与私聊会话不一致")

        batch = await self.store.get_chat_import_batch(batch_id)
        if not batch:
            raise ValueError("导入批次不存在")
        state = clean_text(batch.get("state"), 40)
        if state not in {"completed", "completed_with_warnings"}:
            raise ValueError("仅已完成的导入批次可以修正归属")
        if clean_text(batch.get("scope"), 40) != "private":
            raise ValueError("仅私聊历史导入支持修正归属")
        if self._task_active(batch_id):
            raise ValueError("导入任务仍在运行，请等待完成后重试")
        if (
            clean_text(batch.get("session_id"), 200),
            clean_text(batch.get("user_id"), 120),
            clean_text(batch.get("bot_id"), 120),
        ) == (session_id, user_id, bot_id):
            raise ValueError("当前批次已经属于所选私聊，无需重复修正")
        if not await self.store.chat_import_rebind_target_exists(
            batch_id=batch_id,
            session_id=session_id,
            user_id=user_id,
            bot_id=bot_id,
        ):
            raise ValueError("目标私聊上下文不存在，或用户、Bot 与所选会话不一致")

        old_user_id = clean_text(batch.get("user_id"), 120)
        if not old_user_id:
            speaker_map = (
                batch.get("speaker_map")
                if isinstance(batch.get("speaker_map"), dict)
                else {}
            )
            for mapping in speaker_map.values():
                if (
                    not isinstance(mapping, dict)
                    or clean_text(mapping.get("role"), 20) != "user"
                ):
                    continue
                old_user_id = clean_text(
                    mapping.get("source_entity_id") or mapping.get("entity_id"), 120
                )
                if old_user_id:
                    break
        if not old_user_id:
            for record in await self.store.list_chat_import_memories(batch_id):
                for entity in (record.subject, record.object):
                    if clean_text(entity.kind, 40) == "user" and clean_text(
                        entity.id, 120
                    ):
                        old_user_id = clean_text(entity.id, 120)
                        break
                if old_user_id:
                    break

        backup = await self.store.backup_async(".before_chat_import_rebind")
        rebound = await self.store.rebind_chat_import_batch(
            batch_id=batch_id,
            session_id=session_id,
            platform=platform,
            user_id=user_id,
            user_name=user_name,
            bot_id=bot_id,
            bot_name=bot_name,
            backup_path=str(backup),
        )
        refreshed = await self.store.get_chat_import_batch(batch_id)
        if not refreshed:
            raise RuntimeError("修正归属后无法读取导入批次")
        relationship_observations = await self._rebind_relationship_observations(
            batch_id=batch_id,
            old_user_id=old_user_id,
            user_id=user_id,
            user_name=clean_text(refreshed.get("user_name"), 120)
            or user_name
            or user_id,
            observation_count=int(refreshed.get("relationship_observation_count") or 0),
        )
        stats = dict(refreshed.get("stats") or {})
        stats["relationship_observation_rebind"] = relationship_observations
        refreshed = (
            await self.store.update_chat_import_batch(batch_id, stats=stats)
            or refreshed
        )
        try:
            await self._register_batch_identities(refreshed)
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 导入批次归属已修正，但身份索引登记失败: batch=%s error=%s",
                batch_id,
                exc,
            )
        public_batch = self._public_batch(refreshed)
        return {
            "batch_id": batch_id,
            "state": clean_text(refreshed.get("state"), 40),
            "batch": public_batch,
            "target": {
                "scope": "private",
                "session_id": session_id,
                "platform": platform,
                "user_id": user_id,
                "user_name": clean_text(refreshed.get("user_name"), 120),
                "bot_id": bot_id,
                "bot_name": clean_text(refreshed.get("bot_name"), 120),
            },
            "rebind": rebound,
            "relationship_observations": relationship_observations,
            "backup_path": str(backup),
        }
