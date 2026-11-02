from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .assertions import (
    ASSERTION_MEMORY_TYPE,
    CROSS_SCENE_DIMENSIONS,
    MAX_ASSERTIONS_PER_BATCH,
    assertion_is_promotable,
    cardinality_for,
    normalize_assertion,
)
from .astrbot_compat import (
    logger,
)
from .models import (
    EntityRef,
    MemoryRecord,
    SessionContext,
    clean_text,
    stable_fingerprint,
)


class MemoryCompanionServiceSummaryEvidence:
    """service summary evidence; the facade owns all shared resources."""

    async def _store_summary_evidence_candidate(
        self,
        ctx: SessionContext,
        batch_id: str,
        rows: list[dict[str, Any]],
        payload: dict[str, Any] | None,
        feedback: str,
        *,
        mark_timeline_summarized: bool = True,
    ) -> str:
        """Keep a batch whose summary could not be validated instead of freezing it.

        Quarantine is the right answer only when the provider itself is broken.
        When the model did return a body, discarding it lost the conversation for
        good: the batch kept owning its events, so those messages could never
        reach memory and their timeline rows could never be cleaned up either.
        The batch is therefore stored as a pending evidence candidate -- visible
        in the panel and reviewable, but never injected as a stable memory.
        """
        body = self.summarizer.compose_memory_content(payload or {})
        valid_days = max(
            1,
            self.config.int("memory_summary.candidate_valid_days", 30),
        )
        record = MemoryRecord(
            id=self.stable_id("summary_batch", batch_id),
            memory_type="conversation_summary",
            subject=self._bot_entity(ctx)
            if ctx.scope == "group"
            else EntityRef(
                kind="user",
                id=ctx.user_id,
                name=ctx.user_name,
                role="conversation_partner",
            ),
            object=EntityRef(
                kind="group", id=ctx.group_id, name=ctx.group_name, role="group"
            )
            if ctx.scope == "group"
            else self._bot_entity(ctx),
            scope=ctx.scope,
            session_id=ctx.session_id,
            platform=ctx.platform,
            group_id=ctx.group_id,
            visibility="group_public" if ctx.scope == "group" else "private_pair",
            sayability="direct",
            reality_level="llm_summary",
            lifecycle="short_term_candidate",
            content=body,
            evidence="\n".join(
                clean_text(row.get("content"), 220)
                for row in rows[: self.config.int("memory_summary.evidence_events", 12)]
                if clean_text(row.get("content"), 220)
            ),
            confidence=0.4,
            importance=0.3,
            review_status="pending",
            tags=[
                "summary",
                "evidence_candidate",
                "summary_validation_failed",
                ctx.scope,
            ],
            owner_bot_id=self._bot_subject_id(ctx),
            valid_to=(
                datetime.now(timezone.utc) + timedelta(days=valid_days)
            ).isoformat(timespec="seconds"),
            durability="short",
            sensitivity="internal",
            metadata={
                "summary_batch_id": batch_id,
                "summary_event_count": len(rows),
                "degraded_summary": True,
                "summary_validation_error": clean_text(feedback, 1800),
                "quality_warnings": list(
                    (payload or {}).get("_quality_warnings")
                    or (payload or {}).get("_validation_warnings")
                    or []
                ),
                "summary_refs": (payload or {}).get("summary_refs", []),
                "key_facts_with_refs": (payload or {}).get("key_facts_with_refs", []),
                "topics": (payload or {}).get("topics", []),
                "raw_timeline_preserved": True,
                "owner_bot_id": self._bot_subject_id(ctx),
                "policy_version": "memory_capture_v1",
                "source_event_ids": [
                    clean_text(row.get("id"), 160)
                    for row in rows
                    if clean_text(row.get("id"), 160)
                ],
            },
        )
        memory_id = await self.store.finish_summary_batch(
            batch_id,
            [str(row["id"]) for row in rows],
            record=record,
            mark_timeline=mark_timeline_summarized,
        )
        await self._persist_assertions(ctx, payload or {}, memory_id, rows)
        logger.warning(
            "[MemoryCompanion] 阶段总结已降级为待复核候选: session=%s batch=%s memory=%s",
            ctx.session_id,
            batch_id,
            memory_id,
        )
        return memory_id

    async def _persist_assertions(
        self,
        ctx: SessionContext,
        payload: dict[str, Any],
        summary_memory_id: str,
        rows: list[dict[str, Any]],
    ) -> list[str]:
        """Store what this window says is true about the person, merged by slot.

        A summary records what was discussed; an assertion records what is
        true, which is the only part a later conversation can be answered from.
        Both come out of the single summary call, so this adds no latency and
        no extra model cost.

        Failures are contained on purpose: a summary that has already landed
        must never be retried or rolled back because one assertion could not be
        stored.
        """
        if not self.config.bool("memory_assertions.enabled", True):
            return []
        raw = payload.get("assertions")
        if not isinstance(raw, list) or not raw:
            return []
        limit = max(
            1,
            self.config.int(
                "memory_assertions.max_per_batch", MAX_ASSERTIONS_PER_BATCH
            ),
        )
        row_by_id = {
            clean_text(row.get("id"), 160): row
            for row in rows
            if clean_text(row.get("id"), 160)
        }
        stored: list[str] = []
        promoted = 0
        for item in raw[:limit]:
            assertion = normalize_assertion(item)
            if not assertion:
                continue
            cited = [row_by_id[ref] for ref in assertion["refs"] if ref in row_by_id]
            if not cited or any(ref not in row_by_id for ref in assertion["refs"]):
                continue
            if (
                self.summarizer.citation_check(assertion["value"], cited)[0]
                != "supported"
            ):
                continue
            subject_ctx = self._assertion_subject_context(ctx, assertion, cited)
            if subject_ctx is None:
                continue
            evidence = [
                clean_text(row.get("content"), 2000)
                for row in cited
                if clean_text(row.get("content"), 2000)
            ]
            promotable = assertion_is_promotable(assertion, evidence)
            record = self._assertion_record(
                subject_ctx, assertion, summary_memory_id, evidence, promotable
            )
            try:
                result = await self.store.upsert_assertion(record)
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 断言写入失败，已跳过该条: session=%s dimension=%s error=%s",
                    ctx.session_id,
                    assertion["dimension"],
                    self._describe_exception(exc),
                )
                continue
            if not result.get("ok"):
                continue
            stored.append(clean_text(result.get("memory_id"), 120))
            promoted += 1 if promotable else 0
        if stored:
            logger.info(
                "[MemoryCompanion] 已提炼断言: session=%s stored=%s promoted=%s",
                ctx.session_id,
                len(stored),
                promoted,
            )
        return stored

    def _assertion_subject_context(
        self,
        ctx: SessionContext,
        assertion: dict[str, Any],
        cited: list[dict[str, Any]],
    ) -> SessionContext | None:
        """Bind a personal assertion to its cited speaker, including group turns."""
        subject = clean_text(assertion.get("subject"), 160).casefold()
        speakers: dict[str, str] = {}
        for row in cited:
            if clean_text(row.get("event_type"), 40) != "user_message":
                continue
            actor_id = clean_text(row.get("subject_id"), 160)
            actor_name = clean_text(row.get("subject_name"), 160)
            # Older private timeline rows may omit the actor, but the pair still
            # identifies them. Group rows need an explicit speaker.
            if not actor_id and ctx.scope != "group":
                actor_id, actor_name = ctx.user_id, ctx.user_name
            if not actor_id:
                continue
            aliases = {actor_id.casefold(), actor_name.casefold()}
            if actor_id == ctx.user_id:
                aliases.add(clean_text(ctx.user_name, 160).casefold())
            if subject in aliases and subject:
                if ctx.scope != "group" and actor_id != ctx.user_id:
                    continue
                if (
                    self.summarizer.citation_check(assertion["value"], [row])[0]
                    == "supported"
                ):
                    speakers[actor_id] = actor_name or (
                        ctx.user_name if actor_id == ctx.user_id else actor_id
                    )
        if len(speakers) != 1:
            return None
        actor_id, actor_name = next(iter(speakers.items()))
        return replace(ctx, user_id=actor_id, user_name=actor_name)

    def _assertion_record(
        self,
        ctx: SessionContext,
        assertion: dict[str, Any],
        summary_memory_id: str,
        evidence: list[str],
        promotable: bool,
    ) -> MemoryRecord:
        """Build one assertion row, scoped exactly like the conversation it came from.

        Visibility mirrors the source window: a fact distilled from a private
        chat stays inside that private pair, so no later step can surface it in
        a group.  Only the already-whitelisted preference dimensions are marked
        cross-scene, and only for the user themselves.
        """
        dimension = assertion["dimension"]
        cross_scene = ctx.scope != "group" and dimension in CROSS_SCENE_DIMENSIONS
        value = assertion["value"]
        qualifier = assertion["qualifier"]
        content = f"{qualifier}：{value}" if qualifier else value
        return MemoryRecord(
            id=self.stable_id(
                "assertion",
                self._bot_subject_id(ctx),
                ctx.platform,
                ctx.scope,
                ctx.session_id,
                ctx.user_id,
                dimension,
                assertion["polarity"],
                value,
                summary_memory_id,
            ),
            memory_type=ASSERTION_MEMORY_TYPE,
            subject=EntityRef(
                kind="user",
                id=ctx.user_id,
                name=ctx.user_name,
                role="assertion_subject",
            ),
            object=self._bot_entity(ctx),
            scope=ctx.scope,
            session_id=ctx.session_id,
            platform=ctx.platform,
            group_id=ctx.group_id,
            visibility="private_pair" if ctx.scope != "group" else "group_public",
            sayability="direct",
            reality_level="llm_assertion",
            lifecycle="stable_memory" if promotable else "short_term_candidate",
            content=content,
            evidence="\n".join(evidence),
            confidence=0.78 if promotable else 0.5,
            importance=0.6 if promotable else 0.3,
            review_status="auto" if promotable else "pending",
            tags=[
                "assertion",
                dimension,
                assertion["polarity"],
                "long_term" if promotable else "evidence_candidate",
            ],
            owner_bot_id=self._bot_subject_id(ctx),
            durability="normal" if promotable else "short",
            sensitivity="internal",
            metadata={
                "profile_dimension": dimension,
                "profile_polarity": assertion["polarity"],
                "profile_value": value,
                "normalized_value": assertion["normalized_value"],
                "profile_cardinality": cardinality_for(dimension),
                "profile_state": "active" if promotable else "candidate",
                "assertion_durability": assertion["durability"],
                "assertion_evidence_refs": list(assertion["refs"]),
                "assertion_source_summary_id": summary_memory_id,
                "assertion_cross_scene": cross_scene,
                "assertion_extractor": "summary_assertion_v1",
                "extraction_quality_score": 0.78 if promotable else 0.5,
                "owner_bot_id": self._bot_subject_id(ctx),
                "policy_version": "memory_assertion_v1",
            },
        )

    async def _record_verified_group_bot_self_facts(
        self,
        ctx: SessionContext,
        rows: list[dict[str, Any]],
        payload: dict[str, Any],
        summary_memory_id: str,
    ) -> int:
        if ctx.scope != "group":
            return 0
        facts = payload.get("bot_self_facts") if isinstance(payload, dict) else None
        if not isinstance(facts, list):
            return 0

        current_bot_id = self._bot_subject_id(ctx)
        bot_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            event_id = clean_text(row.get("id"), 160)
            event_type = clean_text(row.get("event_type"), 40).lower()
            subject_id = clean_text(row.get("subject_id"), 120)
            if not event_id or event_type != "bot_response":
                continue
            if subject_id and subject_id not in {"self", current_bot_id}:
                continue
            bot_rows[event_id] = row

        created = 0
        for item in facts[:4]:
            if not isinstance(item, dict):
                continue
            event_id = clean_text(
                item.get("event_id") or item.get("source_event_id"), 160
            )
            source_row = bot_rows.get(event_id)
            if source_row is None:
                continue
            fact = clean_text(item.get("fact") or item.get("content"), 220)
            if len(fact) < 4 or self.summarizer._looks_like_prompt_injection(fact):
                continue
            kind = clean_text(item.get("kind"), 24).lower()
            if kind not in {"schedule", "commitment", "action"}:
                kind = "schedule"
            memory_type = "schedule_fragment" if kind == "schedule" else "self_action"
            evidence = clean_text(source_row.get("content"), 700)
            if not evidence or not self.summarizer._bot_self_fact_supported_by_evidence(
                fact, evidence
            ):
                continue
            record = MemoryRecord(
                id=self.stable_id(
                    "group_bot_self_fact", ctx.session_id, event_id, kind, fact
                ),
                memory_type=memory_type,
                subject=self._bot_entity(ctx),
                object=EntityRef(
                    kind="group", id=ctx.group_id, name=ctx.group_name, role="group"
                ),
                scope="group",
                session_id=ctx.session_id,
                platform=ctx.platform,
                message_id=event_id,
                group_id=ctx.group_id,
                visibility="bot_self",
                sayability="direct",
                reality_level="bot_action",
                lifecycle="stable_memory",
                content=fact,
                evidence=evidence,
                confidence=0.86,
                importance=0.6,
                review_status="auto",
                tags=["bot_self_fact", "group_origin", kind],
                metadata={
                    "verified_bot_self_fact": True,
                    "direct_bot_response": True,
                    "source_event_id": event_id,
                    "source_summary_id": summary_memory_id,
                    "group_origin": ctx.group_id,
                    "fact_kind": kind,
                    "owner_bot_id": current_bot_id,
                },
                occurred_at=clean_text(
                    source_row.get("occurred_at") or source_row.get("created_at"), 80
                ),
                source_plugin="memory_companion",
            )
            try:
                self.importance.calibrate(record, source="verified_group_bot_fact")
                memory_id = await self.store.insert_memory(record)
                self._schedule_memory_embedding(memory_id, record)
                created += 1
            except Exception as exc:
                logger.warning(
                    "[MemoryCompanion] 群聊 Bot 自身事实写入失败: session=%s event=%s error=%s",
                    ctx.session_id,
                    event_id,
                    exc,
                )
        return created

    async def _index_summary_knowledge_graph(
        self,
        ctx: SessionContext,
        record: MemoryRecord,
        payload: dict[str, Any],
        memory_id: str,
    ) -> None:
        if not self.config.bool("knowledge_graph.enabled", True):
            return
        try:
            indexed = await self._index_summary_knowledge_graph_inner(
                ctx, record, payload, memory_id
            )
            if indexed:
                logger.info(
                    "[MemoryCompanion] 已写入知识图谱关联: session=%s memory=%s edges=%s",
                    ctx.session_id,
                    memory_id,
                    indexed,
                )
        except Exception as exc:
            logger.warning(
                "[MemoryCompanion] 知识图谱关联写入失败: session=%s memory=%s error=%s",
                ctx.session_id,
                memory_id,
                exc,
                exc_info=True,
            )

    async def _index_summary_knowledge_graph_inner(
        self,
        ctx: SessionContext,
        record: MemoryRecord,
        payload: dict[str, Any],
        memory_id: str,
    ) -> int:
        topics = self._kg_list(payload.get("topics"), limit=8, item_limit=80)
        key_facts = self._kg_list(payload.get("key_facts"), limit=10, item_limit=180)
        participants = self._kg_list(
            payload.get("participants"), limit=12, item_limit=80
        )
        associations = self._kg_associations(payload.get("associations"))
        if ctx.scope == "private" and (ctx.user_name or ctx.user_id):
            participants = self._kg_unique(
                [ctx.user_name or ctx.user_id, *participants], limit=12
            )
        if not topics and not key_facts and not participants and not associations:
            return 0

        scope = ctx.scope
        session_id = ctx.session_id
        group_id = ctx.group_id
        memory_label = clean_text(record.content, 120) or memory_id
        memory_node = await self.store.upsert_knowledge_node(
            node_type="memory",
            label=memory_label,
            scope=scope,
            session_id=session_id,
            group_id=group_id,
            confidence=0.72,
            metadata={"memory_id": memory_id, "memory_type": record.memory_type},
        )
        window_label = (
            f"群聊 {group_id}"
            if scope == "group"
            else f"私聊 {ctx.user_name or ctx.user_id or session_id}"
        )
        window_node = await self.store.upsert_knowledge_node(
            node_type="window",
            label=window_label,
            scope=scope,
            session_id=session_id,
            group_id=group_id,
            confidence=0.86,
            metadata={"session_id": session_id, "scope": scope},
        )
        edge_count = 0
        if window_node and memory_node:
            edge_count += await self._kg_edge(
                window_node,
                memory_node,
                "contains_memory",
                ctx,
                memory_id,
                memory_label,
                0.78,
            )

        person_nodes: dict[str, str] = {}
        for participant in participants:
            node_type = (
                "bot"
                if participant in {"我", "Bot", "我(Bot)", "bot", "BOT"}
                else "person"
            )
            node = await self.store.upsert_knowledge_node(
                node_type=node_type,
                label=participant,
                scope=scope,
                session_id=session_id,
                group_id=group_id,
                confidence=0.78,
                metadata={"source_memory_id": memory_id},
            )
            if not node:
                continue
            person_nodes[participant] = node
            edge_count += await self._kg_edge(
                node,
                memory_node,
                "participated_in",
                ctx,
                memory_id,
                record.content,
                0.72,
            )

        topic_nodes: list[tuple[str, str]] = []
        topic_nodes_by_label: dict[str, str] = {}
        for topic in topics:
            node = await self.store.upsert_knowledge_node(
                node_type="topic",
                label=topic,
                scope=scope,
                session_id=session_id,
                group_id=group_id,
                confidence=0.74,
                metadata={"source_memory_id": memory_id},
            )
            if not node:
                continue
            topic_nodes.append((topic, node))
            topic_nodes_by_label[topic.casefold()] = node
            edge_count += await self._kg_edge(
                memory_node,
                node,
                "has_topic",
                ctx,
                memory_id,
                topic,
                0.74,
            )

        fact_nodes_by_label: dict[str, str] = {}
        for fact in key_facts:
            fact_node = await self.store.upsert_knowledge_node(
                node_type="fact",
                label=fact,
                scope=scope,
                session_id=session_id,
                group_id=group_id,
                confidence=0.68,
                metadata={"source_memory_id": memory_id},
            )
            if not fact_node:
                continue
            fact_nodes_by_label[fact.casefold()] = fact_node
            edge_count += await self._kg_edge(
                memory_node,
                fact_node,
                "has_fact",
                ctx,
                memory_id,
                fact,
                0.68,
            )
            for name, person_node in person_nodes.items():
                if name and name in fact:
                    edge_count += await self._kg_edge(
                        person_node,
                        fact_node,
                        "mentioned_fact",
                        ctx,
                        memory_id,
                        fact,
                        0.64,
                    )
            for topic, topic_node in topic_nodes:
                if topic and topic in fact:
                    edge_count += await self._kg_edge(
                        fact_node,
                        topic_node,
                        "about_topic",
                        ctx,
                        memory_id,
                        fact,
                        0.62,
                    )

        for association in associations:
            cue = association["cue"]
            tag = association["tag"]
            content = association["content"]
            layer = association["layer"]
            cue_node = await self.store.upsert_knowledge_node(
                node_type="cue",
                label=cue,
                scope=scope,
                session_id=session_id,
                group_id=group_id,
                confidence=0.7,
                metadata={"source_memory_id": memory_id, "content_layer": layer},
            )
            if not cue_node or not memory_node:
                continue
            target_node = fact_nodes_by_label.get(content.casefold())
            relation = "routes_to_fact"
            if not target_node:
                target_node = topic_nodes_by_label.get(content.casefold())
                relation = "routes_to_topic"
            if not target_node:
                target_node = memory_node
                relation = "routes_to_memory"
            edge_count += await self._kg_edge(
                cue_node,
                target_node,
                f"{relation}_{stable_fingerprint(tag, content, layer)[:8]}",
                ctx,
                memory_id,
                content,
                0.7,
                metadata={
                    "source": "summary_association_v1",
                    "associative_tag": tag,
                    "content_layer": layer,
                },
            )
        return edge_count

    async def _kg_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        ctx: SessionContext,
        memory_id: str,
        evidence: str,
        confidence: float,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        edge_metadata = {"source": "summary_indexer_v1"}
        if isinstance(metadata, dict):
            edge_metadata.update(metadata)
        edge_id = await self.store.upsert_knowledge_edge(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            scope=ctx.scope,
            session_id=ctx.session_id,
            group_id=ctx.group_id,
            source_memory_id=memory_id,
            evidence=evidence,
            confidence=confidence,
            review_status="auto",
            metadata=edge_metadata,
        )
        return 1 if edge_id else 0

    def _kg_list(self, value: Any, *, limit: int, item_limit: int) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return self._kg_unique(
            [clean_text(item, item_limit) for item in value], limit=limit
        )

    def _kg_associations(self, value: Any) -> list[dict[str, str]]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            cue = clean_text(item.get("cue"), 80)
            tag = clean_text(item.get("tag"), 80)
            content = clean_text(item.get("content"), 240)
            layer = clean_text(item.get("layer"), 24).casefold()
            if (
                not cue
                or not tag
                or not content
                or layer not in {"episodic", "semantic", "abstraction"}
            ):
                continue
            key = (cue.casefold(), tag.casefold(), content.casefold(), layer)
            if key in seen:
                continue
            seen.add(key)
            result.append({"cue": cue, "tag": tag, "content": content, "layer": layer})
            if len(result) >= 12:
                break
        return result

    @staticmethod
    def _kg_unique(values: list[str], *, limit: int) -> list[str]:
        result: list[str] = []
        for value in values:
            text = clean_text(value, 180)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result
