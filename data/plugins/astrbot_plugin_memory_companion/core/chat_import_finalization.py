from __future__ import annotations

import json
from typing import Any

from .astrbot_compat import logger
from .models import (
    MemoryRecord,
    clean_text,
    json_dumps,
    stable_fingerprint,
)


class HistoricalChatImporterFinalization:
    """Finalization; shared runtime resources belong to the facade."""

    async def _insert_important_events(
        self,
        batch: dict[str, Any],
        segment: dict[str, Any],
        result: dict[str, Any],
        summary_memory_id: str,
    ) -> int:
        created = 0
        for event in result["important_events"]:
            actor = self._entity_for_name(batch, event["actor"])
            object_ref = self._entity_for_name(batch, event["object"])
            record = MemoryRecord(
                id="mem_"
                + stable_fingerprint(
                    batch["id"],
                    "important_event",
                    event["thread_key"],
                    event["content"],
                    event["start_at"],
                )[:20],
                memory_type="important_event",
                subject=actor,
                object=object_ref,
                scope="private",
                session_id=batch["session_id"],
                platform=batch.get("platform") or "",
                visibility="private_pair",
                sayability="direct",
                reality_level="historical_evidence",
                lifecycle="open_loop"
                if event["status"] in {"planned", "confirmed", "ongoing"}
                else "stable_memory",
                content=event["content"],
                evidence="；".join(event["source_message_ids"]),
                confidence=event["confidence"],
                importance=event["importance"],
                review_status="auto" if event["confidence"] >= 0.7 else "pending",
                tags=[
                    "important_event",
                    "historical_chat",
                    event["event_type"],
                    event["status"],
                ],
                metadata={
                    **event,
                    "source_summary_memory_id": summary_memory_id,
                    "segment_id": segment["id"],
                    "import_batch_id": batch["id"],
                    "owner_bot_id": batch["bot_id"],
                    "valid_from": event["start_at"],
                    "valid_until": event["end_at"],
                },
                occurred_at=event["start_at"] or segment["start_at"],
                source_plugin="historical_chat_import",
                import_batch_id=batch["id"],
            )
            await self.store.insert_memory(record)
            created += 1
        return created

    async def _stage_relationship_observations(
        self,
        batch: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> int:
        if not observations:
            return 0
        api = self._private_api()
        stage = (
            getattr(api, "stage_historical_relationship_observations", None)
            if api is not None
            else None
        )
        if not callable(stage):
            return 0
        try:
            result = stage(
                user_id=batch["user_id"],
                user_name=batch.get("user_name") or batch["user_id"],
                batch_id=batch["id"],
                observations=observations,
            )
            if hasattr(result, "__await__"):
                result = await result
            return (
                int((result or {}).get("staged") or 0)
                if isinstance(result, dict)
                else 0
            )
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 历史关系观察写入陪伴关系网失败: batch=%s error=%s",
                batch["id"],
                exc,
            )
            return 0

    async def _finalize_batch(self, batch: dict[str, Any]) -> None:
        segments = await self.store.chat_import_segments(batch["id"])
        failed = [item for item in segments if item.get("status") == "failed"]
        if failed:
            await self.store.update_chat_import_batch(
                batch["id"],
                state="paused",
                error=f"仍有 {len(failed)} 个片段失败，可恢复后重试",
            )
            return
        if batch.get("state") == "indexing":
            await self._finish_batch_indexing(batch, segments)
            return
        summaries: list[dict[str, Any]] = []
        raw_observations: list[dict[str, Any]] = []
        segment_by_id = {clean_text(item.get("id"), 120): item for item in segments}
        for item in segments:
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            raw_observations.extend(
                observation
                for observation in (result.get("relationship_observations") or [])
                if isinstance(observation, dict)
            )
            summary = clean_text(
                result.get("summary")
                or result.get("canonical_summary")
                or result.get("archive_note"),
                1600,
            )
            if summary:
                summaries.append(
                    {
                        "segment_id": item["id"],
                        "date": clean_text(item.get("local_date"), 20),
                        "start_at": item.get("start_at"),
                        "end_at": item.get("end_at"),
                        "summary": summary,
                        "events": (result.get("important_events") or [])[:4],
                        "facts": (result.get("stable_facts") or [])[:4],
                        "relationships": (
                            result.get("relationship_observations") or []
                        )[:4],
                    }
                )
        batch_stats = dict(batch.get("stats") or {})
        saved_reconcile = (
            batch_stats.get("historical_reconcile")
            if isinstance(batch_stats.get("historical_reconcile"), dict)
            else None
        )
        if saved_reconcile is None and batch.get("state") != "reconciling":
            batch = (
                await self.store.update_chat_import_batch(
                    batch["id"],
                    state="reconciling",
                )
                or batch
            )
        warnings: list[str] = []
        observations: list[dict[str, Any]] = []
        digest_outputs: list[dict[str, Any]] = []
        phase_summary = ""
        reconcile_failed = False
        if saved_reconcile is not None:
            digest_outputs = [
                item
                for item in (saved_reconcile.get("outputs") or [])
                if isinstance(item, dict)
            ]
            phase_summary = clean_text(saved_reconcile.get("phase_summary"), 1600)
            warnings = [
                clean_text(item, 240)
                for item in (saved_reconcile.get("warnings") or [])
                if clean_text(item, 240)
            ]
            reconcile_failed = bool(saved_reconcile.get("failed"))
        elif summaries:
            try:
                # 同一天的片段不拆到不同整理请求，避免生成多份互相竞争的日摘要。
                day_groups: list[list[dict[str, Any]]] = []
                for summary_item in summaries:
                    if day_groups and day_groups[-1][0].get("date") == summary_item.get(
                        "date"
                    ):
                        day_groups[-1].append(summary_item)
                    else:
                        day_groups.append([summary_item])
                chunks: list[list[dict[str, Any]]] = []
                current_chunk: list[dict[str, Any]] = []
                current_cost = 0
                for day_group in day_groups:
                    cost = sum(
                        len(json_dumps(summary_item)) + 1 for summary_item in day_group
                    )
                    if current_chunk and current_cost + cost > 5200:
                        chunks.append(current_chunk)
                        current_chunk = []
                        current_cost = 0
                    current_chunk.extend(day_group)
                    current_cost += cost
                if current_chunk:
                    chunks.append(current_chunk)
                for chunk in chunks:
                    prompt = (
                        "下面是已经有原始证据约束的历史会话片段摘要。请进行跨片段去重，不得增加新事实。"
                        "daily_digests 必须使用已确认称呼的第三人称，按当天时间顺序保留关键互动、决定、承诺、结果和情绪变化；"
                        "信息丰富的日期写成约 150—500 个中文字符，简单日期可以更短，但禁止把多个有意义片段压成一句泛泛结论。"
                        '输出 JSON：{"daily_digests":[{"date":"YYYY-MM-DD","summary":"","importance":0.0}],'
                        '"stable_facts":[{"content":"","valid_from":"ISO时间","confidence":0.0,"segment_ids":[]}],'
                        '"relationship_observations":[{"title":"","content":"","observed_at":"ISO时间",'
                        '"confidence":0.0,"segment_ids":[]}],"phase_summary":"这一批片段体现的关系或生活阶段摘要"}。'
                        "日摘要只能使用输入中存在的 date；稳定事实和关系观察必须引用输入中的 segment_ids。"
                        "关系观察只保留真正影响相处方式、称呼、信任或边界的候选，整批最多 24 条。"
                        "相互矛盾的事实不要合并，后出现的纠正要标明替代关系。片段数据："
                        + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
                    )
                    raw_output = await self._call_json_provider(
                        batch, prompt=prompt, task="historical_chat_reconcile"
                    )
                    digest_outputs.append(
                        self._normalize_reconcile_output(
                            raw_output, chunk, segment_by_id
                        )
                    )
                phase_summaries = [
                    clean_text(output.get("phase_summary"), 1200)
                    for output in digest_outputs
                    if clean_text(output.get("phase_summary"), 1200)
                ]
                if phase_summaries:
                    phase_summary = phase_summaries[0]
                    if len(phase_summaries) > 1:
                        phase_output = await self._call_json_provider(
                            batch,
                            prompt=(
                                "下面是同一份历史聊天不同时间段的阶段摘要。只做去重合并，不增加新事实。"
                                '输出 JSON：{"phase_summary":"完整阶段摘要"}。数据：'
                                + json.dumps(phase_summaries, ensure_ascii=False)
                            ),
                            task="historical_chat_phase_summary",
                        )
                        phase_summary = (
                            clean_text(phase_output.get("phase_summary"), 1600)
                            or phase_summary
                        )
            except Exception as exc:
                warnings.append(f"全局整理失败但片段记忆已保留: {clean_text(exc, 180)}")
                reconcile_failed = True

        if saved_reconcile is None:
            batch_stats["historical_reconcile"] = {
                "outputs": digest_outputs,
                "phase_summary": phase_summary,
                "failed": reconcile_failed,
                "warnings": warnings,
            }
            await self.store.update_chat_import_batch(
                batch["id"],
                state="reconciling",
                stats=batch_stats,
                error="；".join(warnings),
            )

        if not reconcile_failed:
            for output in digest_outputs:
                observations.extend(
                    item
                    for item in (output.get("relationship_observations") or [])
                    if isinstance(item, dict)
                )
                await self._insert_reconciled_memories(batch, output)
            if phase_summary:
                await self._insert_phase_summary(batch, phase_summary, summaries)
        else:
            observations.extend(raw_observations)
        if not observations:
            observations.extend(raw_observations)
        observations = self._dedupe_relationship_observations(observations)
        staged = await self._stage_relationship_observations(batch, observations)
        refreshed = await self.store.get_chat_import_batch(batch["id"]) or batch
        batch_stats = dict(refreshed.get("stats") or batch_stats)
        reconcile_checkpoint = batch_stats.get("historical_reconcile")
        if isinstance(reconcile_checkpoint, dict):
            reconcile_checkpoint["applied"] = True
            batch_stats["historical_reconcile"] = reconcile_checkpoint
        await self.store.update_chat_import_batch(
            batch["id"],
            state="indexing",
            relationship_observation_count=int(
                refreshed.get("relationship_observation_count") or 0
            )
            + staged,
            stats=batch_stats,
            error="；".join(warnings),
        )
        indexing_batch = await self.store.get_chat_import_batch(batch["id"]) or batch
        await self._finish_batch_indexing(indexing_batch, segments)

    async def _finish_batch_indexing(
        self,
        batch: dict[str, Any],
        segments: list[dict[str, Any]] | None = None,
    ) -> None:
        batch = await self._repair_batch_identity_links(batch)
        batch = await self._repair_batch_summary_perspective(batch)
        segments = (
            segments
            if segments is not None
            else await self.store.chat_import_segments(batch["id"])
        )
        if not self._detail_quality_current(batch):
            batch = (
                await self.store.update_chat_import_batch(
                    batch["id"],
                    state="enriching",
                )
                or batch
            )
            await self._enrich_batch_details(batch, segments)
            return
        warnings = (
            [clean_text(batch.get("error"), 1000)]
            if clean_text(batch.get("error"), 1000)
            else []
        )
        embedding_result = await self._index_batch_embeddings(batch)
        if embedding_result.get("status") == "partial":
            warnings.append(
                f"向量索引部分失败: {embedding_result.get('indexed', 0)}/{embedding_result.get('eligible', 0)}"
            )
        memory_counts = await self.store.chat_import_memory_counts(batch["id"])
        refreshed = await self.store.get_chat_import_batch(batch["id"]) or batch
        stats = dict(refreshed.get("stats") or {})
        stats["memory_counts"] = memory_counts
        stats["embedding"] = embedding_result
        await self.store.update_chat_import_batch(
            batch["id"],
            state="completed_with_warnings" if warnings else "completed",
            completed_segments=len(segments),
            checkpoint_segment=len(segments),
            summary_memory_count=int(memory_counts.get("conversation_summary") or 0),
            important_event_count=int(memory_counts.get("important_event") or 0),
            stats=stats,
            error="；".join(warnings),
        )
        logger.info(
            "[MemoryCompanion] 历史对话导入完成: batch=%s messages=%s segments=%s warnings=%s",
            batch["id"],
            (batch.get("stats") or {}).get("message_count"),
            len(segments),
            len(warnings),
        )
