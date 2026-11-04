"""Erasure targets and matching for native private working state."""

import json

from .models import clean_text

CLEAR_TABLES = (
    "memory_fts",
    "emotion_event_deliveries",
    "emotion_events",
    "portrait_learning_queue",
    "portrait_daily_runs",
    "portrait_suppressions",
    "portrait_facts",
    "portrait_evidence",
    "portrait_operations",
    "portrait_people",
    "review_queue",
    "injection_logs",
    "summary_failures",
    "summary_batches",
    "chat_import_segments",
    "chat_import_batches",
    "relationship_edges",
    "knowledge_edges",
    "knowledge_nodes",
    "working_objects",
    "timeline",
    "cross_window_threads",
    "memory_acl_rules",
    "memory_acl_policies",
    "memory_embeddings",
    "identities",
    "memories",
    "import_batches",
)


def validate_scope(target_type, group_id, user_id):
    target_type = clean_text(target_type, 40).lower()
    group_id, user_id = clean_text(group_id, 120), clean_text(user_id, 120)
    if target_type not in {"group", "private", "group_member"}:
        raise ValueError("target_type must be group, private or group_member")
    if target_type == "group" and not group_id:
        raise ValueError("group_id is required")
    if target_type == "private" and not user_id:
        raise ValueError("user_id is required")
    if target_type == "group_member" and (not group_id or not user_id):
        raise ValueError("group_id and user_id are required")
    return target_type, group_id, user_id


def working_scope(db, target_type, user_id, *, execute=False):
    """An erased private source withdraws its whole derived working object.

    Minimal hashed operation/name/import markers remain to prevent replay and
    automatic re-adoption of preserved legacy JSON; they contain no message text.
    """
    if target_type != "private":
        return 0
    rows = db.execute("SELECT owner,id,record FROM working_revisions").fetchall()
    keys = set()
    for row in rows:
        source = json.loads(row[2])["source"]
        origin = source.get("origin", "").split(":", 2)
        if source.get("user_id") == user_id or (
            len(origin) == 3
            and origin[1].lower()
            in {"friendmessage", "privatemessage", "friend", "private"}
            and origin[2] == user_id
        ):
            keys.add((row[0], row[1]))
    if execute:
        db.executemany(
            "DELETE FROM working_objects WHERE owner=? AND id=?", sorted(keys)
        )
    return len(keys)
