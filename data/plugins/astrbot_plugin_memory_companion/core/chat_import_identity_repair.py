from __future__ import annotations

import re
from typing import Any

from .models import (
    EntityRef,
    clean_text,
)


class HistoricalChatImporterIdentityRepair:
    """Identity repair; shared runtime resources belong to the facade."""

    def _entity_for_name(self, batch: dict[str, Any], name: str) -> EntityRef:
        query = clean_text(name, 80)
        query_forms = self._identity_alias_forms(query)
        matched_roles = {
            role
            for role in ("user", "bot")
            if query_forms.intersection(self._participant_aliases(batch, role))
        }
        if matched_roles == {"bot"}:
            return EntityRef.bot_self(
                bot_id=clean_text(batch.get("bot_id"), 120),
                bot_name=clean_text(batch.get("bot_name"), 80) or query,
            )
        if matched_roles == {"user"}:
            return EntityRef(
                kind="user",
                id=clean_text(batch.get("user_id"), 120),
                name=clean_text(batch.get("user_name"), 80) or query,
                role="conversation_partner",
            )
        return EntityRef(kind="unknown", id="", name=query, role="mentioned")

    @staticmethod
    def _identity_alias_forms(value: Any) -> set[str]:
        text = clean_text(value, 120).lower()
        if not text:
            return set()

        def compact(part: str) -> str:
            return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", part.lower())

        forms = {compact(text)}
        for inner in re.findall(r"[（(\[【]([^）)\]】]+)[）)\]】]", text):
            forms.add(compact(inner))
        without_brackets = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", text)
        forms.add(compact(without_brackets))
        for part in re.split(r"[\s/|,，、;；:：]+", text):
            forms.add(compact(part))
        return {item for item in forms if item}

    @staticmethod
    def _has_ambiguous_first_person(text: Any) -> bool:
        value = clean_text(text, 2200)
        value = re.sub(r"(?:自我|忘我|无我)", "", value)
        return any(token in value for token in ("我", "你", "咱"))

    @classmethod
    def _neutral_detailed_summary(cls, narrative: Any, canonical: Any) -> str:
        narrative_text = clean_text(narrative, 2200)
        canonical_text = clean_text(canonical, 800)
        if narrative_text and not cls._has_ambiguous_first_person(narrative_text):
            return narrative_text
        return canonical_text or narrative_text

    def _detail_quality_current(self, batch: dict[str, Any]) -> bool:
        detail_quality = (batch.get("stats") or {}).get("detail_quality")
        return (
            isinstance(detail_quality, dict)
            and int(detail_quality.get("version") or 0) >= self.DETAIL_SCHEMA_VERSION
        )

    @classmethod
    def _detail_content_sufficient(
        cls,
        content: Any,
        metadata: Any,
        source_chars: int,
    ) -> bool:
        metadata = metadata if isinstance(metadata, dict) else {}
        try:
            detail_version = int(metadata.get("detail_schema_version") or 0)
        except (TypeError, ValueError):
            detail_version = 0
        if detail_version < cls.DETAIL_SCHEMA_VERSION:
            return False
        if (
            clean_text(metadata.get("summary_perspective"), 40)
            != "neutral_third_person"
        ):
            return False
        text = clean_text(content, 2200)
        if not text or cls._has_ambiguous_first_person(text):
            return False
        try:
            source_length = max(0, int(source_chars or 0))
        except (TypeError, ValueError):
            source_length = 0
        minimum = 100 if source_length >= 600 else 60 if source_length >= 250 else 15
        return len(text) >= minimum

    def _participant_aliases(self, batch: dict[str, Any], role: str) -> set[str]:
        values: list[Any] = []
        if role == "bot":
            values.extend([batch.get("bot_id"), batch.get("bot_name"), "Bot", "机器人"])
        else:
            values.extend([batch.get("user_id"), batch.get("user_name"), "用户"])
        for speaker, mapping in (batch.get("speaker_map") or {}).items():
            if clean_text(mapping.get("role"), 20) != role:
                continue
            values.extend(
                [speaker, mapping.get("display_name"), mapping.get("entity_id")]
            )
        return {
            alias for value in values for alias in self._identity_alias_forms(value)
        }

    async def _register_batch_identities(self, batch: dict[str, Any]) -> None:
        session_id = clean_text(batch.get("session_id"), 200)
        identity_platform = clean_text(
            session_id.split(":", 1)[0] if ":" in session_id else "", 40
        )
        identity_platform = (
            identity_platform or clean_text(batch.get("platform"), 40) or "unknown"
        )
        for role in ("user", "bot"):
            aliases = sorted(self._participant_aliases(batch, role))
            if role == "bot":
                entity = EntityRef.bot_self(
                    clean_text(batch.get("bot_id"), 120),
                    clean_text(batch.get("bot_name"), 80),
                )
            else:
                entity = EntityRef(
                    kind="user",
                    id=clean_text(batch.get("user_id"), 120),
                    name=clean_text(batch.get("user_name"), 80),
                    role="conversation_partner",
                )
            if entity.id:
                await self.store.upsert_identity(
                    platform=identity_platform,
                    entity=entity,
                    aliases=aliases,
                    profile={"source": "historical_chat_identity_confirmation"},
                    confidence=0.95,
                )

    @staticmethod
    def _entity_payload(entity: EntityRef) -> dict[str, str]:
        return {
            "kind": clean_text(entity.kind, 40),
            "id": clean_text(entity.id, 120),
            "name": clean_text(entity.name, 80),
            "role": clean_text(entity.role, 80),
        }

    async def _repair_batch_identity_links(
        self, batch: dict[str, Any]
    ) -> dict[str, Any]:
        stats = dict(batch.get("stats") or {})
        checkpoint = (
            stats.get("identity_links")
            if isinstance(stats.get("identity_links"), dict)
            else {}
        )
        if int(checkpoint.get("version") or 0) >= self.IDENTITY_LINKS_VERSION:
            return batch
        user_id = clean_text(batch.get("user_id"), 120)
        canonical_session = await self.store.preferred_private_session_id(
            user_id,
            clean_text(batch.get("bot_id"), 120),
        )
        canonical_session = canonical_session or clean_text(
            batch.get("session_id"), 200
        )
        records = await self.store.list_chat_import_memories(
            clean_text(batch.get("id"), 120)
        )
        entity_links: dict[str, dict[str, dict[str, str]]] = {}
        for record in records:
            links: dict[str, dict[str, str]] = {}
            if not record.subject.id or record.subject.kind == "unknown":
                resolved = self._entity_for_name(
                    batch, record.subject.name or record.metadata.get("actor")
                )
                if resolved.id:
                    links["subject"] = self._entity_payload(resolved)
            if not record.object.id or record.object.kind == "unknown":
                resolved = self._entity_for_name(
                    batch, record.object.name or record.metadata.get("object")
                )
                if resolved.id:
                    links["object"] = self._entity_payload(resolved)
            if links:
                entity_links[record.id] = links
        repaired = await self.store.repair_chat_import_identity_links(
            batch_id=clean_text(batch.get("id"), 120),
            session_id=canonical_session,
            entity_links=entity_links,
        )
        await self._register_batch_identities(
            {**batch, "session_id": canonical_session}
        )
        stats["identity_links"] = {
            "version": self.IDENTITY_LINKS_VERSION,
            "target_user_id": user_id,
            "canonical_session_id": canonical_session,
            "repaired_memories": int(repaired.get("memories") or 0),
            "repaired_entities": int(repaired.get("entities") or 0),
            "repaired_timeline": int(repaired.get("timeline") or 0),
        }
        updated = await self.store.update_chat_import_batch(
            clean_text(batch.get("id"), 120),
            stats=stats,
        )
        return updated or {**batch, "session_id": canonical_session, "stats": stats}

    async def _repair_batch_summary_perspective(
        self, batch: dict[str, Any]
    ) -> dict[str, Any]:
        stats = dict(batch.get("stats") or {})
        checkpoint = (
            stats.get("summary_perspective")
            if isinstance(stats.get("summary_perspective"), dict)
            else {}
        )
        if int(checkpoint.get("version") or 0) >= self.SUMMARY_PERSPECTIVE_VERSION:
            return batch
        repaired = await self.store.neutralize_chat_import_summary_perspective(
            clean_text(batch.get("id"), 120)
        )
        stats["summary_perspective"] = {
            "version": self.SUMMARY_PERSPECTIVE_VERSION,
            "mode": "neutral_third_person",
            "repaired_memories": int(repaired.get("memories") or 0),
            "embeddings_removed": int(repaired.get("embeddings_removed") or 0),
        }
        embeddings_removed = int(repaired.get("embeddings_removed") or 0)
        if embeddings_removed and isinstance(stats.get("embedding"), dict):
            embedding_stats = dict(stats["embedding"])
            embedding_stats["status"] = "pending_reindex"
            embedding_stats["indexed"] = max(
                0, int(embedding_stats.get("indexed") or 0) - embeddings_removed
            )
            stats["embedding"] = embedding_stats
        updated = await self.store.update_chat_import_batch(
            clean_text(batch.get("id"), 120),
            state="indexing"
            if embeddings_removed
            else clean_text(batch.get("state"), 40),
            stats=stats,
        )
        return updated or {**batch, "stats": stats}
