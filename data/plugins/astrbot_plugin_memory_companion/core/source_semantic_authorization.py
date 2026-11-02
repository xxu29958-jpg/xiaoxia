from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from .source_semantic_common import _SOURCE_PARTITION_FIELDS


def _build_scope_matches(
    build_scope: Any, row: Mapping[str, Any], metadata: Mapping[str, Any]
) -> bool:
    if not isinstance(build_scope, Mapping):
        raise ValueError("semantic_build_scope_invalid")
    kind = build_scope.get("kind")
    if kind == "all":
        if set(build_scope) != {"kind"}:
            raise ValueError("semantic_build_scope_invalid")
        return True
    if kind != "partition":
        raise ValueError("semantic_build_scope_invalid")
    allowed = {"kind", *_SOURCE_PARTITION_FIELDS}
    if set(build_scope) - allowed:
        raise ValueError("semantic_build_scope_invalid")
    required = {
        "scope",
        "session_id",
        "object_id",
        "owner_bot_id",
        "platform",
        "persona_id",
    }
    if not required.issubset(build_scope):
        raise ValueError("semantic_build_scope_invalid")
    required_nonempty = required - {"persona_id"}
    if (
        build_scope.get("scope") not in {"private", "group"}
        or any(
            not isinstance(build_scope.get(key), str) or not build_scope.get(key)
            for key in required_nonempty - {"scope"}
        )
        or not isinstance(build_scope.get("persona_id"), str)
        or (
            "participant_user_id" in build_scope
            and not isinstance(build_scope["participant_user_id"], str)
        )
    ):
        raise ValueError("semantic_build_scope_invalid")
    actual = {
        "scope": row.get("scope", ""),
        "session_id": row.get("session_id", ""),
        "object_id": row.get("object_id", ""),
        "owner_bot_id": metadata.get("owner_bot_id", ""),
        "platform": metadata.get("platform", ""),
        "persona_id": metadata.get("persona_id", ""),
        "participant_user_id": metadata.get("participant_user_id", ""),
    }
    return all(
        actual.get(key, "") == value
        for key, value in build_scope.items()
        if key != "kind"
    )


def _build_scope_sql(build_scope: Mapping[str, Any]) -> tuple[str, list[str]]:
    _build_scope_matches(build_scope, {}, {})
    if build_scope.get("kind") == "all":
        return "1=1", []
    expressions = {
        "scope": "t.scope",
        "session_id": "t.session_id",
        "object_id": "t.object_id",
        "owner_bot_id": "COALESCE(json_extract(CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END,'$.owner_bot_id'),'')",
        "platform": "COALESCE(json_extract(CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END,'$.platform'),'')",
        "persona_id": "COALESCE(json_extract(CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END,'$.persona_id'),'')",
        "participant_user_id": "COALESCE(json_extract(CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END,'$.participant_user_id'),'')",
    }
    clauses = []
    params: list[str] = []
    for key, value in build_scope.items():
        if key == "kind":
            continue
        clauses.append(f"{expressions[key]}=?")
        params.append(value)
    return " AND ".join(clauses), params


def _authorization_result(
    authorize_source: Callable[[dict[str, Any], dict[str, Any]], bool],
    row: Mapping[str, Any],
    metadata: dict[str, Any],
) -> bool:
    if not callable(authorize_source):
        raise ValueError("semantic_source_authorizer_required")
    projection = {key: row[key] for key in row.keys() if key != "content"}
    projection["metadata"] = metadata
    try:
        return authorize_source(projection, metadata) is True
    except Exception as exc:
        raise ValueError("semantic_source_authorization_failed") from exc


def _writable_generation(conn: sqlite3.Connection, generation: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM source_semantic_generations WHERE generation=?",
        (generation,),
    ).fetchone()
    if row is None or row["state"] not in {"building", "ready"}:
        raise ValueError("semantic_generation_not_writable")
    return row
