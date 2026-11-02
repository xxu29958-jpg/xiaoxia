from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .astrbot_compat import logger
from .models import (
    clean_text,
    json_dumps,
    stable_fingerprint,
    utc_now,
)


class HistoricalChatImporterStaging:
    """Staging; shared runtime resources belong to the facade."""

    def _upload_dir(self, upload_id: str) -> Path:
        normalized = clean_text(upload_id, 120)
        if not re.fullmatch(r"chatup_[0-9a-f]{24}", normalized):
            raise ValueError("上传预览 ID 无效")
        return self.upload_root / normalized

    def _cleanup_staged_uploads(self, *, keep: str = "", max_age_days: int = 7) -> None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(1, max_age_days))
        for child in self.upload_root.iterdir():
            if not child.is_dir() or child.name == keep:
                continue
            manifest_path = child / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(
                    str(manifest.get("created_at") or "").replace("Z", "+00:00")
                )
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created.astimezone(timezone.utc) >= cutoff:
                    continue
                shutil.rmtree(child)
            except Exception:
                # 无法确认创建时间的目录不自动删除，避免误清理用户档案。
                continue

    def clear_archives(self) -> dict[str, int]:
        files = 0
        directories = 0
        if self.root.is_dir():
            for path in self.root.rglob("*"):
                if path.is_file():
                    files += 1
                elif path.is_dir():
                    directories += 1
            shutil.rmtree(self.root)
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.batch_root.mkdir(parents=True, exist_ok=True)
        self._tasks.clear()
        return {"files": files, "directories": directories}

    async def clear_relationship_observations(self) -> int:
        api = self._private_api()
        rollback = (
            getattr(api, "rollback_historical_relationship_observations", None)
            if api is not None
            else None
        )
        if not callable(rollback):
            return 0
        removed = 0
        for batch in await self.store.list_chat_import_batches(None):
            batch_id = clean_text(batch.get("id"), 120)
            if not batch_id:
                continue
            try:
                result = rollback(batch_id)
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, dict):
                    removed += int(result.get("removed") or 0)
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 清理历史关系观察失败: batch=%s error=%s",
                    batch_id,
                    exc,
                )
        return removed

    @staticmethod
    def _decode_upload(content: bytes) -> tuple[str, str]:
        if not content:
            raise ValueError("上传文件为空")
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ValueError("无法识别文本编码；请保存为 UTF-8 后重试")

    @staticmethod
    def _structured_source_text(messages: list[dict[str, Any]]) -> str:
        blocks = []
        for item in messages:
            speaker = clean_text(item.get("speaker"), 80) or "未知说话人"
            local_time = clean_text(item.get("local_time"), 80)
            content = str(item.get("content") or "").strip()
            blocks.append(f"{speaker}: {local_time}\n{content}")
        return "\n\n".join(blocks).strip() + "\n"

    def _stage_parsed_messages(
        self,
        *,
        source_name: str,
        source_hash: str,
        source_text: str,
        source_encoding: str,
        base_year: int,
        messages: list[dict[str, Any]],
        stats: dict[str, Any],
        source_kind: str,
        source_metadata: dict[str, Any] | None = None,
        identity_context: dict[str, Any] | None = None,
        speaker_suggestions: list[dict[str, Any]] | None = None,
        read_stats: dict[str, Any] | None = None,
        truncated: bool = False,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        parse_hash = stable_fingerprint(
            source_hash,
            [item.get("message_id") for item in messages],
        )
        upload_id = "chatup_" + parse_hash[:24]
        speakers = list((stats.get("speakers") or {}).keys())
        identity = (
            identity_context
            if isinstance(identity_context, dict)
            else self._identity_context(speakers)
        )
        suggestions = (
            speaker_suggestions
            if isinstance(speaker_suggestions, list)
            else self._suggest_speaker_roles(messages, identity)
        )
        provisional_map = {
            item["speaker"]: {"role": item["suggested_role"]}
            for item in suggestions
            if isinstance(item, dict) and clean_text(item.get("speaker"), 80)
        }
        options = self._resolved_options()
        segmenter = self._segmenter_from_options(options)
        segments = segmenter.segments(messages, provisional_map)
        resolved_stats = dict(stats)
        resolved_stats.update(
            {
                "logical_turn_count": len(
                    segmenter.logical_turns(messages, provisional_map)
                ),
                "candidate_segment_count": len(segments),
                "estimated_summary_calls": max(
                    1,
                    math.ceil(
                        sum(len(item["transcript"]) for item in segments)
                        / options["package_chars"]
                    ),
                ),
                "estimated_reconcile_calls": max(
                    1, math.ceil(max(1, len(segments)) / 16)
                ),
            }
        )
        if isinstance(read_stats, dict) and read_stats:
            resolved_stats["source_read"] = dict(read_stats)
            resolved_stats["source_truncated"] = bool(truncated)
        self._cleanup_staged_uploads(keep=upload_id)
        upload_dir = self._upload_dir(upload_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        normalized_source = (
            str(source_text or "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .lstrip("\ufeff")
        )
        (upload_dir / "source.txt").write_text(
            normalized_source, encoding="utf-8", newline="\n"
        )
        with (upload_dir / "parsed.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            for item in messages:
                handle.write(json_dumps(item) + "\n")
        manifest = {
            "upload_id": upload_id,
            "source_name": clean_text(source_name, 240) or "历史对话",
            "source_hash": source_hash,
            "parse_hash": parse_hash,
            "source_encoding": source_encoding,
            "normalized_encoding": "utf-8",
            "source_kind": clean_text(source_kind, 40) or "text_file",
            "source_metadata": source_metadata
            if isinstance(source_metadata, dict)
            else {},
            "base_year": int(base_year or 0),
            "options": options,
            "stats": resolved_stats,
            "speaker_suggestions": suggestions,
            "identity_context": identity,
            "segment_preview": [self._segment_preview(item) for item in segments[:12]],
            "read_stats": read_stats if isinstance(read_stats, dict) else {},
            "truncated": bool(truncated),
            "warnings": [
                clean_text(item, 240)
                for item in (warnings or [])
                if clean_text(item, 240)
            ],
            "created_at": utc_now(),
        }
        (upload_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest
