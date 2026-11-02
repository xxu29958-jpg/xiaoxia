from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any

from .identity import parse_scope_from_session
from .models import (
    clean_text,
    json_dumps,
    json_loads,
    utc_now,
)


class MemoryStoreImportRebind:
    """Import rebind; state and transaction ownership remain in the facade."""

    async def rebind_chat_import_batch(
        self,
        *,
        batch_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        user_name: str = "",
        bot_id: str,
        bot_name: str = "",
        backup_path: str = "",
    ) -> dict[str, int]:
        return await asyncio.to_thread(
            self._rebind_chat_import_batch_sync,
            batch_id,
            session_id,
            platform,
            user_id,
            user_name,
            bot_id,
            bot_name,
            backup_path,
        )

    def _rebind_chat_import_batch_sync(
        self,
        batch_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        user_name: str,
        bot_id: str,
        bot_name: str,
        backup_path: str,
    ) -> dict[str, int]:
        batch_id = clean_text(batch_id, 120)
        session_id = clean_text(session_id, 200)
        platform = clean_text(platform, 40)
        user_id = clean_text(user_id, 120)
        user_name = clean_text(user_name, 120)
        bot_id = clean_text(bot_id, 120)
        bot_name = clean_text(bot_name, 120)
        backup_path = clean_text(backup_path, 2000)
        if not batch_id or not session_id or not platform or not user_id or not bot_id:
            raise ValueError("批次、用户、会话、Bot 和平台均不能为空")
        if user_id == bot_id:
            raise ValueError("目标用户 ID 和 Bot ID 不能相同")
        parsed_scope, parsed_target = parse_scope_from_session(session_id)
        if parsed_scope != "private" or clean_text(parsed_target, 120) != user_id:
            raise ValueError("目标用户 ID 与私聊会话不一致")
        session_platform = (
            clean_text(session_id.split(":", 1)[0], 40) if ":" in session_id else ""
        )
        if session_platform and session_platform.casefold() != platform.casefold():
            raise ValueError("目标平台与私聊会话不一致")

        counts = {
            "memories": 0,
            "timeline": 0,
            "embeddings_removed": 0,
            "relationships": 0,
            "relationships_merged": 0,
            "knowledge_edges_removed": 0,
            "knowledge_nodes_removed": 0,
            "batch": 0,
        }
        now = utc_now()
        with self._lock:
            with self._transaction_sync():
                batch_row = self._conn.execute(
                    "SELECT * FROM chat_import_batches WHERE id=?",
                    (batch_id,),
                ).fetchone()
                if batch_row is None:
                    raise ValueError("导入批次不存在")
                state = clean_text(batch_row["state"], 40)
                if state not in {"completed", "completed_with_warnings"}:
                    raise ValueError("仅已完成的导入批次可以修正归属")
                if clean_text(batch_row["scope"], 40) != "private":
                    raise ValueError("仅私聊历史导入支持修正归属")
                old_session_id = clean_text(batch_row["session_id"], 200)
                old_user_id = clean_text(batch_row["user_id"], 120)
                old_bot_id = clean_text(batch_row["bot_id"], 120)
                if (old_session_id, old_user_id, old_bot_id) == (
                    session_id,
                    user_id,
                    bot_id,
                ):
                    raise ValueError("当前批次已经属于所选私聊，无需重复修正")
                target_context = self._private_memory_context_row_sync(
                    session_id,
                    user_id,
                    bot_id,
                    batch_id,
                )
                if target_context is None:
                    raise ValueError(
                        "目标私聊上下文不存在，或用户、Bot 与所选会话不一致"
                    )

                old_target = {
                    "session_id": old_session_id,
                    "platform": clean_text(batch_row["platform"], 40),
                    "user_id": old_user_id,
                    "user_name": clean_text(batch_row["user_name"], 120),
                    "bot_id": old_bot_id,
                    "bot_name": clean_text(batch_row["bot_name"], 120),
                }
                matched_user_name = ""
                matched_bot_name = ""
                for prefix in ("subject", "object"):
                    kind = clean_text(target_context[f"{prefix}_kind"], 40)
                    entity_id = clean_text(target_context[f"{prefix}_id"], 120)
                    name = clean_text(target_context[f"{prefix}_name"], 120)
                    if kind == "user" and entity_id == user_id:
                        matched_user_name = name or matched_user_name
                    elif kind == "bot" and entity_id == bot_id:
                        matched_bot_name = name or matched_bot_name
                new_target = {
                    "session_id": session_id,
                    "platform": platform,
                    "user_id": user_id,
                    "user_name": user_name
                    or matched_user_name
                    or old_target["user_name"],
                    "bot_id": bot_id,
                    "bot_name": bot_name or matched_bot_name or old_target["bot_name"],
                }

                speaker_map = json_loads(batch_row["speaker_map"], {})
                if not isinstance(speaker_map, dict):
                    speaker_map = {}
                roles_by_id: dict[str, set[str]] = {}
                roles_by_name: dict[str, set[str]] = {}

                def identity_forms(value: Any) -> set[str]:
                    text = clean_text(value, 160).casefold()
                    if not text:
                        return set()
                    forms = {text}
                    forms.update(
                        clean_text(inner, 160).casefold()
                        for inner in re.findall(
                            r"[（(\[【]([^）)\]】]+)[）)\]】]", text
                        )
                    )
                    forms.add(
                        re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", text).strip()
                    )
                    forms.update(
                        part.strip()
                        for part in re.split(r"[\s/|,，、;；:：]+", text)
                        if part.strip()
                    )
                    return {item for item in forms if item}

                for mapped_role, mapped_id, mapped_name in (
                    ("user", old_target["user_id"], old_target["user_name"]),
                    ("bot", old_target["bot_id"], old_target["bot_name"]),
                ):
                    if mapped_id:
                        roles_by_id.setdefault(mapped_id, set()).add(mapped_role)
                    for form in identity_forms(mapped_name):
                        roles_by_name.setdefault(form, set()).add(mapped_role)

                for speaker, mapping in speaker_map.items():
                    if not isinstance(mapping, dict):
                        continue
                    mapped_role = clean_text(mapping.get("role"), 20)
                    if mapped_role not in {"user", "bot"}:
                        continue
                    for mapped_id in (
                        mapping.get("source_entity_id"),
                        mapping.get("entity_id"),
                    ):
                        normalized_id = clean_text(mapped_id, 120)
                        if normalized_id:
                            roles_by_id.setdefault(normalized_id, set()).add(
                                mapped_role
                            )
                    for mapped_name in (speaker, mapping.get("display_name")):
                        for form in identity_forms(mapped_name):
                            roles_by_name.setdefault(form, set()).add(mapped_role)

                def binding_metadata(raw: Any) -> dict[str, Any]:
                    metadata = json_loads(raw, {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    original = metadata.get("original_import_target")
                    if not isinstance(original, dict):
                        metadata["original_import_target"] = dict(old_target)
                    metadata["current_import_target"] = dict(new_target)
                    metadata["identity_rebound_at"] = now
                    return metadata

                memory_rows = self._conn.execute(
                    "SELECT * FROM memories WHERE import_batch_id=?",
                    (batch_id,),
                ).fetchall()
                timeline_rows = self._conn.execute(
                    "SELECT * FROM timeline WHERE import_batch_id=?",
                    (batch_id,),
                ).fetchall()
                if not memory_rows and not timeline_rows:
                    raise ValueError("该导入批次没有可修正的记忆或历史消息")
                memory_ids = [clean_text(row["id"], 120) for row in memory_rows]

                def rebound_entity(
                    row: sqlite3.Row, prefix: str
                ) -> tuple[str, str, str, str]:
                    kind = clean_text(row[f"{prefix}_kind"], 40)
                    entity_id = clean_text(row[f"{prefix}_id"], 120)
                    name = clean_text(row[f"{prefix}_name"], 80)
                    role_key = f"{prefix}_role"
                    role = (
                        clean_text(row[role_key], 80) if role_key in row.keys() else ""
                    )
                    if kind == "user" or (
                        old_target["user_id"] and entity_id == old_target["user_id"]
                    ):
                        return (
                            kind or "user",
                            user_id,
                            new_target["user_name"] or name,
                            role,
                        )
                    if kind == "bot" or (
                        old_target["bot_id"] and entity_id == old_target["bot_id"]
                    ):
                        return (
                            kind or "bot",
                            bot_id,
                            new_target["bot_name"] or name,
                            role,
                        )
                    inferred_roles = set(roles_by_id.get(entity_id, set()))
                    row_metadata = (
                        json_loads(row["metadata"], {})
                        if "metadata" in row.keys()
                        else {}
                    )
                    if not isinstance(row_metadata, dict):
                        row_metadata = {}
                    fallback_name = row_metadata.get(
                        "actor" if prefix == "subject" else "object"
                    )
                    for candidate_name in (name, fallback_name):
                        for form in identity_forms(candidate_name):
                            inferred_roles.update(roles_by_name.get(form, set()))
                    if len(inferred_roles) == 1:
                        inferred_role = next(iter(inferred_roles))
                        if inferred_role == "user":
                            return (
                                "user",
                                user_id,
                                new_target["user_name"] or name,
                                role,
                            )
                        return "bot", bot_id, new_target["bot_name"] or name, role
                    return kind, entity_id, name, role

                self._rebind_import_memories(
                    batch_id,
                    binding_metadata,
                    bot_id,
                    counts,
                    memory_rows,
                    now,
                    platform,
                    rebound_entity,
                    session_id,
                )

                self._rebind_import_derived_records(
                    binding_metadata,
                    bot_id,
                    counts,
                    memory_ids,
                    now,
                    rebound_entity,
                    session_id,
                    user_id,
                )

                self._rebind_import_timeline(
                    batch_id,
                    binding_metadata,
                    bot_id,
                    counts,
                    old_target,
                    platform,
                    session_id,
                    timeline_rows,
                    user_id,
                )

                self._rebind_import_batch_metadata(
                    backup_path,
                    batch_id,
                    batch_row,
                    bot_id,
                    counts,
                    new_target,
                    now,
                    old_target,
                    platform,
                    session_id,
                    speaker_map,
                    user_id,
                )
            self._embedding_candidate_cache.clear()
            self._embedding_candidate_cache_revision = ""
        return counts

    def _rebind_import_batch_metadata(
        self,
        backup_path,
        batch_id,
        batch_row,
        bot_id,
        counts,
        new_target,
        now,
        old_target,
        platform,
        session_id,
        speaker_map,
        user_id,
    ):
        """Rebind import batch metadata."""
        for mapping in speaker_map.values():
            if not isinstance(mapping, dict):
                continue
            mapping.setdefault(
                "source_entity_id", clean_text(mapping.get("entity_id"), 120)
            )
            role = clean_text(mapping.get("role"), 20)
            if role == "user":
                mapping["entity_id"] = user_id
            elif role == "bot":
                mapping["entity_id"] = bot_id

        stats = json_loads(batch_row["stats"], {})
        if not isinstance(stats, dict):
            stats = {}
        rebind_entry = {
            "at": now,
            "from": old_target,
            "to": new_target,
            "backup_path": backup_path,
        }
        history = stats.get("identity_rebind_history")
        if not isinstance(history, list):
            history = []
        stats["identity_rebind_history"] = [*history, rebind_entry][-12:]
        stats["identity_rebind"] = rebind_entry
        identity_links = stats.get("identity_links")
        if not isinstance(identity_links, dict):
            identity_links = {}
        try:
            identity_links_version = int(identity_links.get("version") or 0)
        except (TypeError, ValueError):
            identity_links_version = 0
        identity_links.update(
            {
                "version": max(2, identity_links_version),
                "target_user_id": user_id,
                "canonical_session_id": session_id,
            }
        )
        stats["identity_links"] = identity_links

        batch_cur = self._conn.execute(
            """
            UPDATE chat_import_batches
            SET session_id=?, scope='private', platform=?, user_id=?, user_name=?,
                bot_id=?, bot_name=?, speaker_map=?, stats=?, updated_at=?
            WHERE id=?
            """,
            (
                session_id,
                platform,
                user_id,
                new_target["user_name"],
                bot_id,
                new_target["bot_name"],
                json_dumps(speaker_map),
                json_dumps(stats),
                now,
                batch_id,
            ),
        )
        counts["batch"] = int(batch_cur.rowcount or 0)

    def _rebind_import_timeline(
        self,
        batch_id,
        binding_metadata,
        bot_id,
        counts,
        old_target,
        platform,
        session_id,
        timeline_rows,
        user_id,
    ):
        """Rebind import timeline."""
        for row in timeline_rows:
            subject_id = clean_text(row["subject_id"], 120)
            object_id = clean_text(row["object_id"], 120)
            event_type = clean_text(row["event_type"], 40)
            if event_type == "bot_response":
                subject_id, object_id = bot_id, user_id
            elif event_type == "user_message":
                subject_id, object_id = user_id, bot_id
            else:
                if old_target["user_id"] and subject_id == old_target["user_id"]:
                    subject_id = user_id
                elif old_target["bot_id"] and subject_id == old_target["bot_id"]:
                    subject_id = bot_id
                if old_target["user_id"] and object_id == old_target["user_id"]:
                    object_id = user_id
                elif old_target["bot_id"] and object_id == old_target["bot_id"]:
                    object_id = bot_id
            metadata = binding_metadata(row["metadata"])
            metadata["owner_bot_id"] = bot_id
            metadata["participant_user_id"] = user_id
            metadata["platform"] = platform
            self._conn.execute(
                """
                UPDATE timeline
                SET session_id=?, scope='private', subject_id=?, object_id=?, metadata=?
                WHERE id=? AND import_batch_id=?
                """,
                (
                    session_id,
                    subject_id,
                    object_id,
                    json_dumps(metadata),
                    row["id"],
                    batch_id,
                ),
            )
            counts["timeline"] += 1

    def _rebind_import_derived_records(
        self,
        binding_metadata,
        bot_id,
        counts,
        memory_ids,
        now,
        rebound_entity,
        session_id,
        user_id,
    ):
        """Rebind import derived records."""
        if memory_ids:
            placeholders = ",".join("?" for _ in memory_ids)
            relationship_rows = self._conn.execute(
                f"SELECT * FROM relationship_edges WHERE source_memory_id IN ({placeholders})",
                memory_ids,
            ).fetchall()
            for row in relationship_rows:
                subject = rebound_entity(row, "subject")
                object_ref = rebound_entity(row, "object")
                collision = self._conn.execute(
                    """
                    SELECT id FROM relationship_edges
                    WHERE subject_kind=? AND subject_id=? AND object_kind=? AND object_id=?
                      AND relation_type=? AND scope='private' AND session_id=? AND id!=?
                    LIMIT 1
                    """,
                    (
                        subject[0],
                        subject[1],
                        object_ref[0],
                        object_ref[1],
                        row["relation_type"],
                        session_id,
                        row["id"],
                    ),
                ).fetchone()
                if collision:
                    self._conn.execute(
                        "DELETE FROM relationship_edges WHERE id=?", (row["id"],)
                    )
                    counts["relationships_merged"] += 1
                    continue
                metadata = binding_metadata(row["metadata"])
                metadata["owner_bot_id"] = bot_id
                metadata["participant_user_id"] = user_id
                self._conn.execute(
                    """
                    UPDATE relationship_edges
                    SET subject_kind=?, subject_id=?, subject_name=?,
                        object_kind=?, object_id=?, object_name=?,
                        scope='private', session_id=?, group_id='', metadata=?, updated_at=?
                    WHERE id=? AND source_memory_id=?
                    """,
                    (
                        subject[0],
                        subject[1],
                        subject[2],
                        object_ref[0],
                        object_ref[1],
                        object_ref[2],
                        session_id,
                        json_dumps(metadata),
                        now,
                        row["id"],
                        row["source_memory_id"],
                    ),
                )
                counts["relationships"] += 1

            knowledge_rows = self._conn.execute(
                f"""
                SELECT id, source_node_id, target_node_id
                FROM knowledge_edges
                WHERE source_memory_id IN ({placeholders})
                """,
                memory_ids,
            ).fetchall()
            node_ids = {
                clean_text(node_id, 120)
                for row in knowledge_rows
                for node_id in (row["source_node_id"], row["target_node_id"])
                if clean_text(node_id, 120)
            }
            counts["knowledge_edges_removed"] = int(
                self._conn.execute(
                    f"DELETE FROM knowledge_edges WHERE source_memory_id IN ({placeholders})",
                    memory_ids,
                ).rowcount
                or 0
            )
            for node_id in node_ids:
                used = self._conn.execute(
                    """
                    SELECT 1 FROM knowledge_edges
                    WHERE source_node_id=? OR target_node_id=?
                    LIMIT 1
                    """,
                    (node_id, node_id),
                ).fetchone()
                if used is None:
                    counts["knowledge_nodes_removed"] += int(
                        self._conn.execute(
                            "DELETE FROM knowledge_nodes WHERE id=?",
                            (node_id,),
                        ).rowcount
                        or 0
                    )

    def _rebind_import_memories(
        self,
        batch_id,
        binding_metadata,
        bot_id,
        counts,
        memory_rows,
        now,
        platform,
        rebound_entity,
        session_id,
    ):
        """Rebind import memories."""
        for row in memory_rows:
            subject = rebound_entity(row, "subject")
            object_ref = rebound_entity(row, "object")
            metadata = binding_metadata(row["metadata"])
            metadata["owner_bot_id"] = bot_id
            atom = self._memory_atom_record_for_row(
                row,
                subject_kind=subject[0],
                subject_id=subject[1],
                subject_name=subject[2],
                subject_role=subject[3],
                object_kind=object_ref[0],
                object_id=object_ref[1],
                object_name=object_ref[2],
                object_role=object_ref[3],
                scope="private",
                session_id=session_id,
                platform=platform,
                group_id="",
                metadata=json_dumps(metadata),
                owner_bot_id=bot_id,
            )
            self._conn.execute(
                """
                UPDATE memories
                SET subject_kind=?, subject_id=?, subject_name=?, subject_role=?,
                    object_kind=?, object_id=?, object_name=?, object_role=?,
                    scope='private', session_id=?, platform=?, group_id='',
                    metadata=?, owner_bot_id=?, canonical_key=?,
                    content_fingerprint=?, updated_at=?
                WHERE id=? AND import_batch_id=?
                """,
                (
                    *subject,
                    *object_ref,
                    session_id,
                    platform,
                    json_dumps(metadata),
                    atom.owner_bot_id,
                    atom.canonical_key,
                    atom.content_fingerprint,
                    now,
                    row["id"],
                    batch_id,
                ),
            )
            refreshed = self._conn.execute(
                "SELECT * FROM memories WHERE id=?",
                (row["id"],),
            ).fetchone()
            self._upsert_memory_fts_row(refreshed)
            counts["memories"] += 1
