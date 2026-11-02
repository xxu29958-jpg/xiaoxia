from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .chat_import_support import (
    LOCAL_TZ,
)
from .models import (
    clean_text,
    stable_fingerprint,
)


class HistoricalChatImporterQqExport:
    """Qq export; shared runtime resources belong to the facade."""

    @staticmethod
    def _qq_chat_exporter_placeholder(message_type: str) -> str:
        labels = {
            "image": "[图片]",
            "pic": "[图片]",
            "audio": "[语音]",
            "record": "[语音]",
            "video": "[视频]",
            "file": "[文件]",
            "reply": "[回复消息]",
            "forward": "[转发消息]",
            "json": "[卡片消息]",
            "xml": "[卡片消息]",
            "face": "[表情]",
            "emoji": "[表情]",
            "mface": "[动画表情]",
            "location": "[位置]",
            "music": "[音乐]",
            "dice": "[骰子]",
            "rps": "[猜拳]",
            "poke": "[戳一戳]",
        }
        return labels.get(message_type.casefold(), "[消息]")

    @classmethod
    def _qq_chat_exporter_content(cls, raw: dict[str, Any]) -> str:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        text = (
            str(content.get("text") or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .strip()
        )
        if text:
            return text
        parts: list[str] = []
        for element in (
            content.get("elements") if isinstance(content.get("elements"), list) else []
        ):
            if not isinstance(element, dict):
                continue
            element_type = clean_text(element.get("type"), 40).casefold()
            data = element.get("data") if isinstance(element.get("data"), dict) else {}
            if element_type == "text":
                value = (
                    str(data.get("text") or "")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                )
                if value:
                    parts.append(value)
            else:
                parts.append(cls._qq_chat_exporter_placeholder(element_type))
        text = "".join(parts).strip()
        return text or cls._qq_chat_exporter_placeholder(
            clean_text(raw.get("type"), 40)
        )

    @staticmethod
    def _qq_chat_exporter_time(raw: dict[str, Any]) -> tuple[datetime, str] | None:
        raw_timestamp = raw.get("timestamp")
        try:
            timestamp = float(raw_timestamp)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
            if timestamp > 0:
                moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                return moment.astimezone(LOCAL_TZ), clean_text(
                    raw.get("time"), 80
                ) or str(raw_timestamp)
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        raw_time = clean_text(raw.get("time"), 80)
        if not raw_time:
            return None
        try:
            moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=LOCAL_TZ)
        return moment.astimezone(LOCAL_TZ), raw_time

    @staticmethod
    def _qq_chat_exporter_speaker(sender: dict[str, Any]) -> tuple[str, str]:
        sender_id = clean_text(sender.get("uin") or sender.get("uid"), 120)
        name = (
            clean_text(sender.get("name") or sender.get("nickname"), 60)
            or sender_id
            or "未知说话人"
        )
        return (f"{name} [{sender_id}]" if sender_id else name), sender_id

    def _stage_qq_chat_exporter_json(
        self,
        *,
        source_name: str,
        source_text: str,
        source_encoding: str,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(source_text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "JSON 解析失败：请确认文件完整，并使用 QQChatExporter 导出的聊天记录 JSON"
            ) from exc
        metadata = (
            payload.get("metadata")
            if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict)
            else {}
        )
        raw_messages = payload.get("messages") if isinstance(payload, dict) else None
        if clean_text(metadata.get("name"), 80) != "QQChatExporter" or not isinstance(
            raw_messages, list
        ):
            raise ValueError(
                "该 JSON 不是 QQChatExporter 聊天导出文件；请导入 QQChatExporter 的聊天记录 JSON，"
                "其他记忆备份请使用对应的可移植档案入口"
            )

        chat_info = (
            payload.get("chatInfo") if isinstance(payload.get("chatInfo"), dict) else {}
        )
        self_uin = clean_text(chat_info.get("selfUin"), 120)
        self_uid = clean_text(chat_info.get("selfUid"), 120)
        self_name = clean_text(chat_info.get("selfName"), 80) or self_uin
        peer_uin = clean_text(chat_info.get("peerUin"), 120)
        peer_uid = clean_text(chat_info.get("peerUid"), 120)
        peer_name = clean_text(chat_info.get("name"), 80) or peer_uin
        self_ids = {value for value in (self_uin, self_uid) if value}
        peer_ids = {value for value in (peer_uin, peer_uid) if value}
        source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        conversation_identity = stable_fingerprint(
            "qq_chat_exporter",
            clean_text(chat_info.get("type"), 40).casefold(),
            self_uin or self_uid,
            peer_uin or peer_uid,
            clean_text(chat_info.get("name"), 80)
            if not (self_ids and peer_ids)
            else "",
        )
        messages: list[dict[str, Any]] = []
        skipped_recalled = 0
        skipped_system = 0
        skipped_invalid_time = 0
        skipped_duplicate = 0
        seen_source_message_ids: set[str] = set()
        sender_ids: dict[str, str] = {}

        for source_line, raw in enumerate(raw_messages, start=1):
            if not isinstance(raw, dict):
                continue
            if raw.get("recalled") is True:
                skipped_recalled += 1
                continue
            if raw.get("system") is True:
                skipped_system += 1
                continue
            parsed_time = self._qq_chat_exporter_time(raw)
            if parsed_time is None:
                skipped_invalid_time += 1
                continue
            local_time, raw_time = parsed_time
            sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
            speaker, source_sender_id = self._qq_chat_exporter_speaker(sender)
            sender_id = source_sender_id
            if source_sender_id in self_ids:
                sender_id = self_uin or self_uid or source_sender_id
                speaker = f"{self_name or sender_id} [{sender_id}]"
            elif source_sender_id in peer_ids:
                sender_id = peer_uin or peer_uid or source_sender_id
                speaker = f"{peer_name or sender_id} [{sender_id}]"
            sender_ids[speaker] = sender_id
            try:
                sequence = int(raw.get("seq") or source_line)
            except (TypeError, ValueError):
                sequence = source_line
            sequence = max(1, sequence)
            source_message_id = clean_text(raw.get("id"), 120)
            if source_message_id:
                dedupe_key = stable_fingerprint(
                    conversation_identity, source_message_id
                )
                if dedupe_key in seen_source_message_ids:
                    skipped_duplicate += 1
                    continue
                seen_source_message_ids.add(dedupe_key)
            content = self._qq_chat_exporter_content(raw)
            source_evidence = source_message_id or stable_fingerprint(
                sequence,
                sender_id,
                local_time.isoformat(),
                content,
            )
            message_id = (
                "qqexport_"
                + stable_fingerprint(
                    conversation_identity,
                    source_evidence,
                )[:32]
            )
            messages.append(
                {
                    "sequence": sequence,
                    "speaker": speaker,
                    "raw_time": raw_time,
                    "local_time": local_time.isoformat(timespec="seconds"),
                    "occurred_at": local_time.astimezone(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "inferred_year": False,
                    "source_line": source_line,
                    "content": content,
                    "message_id": message_id,
                    "source_message_id": source_message_id,
                    "source_message_seq": sequence,
                    "source_sender_id": source_sender_id,
                    "source_kind": "qq_chat_exporter",
                }
            )
        if not messages:
            raise ValueError(
                "QQChatExporter 文件中没有可导入的非系统消息；请检查导出内容和消息时间"
            )

        messages.sort(
            key=lambda item: (item["local_time"], item["sequence"], item["source_line"])
        )
        speakers: dict[str, int] = {}
        duplicate_times: dict[str, int] = {}
        for item in messages:
            speaker = item["speaker"]
            speakers[speaker] = speakers.get(speaker, 0) + 1
            local_time = item["local_time"]
            duplicate_times[local_time] = duplicate_times.get(local_time, 0) + 1

        identity = self._identity_context(list(speakers))
        identity = dict(identity) if isinstance(identity, dict) else {}
        bot_identity = dict(identity.get("bot") or {})
        if self_uin:
            bot_identity["self_ids"] = [self_uin]
        if self_name:
            bot_identity["name"] = self_name
        aliases = [
            clean_text(item, 80)
            for item in (bot_identity.get("aliases") or [])
            if clean_text(item, 80)
        ]
        if self_name and self_name not in aliases:
            aliases.append(self_name)
        bot_identity["aliases"] = aliases
        identity["bot"] = bot_identity
        if peer_uin:
            identity["target_users"] = [
                {"user_id": peer_uin, "name": peer_name or peer_uin}
            ]
        identity["available"] = bool(identity.get("available") or self_uin or peer_uin)

        suggestions_by_speaker = {
            item["speaker"]: item
            for item in self._suggest_speaker_roles(messages, identity)
        }
        suggestions: list[dict[str, Any]] = []
        for speaker, count in speakers.items():
            suggestion = dict(suggestions_by_speaker.get(speaker) or {})
            sender_id = sender_ids.get(speaker, "")
            suggestion["speaker"] = speaker
            suggestion["message_count"] = count
            if sender_id and sender_id in self_ids:
                suggestion.update(
                    {
                        "suggested_role": "bot",
                        "confidence": "high",
                        "reasons": [
                            "QQChatExporter 将此账号标记为当前导出账号（self）"
                        ],
                    }
                )
            elif sender_id and sender_id in peer_ids:
                suggestion.update(
                    {
                        "suggested_role": "user",
                        "confidence": "high",
                        "reasons": ["QQChatExporter 将此账号标记为私聊对象（peer）"],
                        "relationship_candidates": (
                            [{"user_id": peer_uin, "name": peer_name or peer_uin}]
                            if peer_uin
                            else []
                        ),
                    }
                )
            suggestions.append(suggestion)

        source_metadata = {
            "exporter": {
                "name": clean_text(metadata.get("name"), 80),
                "version": clean_text(metadata.get("version"), 80),
                "copyright": clean_text(metadata.get("copyright"), 240),
            },
            "chat_type": clean_text(chat_info.get("type"), 40),
            "chat_name": clean_text(chat_info.get("name"), 80),
            "self_uin": self_uin,
            "self_uid": self_uid,
            "self_name": self_name,
            "peer_uin": peer_uin,
            "peer_uid": peer_uid,
            "peer_name": peer_name,
        }
        stats = {
            "source_format": "qq_chat_exporter_json",
            "message_count": len(messages),
            "speaker_count": len(speakers),
            "speakers": speakers,
            "first_at": messages[0]["local_time"],
            "last_at": messages[-1]["local_time"],
            "dialogue_chars": sum(len(str(item["content"])) for item in messages),
            "duplicate_timestamp_groups": sum(
                1 for count in duplicate_times.values() if count > 1
            ),
            "skipped_recalled_count": skipped_recalled,
            "skipped_system_count": skipped_system,
            "skipped_invalid_time_count": skipped_invalid_time,
            "skipped_duplicate_count": skipped_duplicate,
        }
        warnings: list[str] = []
        if skipped_recalled:
            warnings.append(f"已跳过 {skipped_recalled} 条撤回消息")
        if skipped_system:
            warnings.append(f"已跳过 {skipped_system} 条系统消息")
        if skipped_invalid_time:
            warnings.append(f"已跳过 {skipped_invalid_time} 条缺少有效时间的消息")
        if skipped_duplicate:
            warnings.append(f"已按原始消息 ID 去重 {skipped_duplicate} 条重复消息")
        if clean_text(chat_info.get("type"), 40).casefold() not in {"", "private"}:
            warnings.append("该导出记录不是私聊；当前不能开始单用户私聊记忆导入")
        return self.stage_structured_messages(
            source_name=source_name,
            source_hash=source_hash,
            messages=messages,
            stats=stats,
            source_kind="qq_chat_exporter",
            source_metadata=source_metadata,
            identity_context=identity,
            speaker_suggestions=suggestions,
            warnings=warnings,
            source_text=source_text,
            source_encoding=source_encoding,
        )
