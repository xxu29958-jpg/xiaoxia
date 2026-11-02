"""Bounded, turn-local query receipts. No semantic inference or model calls."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import time
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any

from . import query_notes
from .models import json_dumps

PROFILE = "memory.local-query-session.v1"
PROFILE_V3 = "memory.local-query-session.v3"
TOOL_NAME = "memory_companion_query"
OPERATIONS = ("recall", "sources", "navigate", "events")
OPERATIONS_V3 = (*OPERATIONS, "discover")
SOURCE_DISCOVERY_PROFILE = "memory.local-source-discovery.v1"
CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "docs/contracts/query-session/v1"
CONTRACT_ROOT_V3 = (
    Path(__file__).resolve().parents[1] / "docs/contracts/query-session/v3"
)
MAX_OPERATIONS = 24
MAX_LOG_BYTES = 16384
_CALL: ContextVar[Any] = ContextVar("memory_query_call", default=None)
_CONTEXT: ContextVar[Any] = ContextVar("memory_query_context", default=None)


def enabled(service: Any) -> bool:
    return service.config.bool("memory_tools.enable_query_progress", True)


async def query_context(service: Any, event: Any) -> Any:
    call = _CALL.get()
    if call and call["service"] is service and call["event"] is event:
        return call["context"]
    context = _CONTEXT.get()
    if context and context[0] is service and context[1] is event:
        return context[2]
    return await service.identity.resolve_event_context(event)


def note_budget(service: Any, receipt: dict[str, Any]) -> None:
    call = _CALL.get()
    if call and call["service"] is service and receipt.get("accepted"):
        call["record"]["reserved_steps"] += 1


def turn_state(
    service: Any, key: str, now: float, *, create: bool
) -> dict[str, Any] | None:
    """Called under the existing reconstruction lock; reads do not extend TTL."""
    state = service._reconstruction_states.get(key)
    if state and now - state.get("last_seen", 0) > service._RECONSTRUCTION_STATE_TTL:
        if create:
            service._reconstruction_states.pop(key, None)
        state = None
    if not create:
        return state
    if state is None:
        state = {"steps": 0, "signatures": set(), "last_seen": now}
        service._reconstruction_states[key] = state
    state["last_seen"] = now
    if len(service._reconstruction_states) > service._RECONSTRUCTION_STATE_MAX:
        oldest = sorted(
            service._reconstruction_states,
            key=lambda k: service._reconstruction_states[k].get("last_seen", 0),
        )
        for old in oldest:
            if old != key:
                service._reconstruction_states.pop(old, None)
            if len(service._reconstruction_states) <= service._RECONSTRUCTION_STATE_MAX:
                break
    return state


def _parameters(value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Keep bounded query facts; do not retain event quotes or arbitrary objects."""
    value = {
        k: v
        for k, v in value.items()
        if k not in {"self", "event", "p5_attestation", "p5_attestation_consumer"}
    }
    if "parameters" in value:
        value = value["parameters"]
    if "plan" in value:
        plan = value["plan"]
        if isinstance(plan, dict):
            try:
                raw = json_dumps(plan)
            except (ValueError, TypeError, RecursionError):
                return {}, True
            return {
                "operation": plan.get("operation"),
                "goal": str(plan.get("goal", ""))[:240],
                "rows": len(plan.get("rows", []))
                if isinstance(plan.get("rows"), list)
                else None,
                "plan_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            }, True
        return {}, True
    try:
        raw = json_dumps(value)
        if len(raw.encode("utf-8")) <= 2048:
            return json.loads(raw), False
    except (ValueError, TypeError, RecursionError):
        pass
    return {}, True


def _refs(result: dict[str, Any]) -> tuple[list[str], list[str]]:
    sources = [
        s.get("source_ref") for s in result.get("sources", []) if isinstance(s, dict)
    ]
    memories = [m.get("id") for m in result.get("memories", []) if isinstance(m, dict)]
    for item in result.get("evidence", []):
        if isinstance(item, dict):
            memories.append(item.get("memory_id") or item.get("id"))
            sources.extend(
                s.get("source_ref")
                for s in item.get("sources", [])
                if isinstance(s, dict)
            )
    for item in result.get("events", []):
        if isinstance(item, dict):
            sources.extend(
                s.get("source_ref")
                for s in item.get("evidence", [])
                if isinstance(s, dict)
            )
    return list(dict.fromkeys(s for s in sources if isinstance(s, str)))[:96], list(
        dict.fromkeys(m for m in memories if isinstance(m, str))
    )[:96]


def unread_offset(receipt: dict[str, Any]) -> int | None:
    offset = 0
    for start, end in sorted(receipt["spans"]):
        if start > offset:
            return offset
        offset = max(offset, end)
    return None if receipt.get("text_length") == offset else offset


def track_query(operation: str):
    """Track the common owner boundary, including calls through legacy tools."""

    def decorate(method):
        signature = inspect.signature(method)

        @wraps(method)
        async def tracked(service, event, *args, **kwargs):
            if not enabled(service):
                return await method(service, event, *args, **kwargs)
            if service._closing or service._closed:
                return {"ok": False, "error": "query_service_closed"}
            raw_ctx = await query_context(service, event)
            ctx = service._normalized_session_context(raw_ctx)
            if not service._scope_feature_enabled(ctx, "recall"):
                return await method(service, event, *args, **kwargs)
            started = time.monotonic()
            key = service._reconstruction_budget_key(event, ctx)
            params, omitted = _parameters(
                dict(signature.bind(service, event, *args, **kwargs).arguments)
            )
            try:
                versions = await service.store.query_progress_revisions()
            except Exception:
                versions = None
            async with service._reconstruction_lock:
                state = turn_state(service, key, started, create=True)
                ledger = state.setdefault(
                    "query_progress",
                    {
                        "id": "mq_" + secrets.token_hex(8),
                        "revision": 0,
                        "completed": 0,
                        "recall_calls": 0,
                        "operations": [],
                        "active": {},
                        "omitted_operations": 0,
                    },
                )
                ledger["revision"] += 1
                record = {
                    "id": ledger["revision"],
                    "operation": operation,
                    "parameters": params,
                    "parameters_omitted": omitted,
                    "state": "running",
                    "reserved_steps": 0,
                    "versions": versions,
                    "source_refs": [],
                    "memory_refs": [],
                }
                # Ongoing calls retain their own receipt if the displayed log is full.
                ledger["active"][record["id"]] = record
            outer = _CONTEXT.get()
            if outer and outer[0] is service and outer[1] is event:
                outer[3]["operation_id"] = record["id"]
            token = _CALL.set(
                {
                    "service": service,
                    "event": event,
                    "context": raw_ctx,
                    "record": record,
                }
            )
            result = None
            try:
                result = await method(service, event, *args, **kwargs)
                record["state"] = "succeeded" if result.get("ok") else "rejected"
                record["error"] = str(result.get("error", ""))[:160]
                record["source_refs"], record["memory_refs"] = _refs(result)
                record["result_count"] = len(
                    result.get(
                        "sources",
                        result.get(
                            "memories", result.get("evidence", result.get("events", []))
                        ),
                    )
                )
                usage = result.get("usage")
                record["usage"] = (
                    {
                        k: usage[k]
                        for k in ("model_calls", "returned_chars", "source_reads")
                        if k in usage
                    }
                    if isinstance(usage, dict)
                    else {}
                )
                record["usage_known"] = bool(record["usage"])
                return result
            except asyncio.CancelledError:
                record["state"], record["error"] = "cancelled", "cancelled"
                raise
            except Exception:
                record["state"], record["error"] = "failed", "query_failed"
                raise
            finally:
                _CALL.reset(token)
                record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
                async with service._reconstruction_lock:
                    # A clear, replacement or shutdown must not recreate old state.
                    if (
                        service._reconstruction_states.get(key) is state
                        and not service._closing
                    ):
                        ledger["active"].pop(record["id"], None)
                        ledger["completed"] += 1
                        ledger["recall_calls"] += int(operation == "recall")
                        ledger["revision"] += 1
                        ledger["operations"].append(record)
                        consumed = record["parameters"].get("cursor")
                        if record["state"] == "succeeded" and consumed in state.get(
                            "source_cursors", {}
                        ):
                            state.setdefault("consumed_source_cursors", set()).add(
                                consumed
                            )
                        query_notes.trim_history(state)

        return tracked

    return decorate


async def progress(
    service: Any,
    event: Any,
    ctx: Any,
    *,
    detail: bool = False,
    notes: dict[str, Any] | None = None,
    note_ids: list[str] | None = None,
    note_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.monotonic()
    key = service._reconstruction_budget_key(event, ctx)
    async with service._reconstruction_lock:
        state = turn_state(service, key, now, create=False)
        if not state or not state.get("query_progress"):
            if notes is not None:
                notes.update(state="empty")
            return {"state": "empty"}
        # Copy only receipts; no raw messages, event plans or full tool results.
        ledger = deepcopy(state["query_progress"])
        issued = deepcopy(state.get("issued_sources", {}))
        cursors = deepcopy(state.get("source_cursors", {}))
        cursors.update(deepcopy(state.get("source_batch_cursors", {})))
        consumed = set(state.get("consumed_source_cursors", set()))
        steps = state.get("steps", 0)
    try:
        versions = await service.store.query_progress_revisions()
    except Exception:
        return {"state": "unavailable"}
    if service._closing or not service._scope_feature_enabled(ctx, "recall"):
        return {"state": "unavailable"}
    async with service._reconstruction_lock:
        if service._reconstruction_states.get(key) is not state:
            return {"state": "unavailable"}
        if notes is not None and query_notes.enabled(service):
            notes.update(
                query_notes.view(
                    state,
                    versions,
                    detail=detail,
                    note_ids=note_ids,
                    receipt=note_receipt,
                )
            )
    now = time.monotonic()

    def valid(item):
        return (
            item.get("source_revision"),
            item.get("policy_revision"),
        ) == versions and item.get("expires_at", 0) >= now

    available = {ref: item for ref, item in issued.items() if valid(item)}
    available_cursors = {
        ref: item
        for ref, item in cursors.items()
        if valid(item) and ref not in consumed
    }
    max_steps = service._reconstruction_max_steps()
    output = {
        "state": "ready",
        "session": ledger["id"],
        "revision": ledger["revision"],
        "completed": ledger["completed"],
        "in_flight": len(ledger["active"]),
        "steps": {
            "used": steps,
            "limit": max_steps,
            "remaining": max(0, max_steps - steps),
        },
        "recall_calls": ledger["recall_calls"],
        "recall_item_limit": 10,
        "read_sources": len(issued),
        "valid_sources": len(available),
        "partial_sources": sum(
            unread_offset(item) is not None for item in available.values()
        ),
        "continuations": len(available_cursors),
        "current_context": "unknown",
    }
    records = sorted(
        [*ledger["operations"], *ledger["active"].values()], key=lambda item: item["id"]
    )
    visible_records = [item for item in records if item.get("versions") == versions]
    recent = []
    for item in visible_records[-(MAX_OPERATIONS if detail else 2) :]:
        row = {k: item[k] for k in ("id", "operation", "state", "reserved_steps")}
        if item.get("error"):
            row["error"] = item["error"]
        if detail:
            row.update(
                {
                    k: item[k]
                    for k in ("elapsed_ms", "usage", "usage_known")
                    if k in item
                }
            )
            # Stored memory IDs need individual expiry/ACL revalidation. A only
            # reports their count; the original tool response retains the refs.
            if item["operation"] != "recall":
                row.update(
                    parameters=deepcopy(item["parameters"]),
                    parameters_omitted=item["parameters_omitted"],
                    source_refs=[
                        ref for ref in item["source_refs"] if ref in available
                    ],
                )
                if item["operation"] == "navigate":
                    row["parameters"].pop("memory_ids", None)
                    row["parameters_omitted"] = True
            else:
                row["result_count"] = item.get("result_count", 0)
        else:
            # Never repeat citations or plans in the normal, compact return.
            params = item["parameters"]
            if item["operation"] != "recall":
                label = (
                    params.get("terms")
                    or params.get("action")
                    or params.get("operation")
                )
                if label:
                    row["query"] = str(label)[:80]
        recent.append(row)
    output["recent"] = recent
    if detail:
        output["sources"] = [
            {
                "ref": ref,
                "version": item["source_version"],
                "spans": [list(span) for span in item["spans"]],
                "next_excerpt_offset": unread_offset(item),
                "expires_in_seconds": max(0, round(item["expires_at"] - now, 1)),
            }
            for ref, item in available.items()
        ]
        output["cursors"] = [
            {
                "cursor": ref,
                "query": item["query"],
                "expires_in_seconds": max(0, round(item["expires_at"] - now, 1)),
            }
            for ref, item in available_cursors.items()
        ]
    output["omitted_operations"] = (
        ledger["omitted_operations"] + len(records) - len(recent)
    )
    # Limits apply only to the progress display, never to the original result.
    char_limit = 12000 if detail else 900
    omitted = 0
    for field in ("sources", "cursors", "recent"):
        while output.get(field) and len(json_dumps(output)) > char_limit:
            output[field].pop(0)
            omitted += 1
    if omitted:
        output["display_omitted"] = omitted
        while output.get("recent") and len(json_dumps(output)) > char_limit:
            output["recent"].pop(0)
            output["display_omitted"] += 1
    return output


@lru_cache(maxsize=2)
def request_schema() -> dict[str, Any]:
    return json.loads(
        (CONTRACT_ROOT / "schemas/request.schema.json").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def request_validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(request_schema())


@lru_cache(maxsize=1)
def request_schema_v3() -> dict[str, Any]:
    return json.loads(
        (CONTRACT_ROOT_V3 / "schemas/request.schema.json").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def request_validator_v3():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(request_schema_v3())


def _source_discovery_result_valid(value: Any) -> bool:
    """Validate the owner boundary before exposing a v3 envelope.

    The discovery owner has its own contract and is deliberately kept
    independent from query-session.  This small structural check prevents a
    stale/incorrect handler from being presented as a v3 discovery result and
    avoids retrying the owner merely to repair its shape.
    """
    if not isinstance(value, dict) or value.get("profile") != SOURCE_DISCOVERY_PROFILE:
        return False
    if not isinstance(value.get("ok"), bool) or not isinstance(
        value.get("status"), str
    ):
        return False
    if not isinstance(value.get("error"), str):
        return False
    for key in ("matches", "sources"):
        if not isinstance(value.get(key), list):
            return False
    if not isinstance(value.get("coverage"), dict) or not isinstance(
        value.get("usage"), dict
    ):
        return False
    return True


def _v3_binding_matches(service: Any, event: Any, ctx: Any) -> bool:
    if getattr(event, "_memory_query_v3", False) is not True:
        return False
    expected = getattr(event, "_memory_query_v3_key", None)
    try:
        return expected == service._reconstruction_budget_key(event, ctx)
    except Exception:
        return False


async def execute(
    service: Any,
    event: Any,
    operation: str = "status",
    parameters: Any = None,
    *,
    _notes: bool = False,
    query_note: Any = None,
) -> dict[str, Any]:
    result = {
        "profile": PROFILE,
        "ok": False,
        "operation": operation if operation in (*OPERATIONS, "status") else "status",
        "operation_id": None,
        "error": "",
        "result": None,
        "progress": {"state": "unavailable"},
    }
    if _notes:
        result.update(
            profile=query_notes.PROFILE,
            note_receipt={"status": "not_submitted"},
            notes=query_notes.unavailable_view(),
        )
        if not query_notes.enabled(service):
            return {**result, "error": "query_notes_disabled"}
    if not enabled(service) or service._closing or service._closed:
        return {**result, "error": "query_progress_unavailable"}
    params = {} if parameters is None else parameters
    note_ids = None
    if _notes and operation == "status":
        from jsonschema import Draft202012Validator

        if not Draft202012Validator(query_notes.status_parameters()).is_valid(params):
            return {**result, "error": "invalid_query_arguments"}
        note_ids, params = params.get("note_ids"), {}
    if _notes and query_note is not None and operation != "sources":
        return {**result, "error": "query_note_requires_sources"}
    payload = {"operation": operation, "parameters": params}
    if not request_validator().is_valid(payload):
        return {**result, "error": "invalid_query_arguments"}
    raw_ctx = await service.identity.resolve_event_context(event)
    ctx = service._normalized_session_context(raw_ctx)
    if not service._scope_feature_enabled(ctx, "recall"):
        return {**result, "error": "scope_recall_disabled"}
    offered = getattr(event, "_memory_query_operations", None)
    if offered is not None and (
        offered[0] != service._reconstruction_budget_key(event, ctx)
        or operation not in offered[1]
    ):
        return {**result, "error": "operation_not_offered"}
    if operation == "recall" and not service.config.bool(
        "memory_tools.enable_recall_tool", True
    ):
        return {**result, "error": "recall_tool_disabled"}
    if operation != "recall" and not service._memory_reconstruction_enabled(ctx):
        return {**result, "error": "query_progress_unavailable"}
    if _notes and query_note is not None:
        try:
            result["note_receipt"] = await query_notes.accept(
                service, event, ctx, query_note
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result["note_receipt"] = {
                "status": "unavailable",
                "error": "query_note_unavailable",
            }
    if operation != "status":
        context_token = _CONTEXT.set((service, event, raw_ctx, result))
        try:
            result["result"] = await getattr(service, "tool_" + operation)(
                event, **payload["parameters"]
            )
            result["ok"] = bool(result["result"].get("ok"))
        except asyncio.CancelledError:
            raise
        except Exception:
            result["error"] = "query_failed"
        finally:
            _CONTEXT.reset(context_token)
    else:
        result["ok"] = True
    try:
        if _notes:
            result["progress"] = await progress(
                service,
                event,
                ctx,
                detail=operation == "status",
                notes=result["notes"],
                note_ids=note_ids,
                note_receipt=result["note_receipt"],
            )
            query_notes.bound_display(result, detail=operation == "status")
        else:
            result["progress"] = await progress(
                service, event, ctx, detail=operation == "status"
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Preserve a valid tool result even when optional progress is unavailable.
        result["progress"] = {"state": "unavailable"}
        if _notes:
            result["notes"] = query_notes.unavailable_view()
    return result


async def execute_v3(
    service: Any,
    event: Any,
    operation: str = "status",
    parameters: Any = None,
    *,
    _notes: bool = False,
    query_note: Any = None,
) -> dict[str, Any]:
    """Execute the request-local v3 envelope.

    v3 is an additive boundary for the semantic source-discovery owner.  It
    uses the same reconstruction state as v1/v2; the owner remains the only
    place that reserves a step, so wrapping it here cannot double-charge the
    turn.  Existing operations keep their existing owner implementations and
    result bodies.
    """
    allowed_operations = (*OPERATIONS_V3, "status")
    normalized_operation = operation if operation in allowed_operations else "status"
    result: dict[str, Any] = {
        "profile": PROFILE_V3,
        "ok": False,
        "operation": normalized_operation,
        "operation_id": None,
        "error": "",
        "result": None,
        "progress": {"state": "unavailable"},
    }
    if _notes:
        result.update(
            note_receipt={"status": "not_submitted"},
            notes=query_notes.unavailable_view(),
        )
        if not query_notes.enabled(service):
            return {**result, "error": "query_notes_disabled"}
    if not enabled(service) or service._closing or service._closed:
        return {**result, "error": "query_progress_unavailable"}

    params = {} if parameters is None else parameters
    note_ids = None
    if _notes and normalized_operation == "status":
        from jsonschema import Draft202012Validator

        if not Draft202012Validator(query_notes.status_parameters()).is_valid(params):
            return {**result, "error": "invalid_query_arguments"}
        note_ids, params = params.get("note_ids"), {}
    if _notes and query_note is not None and normalized_operation != "sources":
        return {**result, "error": "query_note_requires_sources"}
    payload = {"operation": operation, "parameters": params}
    if not request_validator_v3().is_valid(payload):
        return {**result, "error": "invalid_query_arguments"}

    raw_ctx = await service.identity.resolve_event_context(event)
    ctx = service._normalized_session_context(raw_ctx)
    if not service._scope_feature_enabled(ctx, "recall"):
        return {**result, "error": "scope_recall_disabled"}
    if normalized_operation == "discover" and not _v3_binding_matches(
        service, event, ctx
    ):
        return {**result, "error": "query_v3_unavailable"}
    offered = getattr(event, "_memory_query_operations", None)
    if offered is not None and (
        offered[0] != service._reconstruction_budget_key(event, ctx)
        or normalized_operation not in offered[1]
    ):
        return {**result, "error": "operation_not_offered"}
    if normalized_operation == "recall" and not service.config.bool(
        "memory_tools.enable_recall_tool", True
    ):
        return {**result, "error": "recall_tool_disabled"}
    if normalized_operation != "recall" and not service._memory_reconstruction_enabled(
        ctx
    ):
        return {**result, "error": "query_progress_unavailable"}
    if _notes and query_note is not None:
        try:
            result["note_receipt"] = await query_notes.accept(
                service, event, ctx, query_note
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            result["note_receipt"] = {
                "status": "unavailable",
                "error": "query_note_unavailable",
            }

    if normalized_operation != "status":
        context_token = _CONTEXT.set((service, event, raw_ctx, result))
        try:
            owner_name = (
                "tool_discover_sources"
                if normalized_operation == "discover"
                else "tool_" + normalized_operation
            )
            owner = getattr(service, owner_name, None)
            if owner is None:
                result["error"] = "query_v3_unavailable"
            else:
                owner_result = await owner(event, **payload["parameters"])
                from .source_query_v2 import SOURCE_QUERY_PROFILE_V2, is_batch_request

                batch_requested = (
                    normalized_operation == "sources"
                    and is_batch_request(payload["parameters"])
                )
                batch_valid = (
                    isinstance(owner_result, dict)
                    and owner_result.get("profile") == SOURCE_QUERY_PROFILE_V2
                    and isinstance(owner_result.get("sources"), list)
                    and all(
                        isinstance(owner_result.get(key), dict)
                        for key in ("batch", "coverage", "usage")
                    )
                    and isinstance(owner_result.get("ok"), bool)
                    and isinstance(owner_result.get("error"), str)
                    and owner_result.get("status")
                    in {"batch", "partial", "empty", "rejected"}
                )
                if (
                    normalized_operation == "discover"
                    and not _source_discovery_result_valid(owner_result)
                ) or (batch_requested and not batch_valid):
                    # Do not invoke the owner again to repair a mismatched
                    # profile. The single invocation is already accounted for
                    # by track_query and its progress ledger.
                    result["error"] = "owner_profile_mismatch"
                else:
                    result["result"] = owner_result
                    result["ok"] = (
                        bool(owner_result.get("ok"))
                        if isinstance(owner_result, dict)
                        else False
                    )
                    if not isinstance(owner_result, dict):
                        result["error"] = "owner_result_invalid"
        except asyncio.CancelledError:
            raise
        except Exception:
            result["error"] = "query_failed"
        finally:
            _CONTEXT.reset(context_token)
    else:
        result["ok"] = True

    try:
        if _notes:
            result["progress"] = await progress(
                service,
                event,
                ctx,
                detail=normalized_operation == "status",
                notes=result["notes"],
                note_ids=note_ids,
                note_receipt=result["note_receipt"],
            )
            query_notes.bound_display(result, detail=normalized_operation == "status")
        else:
            result["progress"] = await progress(
                service, event, ctx, detail=normalized_operation == "status"
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        result["progress"] = {"state": "unavailable"}
        if _notes:
            result["notes"] = query_notes.unavailable_view()
    return result
