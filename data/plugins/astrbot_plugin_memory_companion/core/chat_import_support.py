from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import (
    clean_text,
    json_dumps,
    stable_fingerprint,
)

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


FULL_HEADER = re.compile(
    r"^(?P<speaker>[^\r\n:：]{1,80})\s*[:：]\s*"
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})\s*$"
)


SHORT_HEADER = re.compile(
    r"^(?P<speaker>[^\r\n:：]{1,80})\s*[:：]\s*"
    r"(?P<month>\d{2})-(?P<day>\d{2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})\s*$"
)


HEADER_LIKE = re.compile(r"^.{1,80}[:：]\s*\d")


FIELD_LINE = re.compile(r"^(?P<label>[^\r\n:：]{1,32})\s*[:：]\s*(?P<value>.*)$")


FIELD_TIME_LABELS = frozenset(
    {
        "时间",
        "消息时间",
        "发送时间",
        "日期",
        "datetime",
        "timestamp",
        "time",
    }
)


FIELD_SPEAKER_LABELS = frozenset(
    {
        "发送者",
        "发送人",
        "发言人",
        "说话人",
        "发送方",
        "用户",
        "用户昵称",
        "发送者昵称",
        "qq昵称",
        "昵称",
        "姓名",
        "名称",
        "角色",
        "sender",
        "speaker",
        "nickname",
        "name",
        "role",
        "from",
    }
)


FIELD_CONTENT_LABELS = frozenset(
    {
        "内容",
        "消息",
        "消息内容",
        "正文",
        "文本",
        "content",
        "message",
        "text",
    }
)


FIELD_METADATA_LABELS = frozenset(
    {
        "消息id",
        "id",
        "qq",
        "qq号",
        "账号",
        "uin",
        "消息类型",
        "类型",
        "来源",
        "平台",
        "群号",
        "会话",
        "消息方向",
        "方向",
        "是否发送",
        "senderid",
        "sender_id",
        "messageid",
        "message_id",
    }
)


OPENING_MARKERS = ("早安", "早上好", "在吗", "醒了吗", "新年快乐")


CLOSING_MARKERS = ("晚安", "睡吧", "先这样", "回聊", "不聊了", "下次聊", "拜拜", "再见")


@dataclass(slots=True)
class ParsedChatMessage:
    sequence: int
    speaker: str
    raw_time: str
    local_time: str
    occurred_at: str
    inferred_year: bool
    source_line: int
    content: str
    message_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "speaker": self.speaker,
            "raw_time": self.raw_time,
            "local_time": self.local_time,
            "occurred_at": self.occurred_at,
            "inferred_year": self.inferred_year,
            "source_line": self.source_line,
            "content": self.content,
            "message_id": self.message_id,
        }


class HistoricalChatParser:
    @staticmethod
    def _field_parts(line: str) -> tuple[str, str] | None:
        match = FIELD_LINE.fullmatch(str(line or "").strip())
        if match is None:
            return None
        raw_label = clean_text(match.group("label"), 40).strip("[]【】()（）")
        label = re.sub(r"[\s_-]+", "", raw_label).casefold()
        return label, str(match.group("value") or "").strip()

    @classmethod
    def _labeled_export_headers(
        cls,
        lines: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        time_fields: list[dict[str, Any]] = []
        speaker_fields: list[dict[str, Any]] = []
        content_fields: list[dict[str, Any]] = []
        metadata_indexes: set[int] = set()

        for index, line in enumerate(lines):
            stripped = line.strip()
            parts = cls._field_parts(stripped)
            if parts is not None:
                label, value = parts
                if label in FIELD_SPEAKER_LABELS and value:
                    speaker_fields.append(
                        {"line_index": index, "value": clean_text(value, 80)}
                    )
                elif label in FIELD_CONTENT_LABELS:
                    content_fields.append({"line_index": index, "value": value})
                elif label in FIELD_METADATA_LABELS:
                    metadata_indexes.add(index)

            full = FULL_HEADER.fullmatch(stripped)
            short = SHORT_HEADER.fullmatch(stripped) if full is None else None
            match = full or short
            if match is None:
                continue
            label = re.sub(
                r"[\s_-]+",
                "",
                clean_text(match.group("speaker"), 40).strip("[]【】()（）"),
            ).casefold()
            if label not in FIELD_TIME_LABELS:
                continue
            raw_time = parts[1] if parts is not None else stripped
            time_fields.append(
                {
                    "line_index": index,
                    "line": index + 1,
                    "year": int(match.groupdict().get("year") or 0),
                    "month": int(match.group("month")),
                    "day": int(match.group("day")),
                    "clock": match.group("clock"),
                    "raw_time": raw_time,
                }
            )

        field_signature = (
            len(time_fields) >= 2 and (bool(speaker_fields) or len(content_fields) >= 2)
        ) or (len(time_fields) == 1 and bool(speaker_fields) and bool(content_fields))
        if not field_signature:
            return None
        if not speaker_fields:
            raise ValueError(
                "检测到“时间 / 内容”字段式聊天记录，但没有找到发送者或昵称字段；"
                "请导出包含发送者的记录，或转换为“说话人: 时间戳”格式后重试"
            )

        time_indexes = [int(item["line_index"]) for item in time_fields]
        speaker_indexes = [int(item["line_index"]) for item in speaker_fields]
        content_indexes = [int(item["line_index"]) for item in content_fields]
        speaker_by_index = {int(item["line_index"]): item for item in speaker_fields}
        content_by_index = {int(item["line_index"]): item for item in content_fields}
        speaker_index_set = set(speaker_indexes)

        before_votes = 0
        after_votes = 0
        for speaker_index in speaker_indexes:
            position = bisect_left(time_indexes, speaker_index)
            previous_time = time_indexes[position - 1] if position > 0 else None
            next_time = time_indexes[position] if position < len(time_indexes) else None
            previous_distance = (
                speaker_index - previous_time if previous_time is not None else math.inf
            )
            next_distance = (
                next_time - speaker_index if next_time is not None else math.inf
            )
            if next_distance < previous_distance:
                before_votes += 1
            else:
                after_votes += 1
        speaker_layout = "before_time" if before_votes > after_votes else "after_time"
        content_before_votes = 0
        content_after_votes = 0
        for content_index in content_indexes:
            position = bisect_left(time_indexes, content_index)
            previous_time = time_indexes[position - 1] if position > 0 else None
            next_time = time_indexes[position] if position < len(time_indexes) else None
            previous_distance = (
                content_index - previous_time if previous_time is not None else math.inf
            )
            next_distance = (
                next_time - content_index if next_time is not None else math.inf
            )
            if next_distance < previous_distance:
                content_before_votes += 1
            else:
                content_after_votes += 1
        content_layout = (
            "before_time"
            if content_before_votes > content_after_votes
            else "after_time"
        )

        selected_speakers: list[dict[str, Any] | None] = []
        missing_lines: list[int] = []
        for position, time_field in enumerate(time_fields):
            time_index = int(time_field["line_index"])
            previous_time = time_indexes[position - 1] if position > 0 else -1
            next_time = (
                time_indexes[position + 1]
                if position + 1 < len(time_indexes)
                else len(lines)
            )
            before_start = bisect_right(speaker_indexes, previous_time)
            before_end = bisect_left(speaker_indexes, time_index)
            after_start = bisect_right(speaker_indexes, time_index)
            after_end = bisect_left(speaker_indexes, next_time)
            before = speaker_indexes[before_start:before_end]
            after = speaker_indexes[after_start:after_end]
            if speaker_layout == "before_time":
                selected_index = before[-1] if before else (after[0] if after else None)
            else:
                selected_index = after[0] if after else (before[-1] if before else None)
            selected = (
                speaker_by_index.get(selected_index)
                if selected_index is not None
                else None
            )
            selected_speakers.append(selected)
            if selected is None:
                missing_lines.append(int(time_field["line"]))

        if missing_lines:
            samples = "、".join(str(item) for item in missing_lines[:5])
            raise ValueError(
                f"检测到字段式聊天记录，但有 {len(missing_lines)} 条消息找不到对应发送者"
                f"（时间字段行：{samples}）；请保留每条消息的发送者或昵称字段后重试"
            )

        headers: list[dict[str, Any]] = []
        ignored_metadata_lines = 0
        for position, time_field in enumerate(time_fields):
            time_index = int(time_field["line_index"])
            previous_time = time_indexes[position - 1] if position > 0 else -1
            next_time = (
                time_indexes[position + 1]
                if position + 1 < len(time_indexes)
                else len(lines)
            )
            content_end = next_time
            if position + 1 < len(selected_speakers):
                next_speaker = selected_speakers[position + 1]
                next_speaker_index = (
                    int(next_speaker["line_index"])
                    if next_speaker is not None
                    else next_time
                )
                if time_index < next_speaker_index < content_end:
                    content_end = next_speaker_index

            before_start = bisect_right(content_indexes, previous_time)
            before_end = bisect_left(content_indexes, time_index)
            after_start = bisect_right(content_indexes, time_index)
            after_end = bisect_left(content_indexes, content_end)
            before_content = content_indexes[before_start:before_end]
            after_content = content_indexes[after_start:after_end]
            if content_layout == "before_time":
                content_field_index = (
                    before_content[-1]
                    if before_content
                    else (after_content[0] if after_content else None)
                )
            else:
                content_field_index = (
                    after_content[0]
                    if after_content
                    else (before_content[-1] if before_content else None)
                )
            content_is_before = (
                content_field_index is not None and content_field_index < time_index
            )
            body_start = (
                content_field_index + 1
                if content_field_index is not None
                else time_index + 1
            )
            body_end = time_index if content_is_before else content_end
            body_parts = (
                [str(content_by_index[content_field_index]["value"])]
                if content_field_index is not None
                and content_by_index[content_field_index]["value"]
                else []
            )
            for line_index in range(body_start, body_end):
                if line_index in speaker_index_set or line_index in metadata_indexes:
                    ignored_metadata_lines += 1
                    continue
                value = lines[line_index]
                parts = cls._field_parts(value)
                if parts is not None and parts[0] in FIELD_CONTENT_LABELS:
                    value = parts[1]
                if re.fullmatch(r"\s*[-=*_]{3,}\s*", value):
                    continue
                body_parts.append(value)
            while body_parts and not body_parts[0].strip():
                body_parts.pop(0)
            while body_parts and not body_parts[-1].strip():
                body_parts.pop()

            selected = selected_speakers[position]
            headers.append(
                {
                    **time_field,
                    "speaker": clean_text(selected["value"], 80)
                    if selected is not None
                    else "",
                    "content": "\n".join(body_parts).strip(),
                }
            )

        return headers, {
            "source_format": "labeled_fields",
            "field_speaker_layout": speaker_layout,
            "field_content_layout": content_layout,
            "field_time_count": len(time_fields),
            "field_speaker_count": len(speaker_fields),
            "field_content_count": len(content_fields),
            "ignored_metadata_line_count": ignored_metadata_lines,
        }

    def parse(
        self, text: str, *, source_hash: str, base_year: int = 0
    ) -> dict[str, Any]:
        normalized = (
            str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
        )
        if not normalized.strip():
            raise ValueError("对话文件为空")
        if "\x00" in normalized:
            raise ValueError("对话文件包含 NUL 字节，无法按纯文本解析")

        lines = normalized.split("\n")
        labeled_result = self._labeled_export_headers(lines)
        field_stats: dict[str, Any] = {}
        headers: list[dict[str, Any]] = []
        header_like_lines: list[dict[str, Any]] = []
        if labeled_result is not None:
            headers, field_stats = labeled_result
            recognized_lines = {int(item["line_index"]) for item in headers}
            for index, line in enumerate(lines):
                if index in recognized_lines:
                    continue
                if HEADER_LIKE.match(line.strip()):
                    header_like_lines.append(
                        {"line": index + 1, "text": clean_text(line, 160)}
                    )
        else:
            for index, line in enumerate(lines):
                full = FULL_HEADER.fullmatch(line.strip())
                short = SHORT_HEADER.fullmatch(line.strip()) if full is None else None
                match = full or short
                if match is not None:
                    headers.append(
                        {
                            "line_index": index,
                            "line": index + 1,
                            "speaker": clean_text(match.group("speaker"), 80),
                            "year": int(match.groupdict().get("year") or 0),
                            "month": int(match.group("month")),
                            "day": int(match.group("day")),
                            "clock": match.group("clock"),
                            "raw_time": line.split(":", 1)[-1].strip()
                            if ":" in line
                            else line.split("：", 1)[-1].strip(),
                        }
                    )
                elif HEADER_LIKE.match(line.strip()):
                    header_like_lines.append(
                        {"line": index + 1, "text": clean_text(line, 160)}
                    )
        if not headers:
            raise ValueError("没有识别到“说话人: 时间戳”格式的消息头")

        if labeled_result is None:
            structural_headers = sum(
                1
                for item in headers
                if re.sub(
                    r"[\s_-]+",
                    "",
                    clean_text(item.get("speaker"), 40).strip("[]【】()（）"),
                ).casefold()
                in FIELD_TIME_LABELS
            )
            if len(headers) >= 3 and structural_headers * 2 >= len(headers):
                raise ValueError(
                    "检测到大量“时间”字段被当成了说话人；请上传包含发送者/昵称/内容字段的原始导出，"
                    "或转换为“说话人: 时间戳”格式后重试"
                )

        current_year = int(base_year or 0)
        previous_local: datetime | None = None
        previous_month = 0
        messages: list[ParsedChatMessage] = []
        empty_messages = 0
        inversions = 0
        inferred_count = 0
        for sequence, header in enumerate(headers, start=1):
            explicit_year = int(header["year"] or 0)
            inferred = explicit_year <= 0
            if explicit_year > 0:
                current_year = explicit_year
            elif current_year <= 0:
                raise ValueError(
                    f"第 {header['line']} 行时间缺少年份，且前文没有完整年份；请在预览时填写起始年份"
                )
            month = int(header["month"])
            if inferred and previous_month >= 10 and month <= 3:
                current_year += 1
            try:
                local = datetime.strptime(
                    f"{current_year:04d}-{month:02d}-{int(header['day']):02d} {header['clock']}",
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=LOCAL_TZ)
            except ValueError as exc:
                raise ValueError(
                    f"第 {header['line']} 行时间无效: {header['raw_time']}"
                ) from exc
            if previous_local is not None and local < previous_local:
                inversions += 1
            previous_local = local
            previous_month = month
            if inferred:
                inferred_count += 1

            if "content" in header:
                content = str(header.get("content") or "").strip()
            else:
                start = int(header["line_index"]) + 1
                end = (
                    int(headers[sequence]["line_index"])
                    if sequence < len(headers)
                    else len(lines)
                )
                content = "\n".join(lines[start:end]).strip()
            if not content:
                empty_messages += 1
                continue
            if len(content) > 16000:
                content = content[:15999].rstrip() + "…"
            message_id = (
                "hist_"
                + stable_fingerprint(
                    source_hash,
                    sequence,
                    header["speaker"],
                    local.isoformat(),
                    content,
                )[:32]
            )
            messages.append(
                ParsedChatMessage(
                    sequence=sequence,
                    speaker=header["speaker"],
                    raw_time=header["raw_time"],
                    local_time=local.isoformat(timespec="seconds"),
                    occurred_at=local.astimezone(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    inferred_year=inferred,
                    source_line=int(header["line"]),
                    content=content,
                    message_id=message_id,
                )
            )
        if not messages:
            raise ValueError("识别到了消息头，但没有可导入的消息正文")

        # 历史导出偶尔会把补发、转存片段放在文件末尾。整理链必须按绝对时间
        # 前进，但 sequence/source_line 继续保留原文件顺序，时间相同则以原顺序
        # 稳定排序，避免既丢审计线索又按错误时序总结。
        messages.sort(key=lambda item: (item.local_time, item.sequence))

        speakers: dict[str, int] = {}
        duplicate_times: dict[str, int] = {}
        for item in messages:
            speakers[item.speaker] = speakers.get(item.speaker, 0) + 1
            duplicate_times[item.local_time] = (
                duplicate_times.get(item.local_time, 0) + 1
            )
        duplicates = sum(1 for count in duplicate_times.values() if count > 1)
        return {
            "messages": [item.to_dict() for item in messages],
            "stats": {
                "line_count": len(lines),
                "message_count": len(messages),
                "speaker_count": len(speakers),
                "speakers": speakers,
                "first_at": messages[0].local_time,
                "last_at": messages[-1].local_time,
                "inferred_year_count": inferred_count,
                "time_inversion_count": inversions,
                "chronologically_reordered": inversions > 0,
                "duplicate_timestamp_groups": duplicates,
                "empty_message_count": empty_messages,
                "header_like_body_count": len(header_like_lines),
                "header_like_body_samples": header_like_lines[:8],
                "unicode_chars": len(normalized),
                "non_whitespace_chars": len(re.sub(r"\s+", "", normalized)),
                "dialogue_chars": sum(
                    len(re.sub(r"\s+", "", item.content)) for item in messages
                ),
                **field_stats,
            },
        }


class HistoricalChatSegmenter:
    def __init__(
        self,
        *,
        merge_seconds: int = 120,
        hard_gap_minutes: int = 120,
        soft_gap_minutes: int = 30,
        max_turns: int = 30,
        max_chars: int = 4200,
    ) -> None:
        self.merge_seconds = max(0, int(merge_seconds))
        self.hard_gap_minutes = max(1, int(hard_gap_minutes))
        self.soft_gap_minutes = max(1, int(soft_gap_minutes))
        self.max_turns = max(4, int(max_turns))
        self.max_chars = max(1000, int(max_chars))

    @staticmethod
    def _parse_local(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def logical_turns(
        self,
        messages: list[dict[str, Any]],
        speaker_map: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        mapping = speaker_map or {}
        for raw in messages:
            speaker = clean_text(raw.get("speaker"), 80)
            local = self._parse_local(str(raw.get("local_time") or ""))
            role = clean_text((mapping.get(speaker) or {}).get("role"), 20) or "unknown"
            if turns:
                previous = turns[-1]
                gap = (local - self._parse_local(previous["end_local"])).total_seconds()
                if speaker == previous["speaker"] and 0 <= gap <= self.merge_seconds:
                    previous["content"] += "\n" + str(raw.get("content") or "")
                    previous["end_local"] = raw["local_time"]
                    previous["end_at"] = raw["occurred_at"]
                    previous["message_ids"].append(raw["message_id"])
                    previous["sequences"].append(int(raw.get("sequence") or 0))
                    previous["source_lines"].append(int(raw.get("source_line") or 0))
                    previous["inferred_year"] = bool(
                        previous["inferred_year"] or raw.get("inferred_year")
                    )
                    continue
            turns.append(
                {
                    "speaker": speaker,
                    "role": role,
                    "start_local": raw["local_time"],
                    "end_local": raw["local_time"],
                    "start_at": raw["occurred_at"],
                    "end_at": raw["occurred_at"],
                    "content": str(raw.get("content") or ""),
                    "message_ids": [raw["message_id"]],
                    "sequences": [int(raw.get("sequence") or 0)],
                    "source_lines": [int(raw.get("source_line") or 0)],
                    "inferred_year": bool(raw.get("inferred_year")),
                }
            )
        return turns

    @staticmethod
    def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return any(marker in compact for marker in markers)

    def segments(
        self,
        messages: list[dict[str, Any]],
        speaker_map: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        turns = self.logical_turns(messages, speaker_map)
        if not turns:
            return []
        result: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []

        def flush() -> None:
            if not current:
                return
            index = len(result)
            message_ids = [
                message_id for turn in current for message_id in turn["message_ids"]
            ]
            transcript_rows = []
            for turn_index, turn in enumerate(current, start=1):
                transcript_rows.append(
                    {
                        "turn": turn_index,
                        "time": turn["start_local"],
                        "speaker": turn["speaker"],
                        "role": turn["role"],
                        "message_ids": turn["message_ids"],
                        "text": turn["content"],
                    }
                )
            transcript = "\n".join(json_dumps(row) for row in transcript_rows)
            result.append(
                {
                    "id": f"seg_{index:04d}",
                    "segment_index": index,
                    "start_at": current[0]["start_at"],
                    "end_at": current[-1]["end_at"],
                    "start_local": current[0]["start_local"],
                    "end_local": current[-1]["end_local"],
                    "local_date": current[0]["start_local"][:10],
                    "message_ids": message_ids,
                    "transcript": transcript,
                    "char_count": sum(len(turn["content"]) for turn in current),
                    "turn_count": len(current),
                    "inferred_year": any(turn["inferred_year"] for turn in current),
                }
            )
            current.clear()

        for turn in turns:
            if current:
                previous = current[-1]
                gap_minutes = (
                    self._parse_local(turn["start_local"])
                    - self._parse_local(previous["end_local"])
                ).total_seconds() / 60.0
                day_changed = turn["start_local"][:10] != previous["end_local"][:10]
                hard_boundary = gap_minutes > self.hard_gap_minutes
                soft_boundary = gap_minutes > self.soft_gap_minutes and (
                    day_changed
                    or self._has_marker(previous["content"], CLOSING_MARKERS)
                    or self._has_marker(turn["content"], OPENING_MARKERS)
                )
                over_limit = (
                    len(current) >= self.max_turns
                    or sum(len(item["content"]) for item in current)
                    + len(turn["content"])
                    > self.max_chars
                )
                safe_limit_boundary = over_limit and (
                    turn.get("role") == "user"
                    or previous.get("role") != "user"
                    or gap_minutes > 5
                )
                if hard_boundary or soft_boundary or safe_limit_boundary:
                    flush()
            current.append(turn)
        flush()
        return result
