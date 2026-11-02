from __future__ import annotations

import json
import shutil
from typing import Any

from .identity import parse_scope_from_session
from .models import (
    clean_text,
    json_dumps,
    stable_fingerprint,
)


class HistoricalChatImporterStart:
    """Start; shared runtime resources belong to the facade."""

    async def start_import(self, payload: dict[str, Any]) -> dict[str, Any]:
        upload_id = clean_text(payload.get("upload_id"), 120)
        manifest, messages = self._load_upload_messages(upload_id)
        source_metadata = (
            manifest.get("source_metadata")
            if isinstance(manifest.get("source_metadata"), dict)
            else {}
        )
        if clean_text(
            manifest.get("source_kind"), 40
        ) == "qq_chat_exporter" and clean_text(
            source_metadata.get("chat_type"), 40
        ).casefold() not in {"", "private"}:
            raise ValueError(
                "QQChatExporter 群聊记录暂不能按私聊记忆导入，请选择 private 类型的私聊导出文件"
            )
        raw_map = (
            payload.get("speaker_map")
            if isinstance(payload.get("speaker_map"), dict)
            else {}
        )
        speakers = list((manifest.get("stats") or {}).get("speakers") or {})
        speaker_map: dict[str, dict[str, Any]] = {}
        bot_speakers = 0
        user_speakers = 0
        for speaker in speakers:
            raw = raw_map.get(speaker) if isinstance(raw_map.get(speaker), dict) else {}
            role = clean_text(raw.get("role"), 20).lower()
            if role not in {"user", "bot"}:
                raise ValueError(f"请确认说话人“{speaker}”是用户还是 Bot")
            if role == "bot":
                bot_speakers += 1
            else:
                user_speakers += 1
            speaker_map[speaker] = {
                "role": role,
                "entity_id": clean_text(raw.get("entity_id"), 120),
                "display_name": clean_text(raw.get("display_name"), 80) or speaker,
            }
        if bot_speakers != 1 or user_speakers < 1:
            raise ValueError("私聊导入必须恰好确认一个 Bot，并至少确认一个用户")

        user_id = clean_text(payload.get("user_id"), 120)
        user_name = clean_text(payload.get("user_name"), 80)
        bot_id = clean_text(payload.get("bot_id"), 120)
        bot_name = clean_text(payload.get("bot_name"), 80) or "Bot"
        if not user_id:
            raise ValueError("请填写目标用户 ID")
        if not bot_id:
            raise ValueError("请填写目标 Bot ID，避免多 Bot 记忆串线")
        if user_id == bot_id:
            raise ValueError("目标用户 ID 和 Bot ID 不能相同")
        for speaker, mapping in speaker_map.items():
            if mapping["role"] == "bot":
                mapping["entity_id"] = bot_id
                mapping["display_name"] = mapping["display_name"] or bot_name
            else:
                mapping["entity_id"] = mapping["entity_id"] or user_id
                if mapping["entity_id"] == user_id and not user_name:
                    user_name = mapping["display_name"]

        platform = clean_text(payload.get("platform"), 40) or "qq"
        requested_session_id = clean_text(payload.get("session_id"), 200)
        session_id = requested_session_id
        if requested_session_id:
            parsed_scope, parsed_target = parse_scope_from_session(requested_session_id)
            if parsed_scope != "private" or clean_text(parsed_target, 120) != user_id:
                raise ValueError("目标用户 ID 与私聊会话不一致")
            session_platform = (
                clean_text(requested_session_id.split(":", 1)[0], 40)
                if ":" in requested_session_id
                else ""
            )
            if session_platform and session_platform.casefold() != platform.casefold():
                raise ValueError("目标平台与私聊会话不一致")
            if not await self.store.private_memory_context_exists(
                session_id=requested_session_id,
                user_id=user_id,
                bot_id=bot_id,
            ):
                raise ValueError("所选私聊上下文不存在，或用户、Bot 与会话不一致")
        else:
            session_id = await self.store.preferred_private_session_id(user_id, bot_id)
            if session_id and ":" in session_id:
                platform = clean_text(session_id.split(":", 1)[0], 40) or platform
        if not session_id:
            session_id = f"{platform}:FriendMessage:{user_id}"
        user_entity_ids = {
            clean_text(mapping.get("entity_id"), 120)
            for mapping in speaker_map.values()
            if mapping.get("role") == "user"
        }
        if user_entity_ids != {user_id}:
            raise ValueError("私聊导入中的所有用户说话人必须映射到同一个目标用户 ID")
        options = self._resolved_options(
            payload.get("options") if isinstance(payload.get("options"), dict) else None
        )
        identity_fingerprint = {
            speaker: {
                "role": mapping["role"],
                "entity_id": mapping["entity_id"],
            }
            for speaker, mapping in sorted(speaker_map.items())
        }
        fingerprint = stable_fingerprint(
            manifest.get("parse_hash") or manifest.get("source_hash"),
            session_id,
            bot_id,
            json.dumps(identity_fingerprint, ensure_ascii=False, sort_keys=True),
        )
        batch_id = "chatimp_" + fingerprint[:24]
        existing = await self.store.get_chat_import_batch(batch_id)
        if existing and existing.get("state") not in {"rolled_back", "failed"}:
            if existing.get("state") in {"paused", "prepared"} or (
                existing.get("state") in {"running", "reconciling", "indexing"}
                and not self._task_active(batch_id)
            ):
                await self.resume_batch(batch_id)
            return await self.status(batch_id)
        if existing and existing.get("state") == "failed":
            # 上一次可能已写入部分确定性记录。先整批清理再重建，避免复用旧批次
            # 时间线导致归属和回滚边界不完整。
            await self.rollback_batch(batch_id)

        backup = await self.store.backup_async(".before_chat_import")
        batch_dir = self.batch_root / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        upload_dir = self._upload_dir(upload_id)
        shutil.copy2(upload_dir / "source.txt", batch_dir / "source.txt")
        shutil.copy2(upload_dir / "parsed.jsonl", batch_dir / "parsed.jsonl")
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    **manifest,
                    "batch_id": batch_id,
                    "speaker_map": speaker_map,
                    "options": options,
                    "target": {
                        "session_id": session_id,
                        "platform": platform,
                        "user_id": user_id,
                        "user_name": user_name,
                        "bot_id": bot_id,
                        "bot_name": bot_name,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        timeline_rows: list[dict[str, Any]] = []
        for item in messages:
            mapping = speaker_map[clean_text(item.get("speaker"), 80)]
            is_bot = mapping["role"] == "bot"
            subject_id = bot_id if is_bot else mapping["entity_id"]
            object_id = user_id if is_bot else bot_id
            timeline_rows.append(
                {
                    "event_type": "bot_response" if is_bot else "user_message",
                    "session_id": session_id,
                    "scope": "private",
                    "subject_id": subject_id,
                    "object_id": object_id,
                    "content": item["content"],
                    "message_id": item["message_id"],
                    "occurred_at": item["occurred_at"],
                    "retention_class": "historical_archive",
                    "import_batch_id": batch_id,
                    "source_sequence": item["sequence"],
                    "metadata": {
                        "sender_name": mapping["display_name"],
                        "raw_speaker": item["speaker"],
                        "message_id": item["message_id"],
                        "source": "historical_chat_import",
                        "source_kind": clean_text(item.get("source_kind"), 40)
                        or clean_text(manifest.get("source_kind"), 40)
                        or "text_file",
                        "source_name": manifest.get("source_name"),
                        "source_line": item["source_line"],
                        "source_sequence": item["sequence"],
                        "source_message_id": clean_text(
                            item.get("source_message_id"), 120
                        ),
                        "source_message_seq": max(
                            0, int(item.get("source_message_seq") or 0)
                        ),
                        "source_platform_id": clean_text(
                            item.get("source_platform_id")
                            or source_metadata.get("platform_id"),
                            160,
                        ),
                        "source_sender_id": clean_text(
                            item.get("source_sender_id"), 120
                        ),
                        "original_local_time": item["local_time"],
                        "original_time_text": item["raw_time"],
                        "timezone": "Asia/Shanghai",
                        "year_inferred": bool(item["inferred_year"]),
                        "retention_class": "historical_archive",
                        "preserve_raw": True,
                        "import_batch_id": batch_id,
                    },
                }
            )

        try:
            (
                timeline_ids,
                newly_inserted_ids,
            ) = await self.store.add_historical_timeline_events_with_status(
                timeline_rows
            )
            if len(timeline_ids) != len(messages):
                raise RuntimeError(
                    f"时间线写入不完整: expected={len(messages)} actual={len(timeline_ids)}"
                )
            segmenter = self._segmenter_from_options(options)
            messages_to_summarize = [
                item
                for item in messages
                if clean_text(item.get("message_id"), 120) in newly_inserted_ids
            ]
            segments = segmenter.segments(messages_to_summarize, speaker_map)
            db_segments: list[dict[str, Any]] = []
            for item in segments:
                transcript_lines = []
                for line in str(item["transcript"]).splitlines():
                    row = json.loads(line)
                    row["message_ids"] = [
                        timeline_ids.get(message_id, message_id)
                        for message_id in row.get("message_ids") or []
                    ]
                    transcript_lines.append(json_dumps(row))
                db_segments.append(
                    {
                        **item,
                        "id": f"{batch_id}_seg_{int(item['segment_index']):04d}",
                        "message_ids": [
                            timeline_ids.get(message_id, message_id)
                            for message_id in item["message_ids"]
                        ],
                        "transcript": "\n".join(transcript_lines),
                        "status": "pending",
                    }
                )
            stats = dict(manifest.get("stats") or {})
            stats.update(
                {
                    "timeline_count": len(timeline_ids),
                    "new_timeline_count": len(newly_inserted_ids),
                    "reused_timeline_count": len(timeline_ids)
                    - len(newly_inserted_ids),
                    "summarized_source_message_count": len(messages_to_summarize),
                    "segment_count": len(db_segments),
                }
            )
            await self.store.upsert_chat_import_batch(
                {
                    "id": batch_id,
                    "upload_id": upload_id,
                    "source_name": manifest.get("source_name"),
                    "source_hash": manifest.get("source_hash"),
                    "state": "running",
                    "session_id": session_id,
                    "scope": "private",
                    "platform": platform,
                    "user_id": user_id,
                    "user_name": user_name,
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "speaker_map": speaker_map,
                    "options": options,
                    "stats": stats,
                    "total_segments": len(db_segments),
                    "backup_path": str(backup),
                }
            )
            await self._register_batch_identities(
                {
                    "session_id": session_id,
                    "platform": platform,
                    "user_id": user_id,
                    "user_name": user_name,
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "speaker_map": speaker_map,
                }
            )
            await self.store.replace_chat_import_segments(batch_id, db_segments)
        except Exception:
            await self.store.rollback_chat_import_batch(batch_id)
            raise
        self._start_worker(batch_id)
        return await self.status(batch_id)
