from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    clean_text,
)


class HistoricalChatImporterUpload:
    """Upload; shared runtime resources belong to the facade."""

    def stage_upload(
        self, *, filename: str, content: bytes, base_year: int = 0
    ) -> dict[str, Any]:
        if len(content) > self.MAX_UPLOAD_BYTES:
            raise ValueError("对话文件不能超过 8 MiB")
        base_year = int(base_year or 0)
        if base_year and not 1970 <= base_year <= 2200:
            raise ValueError("缺失年份起点必须在 1970 到 2200 之间")
        safe_name = Path(str(filename or "conversation.txt")).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".txt", ".log", ".md", ".json"}:
            raise ValueError("仅支持 TXT、LOG、Markdown 或 QQChatExporter JSON 文件")
        text, source_encoding = self._decode_upload(content)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
        if suffix == ".json":
            return self._stage_qq_chat_exporter_json(
                source_name=safe_name,
                source_text=normalized,
                source_encoding=source_encoding,
            )
        source_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        parsed = self.parser.parse(
            normalized, source_hash=source_hash, base_year=base_year
        )
        parse_stats = (
            parsed.get("stats") if isinstance(parsed.get("stats"), dict) else {}
        )
        warnings: list[str] = []
        if parse_stats.get("source_format") == "labeled_fields":
            warnings.append(
                "已识别字段式导出，系统已将“时间 / 内容”等结构字段排除出说话人列表"
            )
        speaker_count = int(parse_stats.get("speaker_count") or 0)
        if speaker_count > 12:
            warnings.append(
                f"当前识别到 {speaker_count} 个说话人；如果这是私聊记录，请先检查导出格式和发送者字段"
            )
        return self._stage_parsed_messages(
            source_name=safe_name,
            source_hash=source_hash,
            source_text=normalized,
            source_encoding=source_encoding,
            base_year=base_year,
            messages=parsed["messages"],
            stats=parsed["stats"],
            source_kind="text_file",
            warnings=warnings,
        )

    def stage_structured_messages(
        self,
        *,
        source_name: str,
        source_hash: str,
        messages: list[dict[str, Any]],
        stats: dict[str, Any],
        source_kind: str,
        source_metadata: dict[str, Any] | None = None,
        identity_context: dict[str, Any] | None = None,
        speaker_suggestions: list[dict[str, Any]] | None = None,
        read_stats: dict[str, Any] | None = None,
        truncated: bool = False,
        warnings: list[str] | None = None,
        source_text: str | None = None,
        source_encoding: str = "onebot-structured",
    ) -> dict[str, Any]:
        if not messages:
            raise ValueError("没有可暂存的历史消息")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(messages, start=1):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            speaker = clean_text(item.get("speaker"), 80)
            content = str(item.get("content") or "").strip()
            message_id = clean_text(item.get("message_id"), 120)
            local_time = clean_text(item.get("local_time"), 80)
            occurred_at = clean_text(item.get("occurred_at"), 80)
            if (
                not speaker
                or not content
                or not message_id
                or not local_time
                or not occurred_at
            ):
                raise ValueError(
                    f"第 {index} 条结构化消息缺少说话人、时间、正文或消息 ID"
                )
            item["sequence"] = max(1, int(item.get("sequence") or index))
            item["speaker"] = speaker
            item["content"] = content[:15999].rstrip() + (
                "…" if len(content) > 15999 else ""
            )
            item["message_id"] = message_id
            item["local_time"] = local_time
            item["occurred_at"] = occurred_at
            item["raw_time"] = clean_text(item.get("raw_time"), 80) or local_time
            item["source_line"] = max(0, int(item.get("source_line") or index))
            item["inferred_year"] = bool(item.get("inferred_year"))
            normalized.append(item)
        normalized.sort(key=lambda item: (item["local_time"], item["sequence"]))
        if not normalized:
            raise ValueError("没有可暂存的历史消息")
        return self._stage_parsed_messages(
            source_name=source_name,
            source_hash=clean_text(source_hash, 120),
            source_text=source_text
            if isinstance(source_text, str)
            else self._structured_source_text(normalized),
            source_encoding=clean_text(source_encoding, 80) or "onebot-structured",
            base_year=0,
            messages=normalized,
            stats=stats,
            source_kind=source_kind,
            source_metadata=source_metadata,
            identity_context=identity_context,
            speaker_suggestions=speaker_suggestions,
            read_stats=read_stats,
            truncated=truncated,
            warnings=warnings,
        )

    @staticmethod
    def _segment_preview(item: dict[str, Any]) -> dict[str, Any]:
        first_line = ""
        for line in str(item.get("transcript") or "").splitlines():
            try:
                first_line = clean_text(json.loads(line).get("text"), 120)
            except Exception:
                first_line = clean_text(line, 120)
            if first_line:
                break
        return {
            "segment_index": item.get("segment_index"),
            "start_local": item.get("start_local"),
            "end_local": item.get("end_local"),
            "turn_count": item.get("turn_count"),
            "char_count": item.get("char_count"),
            "preview": first_line,
        }

    def preview_upload(self, upload_id: str) -> dict[str, Any]:
        manifest_path = self._upload_dir(upload_id) / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("上传预览不存在或已被清理")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(self.STAGED_DATA_ERROR) from exc
        if not isinstance(manifest, dict):
            raise ValueError(self.STAGED_DATA_ERROR)
        if clean_text(manifest.get("upload_id"), 120) != clean_text(upload_id, 120):
            raise ValueError(self.STAGED_DATA_ERROR)
        return manifest

    def _load_upload_messages(
        self, upload_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        manifest = self.preview_upload(upload_id)
        parsed_path = self._upload_dir(upload_id) / "parsed.jsonl"
        messages: list[dict[str, Any]] = []
        try:
            for line_no, raw in enumerate(
                parsed_path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not raw.strip():
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError(f"line={line_no}")
                messages.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(self.STAGED_DATA_ERROR) from exc
        if not messages:
            raise ValueError(self.STAGED_DATA_ERROR)
        return manifest, messages
