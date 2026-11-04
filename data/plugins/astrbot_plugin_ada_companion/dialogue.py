"""Owner-scoped working state; JSON is a read-only migration source."""

import hashlib
import time
from importlib import import_module
from pathlib import Path


class DialogueState:
    def __init__(self, path: Path, owners, services):
        self.path, self.owners, self.services = path, owners, services
        self.healthy = False
        self.last = {"error": "not_read"}

    def _identity(self, origin, event):
        if not self.owners.healthy:
            raise ValueError("working_identity_unavailable")
        if event is not None:
            owner = self.owners.scope(event)
            if event.unified_msg_origin != origin or event.get_extra(
                "ada_web_read_active"
            ):
                raise PermissionError("private_owner_only")
        elif origin not in self.owners.owners:
            raise PermissionError("private_owner_only")
        else:
            owner = self.owners.shared_scope
        if origin in self.owners.owners:
            origins = tuple(self.owners.owners)
            scopes = [
                ctx for peer in origins for ctx in self.owners.context_variants(peer)
            ]
        else:
            if event is None:
                raise PermissionError("private_owner_only")
            origins = (origin,)
            scopes = [
                {
                    "session_id": origin,
                    "platform": event.get_platform_name(),
                    "user_id": str(event.get_sender_id()),
                    "bot_id": str(event.get_self_id()),
                }
            ]
        return owner, origins, scopes

    async def _access(self, origin, event):
        identity = self._identity(origin, event)
        bridge = self.services.memory()
        store = getattr(getattr(bridge, "_plugin", None), "store", None)
        if store is None:
            raise RuntimeError("working_memory_unavailable")
        # Native plugins use data.plugins; bind helpers to the live store package.
        package = type(store).__module__.rpartition(".")[0]
        WorkingState = import_module(".working_state", package).WorkingState
        adopt = import_module(".working_state_import", package).adopt

        def authorize():
            return (
                getattr(getattr(self.services.memory(), "_plugin", None), "store", None)
                is store
                and self._identity(origin, event) == identity
                and (event is None or not event.is_stopped())
            )

        owner, origins, scopes = identity
        state = WorkingState(store)
        await adopt(state, self.path, owner, origins, authorize)
        return state, owner, scopes, authorize

    def _result(self, result):
        self.healthy = result.get("ok", False)
        self.last = {k: v for k, v in result.items() if k not in {"items", "history"}}
        return result

    def _failure(self, exc):
        return self._result(
            {
                "ok": False,
                "error": str(exc)
                if isinstance(exc, (ValueError, PermissionError, RuntimeError))
                else type(exc).__name__,
            }
        )

    async def snapshot(self, origin, *, event=None, **query):
        try:
            state, owner, _, authorize = await self._access(origin, event)
            result = await state.snapshot(owner, authorize=authorize, **query)
            if event is not None:
                previous = event.get_extra("ada_working_observed", {})
                event.set_extra(
                    "ada_working_observed",
                    {**previous, **{r["id"]: r for r in result["items"]}},
                )
            return self._result(result)
        except Exception as exc:
            return self._failure(exc)

    async def visible(self, origin, now=None, *, event=None):
        result = await self.snapshot(origin, event=event, now=now)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return result["items"]

    async def update(self, event, *, action, expected_version=None, now=None, **change):
        try:
            state, owner, scopes, authorize = await self._access(
                event.unified_msg_origin, event
            )
            source = state.store.core_event_source(event)
            change = {**change, "action": action}
            if expected_version is None:
                expected_version = self._observed_version(event, change)
            change["expected_version"] = expected_version
            result = await state.change(
                owner=owner,
                change=change,
                source=source,
                scopes=scopes,
                authorize=authorize,
                now=now,
            )
            return self._result(result)
        except Exception as exc:
            return self._failure(exc)

    @staticmethod
    def _observed_version(event, change):
        observed = event.get_extra("ada_working_observed", {})
        item = observed.get(change.get("item_id"))
        if not item and not change.get("item_id"):
            matches = [
                r
                for r in observed.values()
                if (not change.get("kind") or r["kind"] == change["kind"])
                and r["title"].casefold() == change.get("title", "").strip().casefold()
            ]
            item = matches[0] if len(matches) == 1 else None
            if item:
                change["item_id"] = item["id"]
        return item["version"] if item else ""

    async def history(self, event, item_id, before=0):
        try:
            state, owner, _, authorize = await self._access(
                event.unified_msg_origin, event
            )
            return self._result(
                await state.history(owner, item_id, authorize=authorize, before=before)
            )
        except Exception as exc:
            return self._failure(exc)

    async def apply_updates(self, event, updates):
        """Apply optional native reply intents against the original prompt snapshot."""
        results = []
        if not isinstance(updates, list) or event.is_stopped():
            return results
        for update in updates[:2]:
            fields = ("item_id", "title", "content", "evidence_quote")
            if (
                not isinstance(update, dict)
                or update.get("action") not in {"save", "resolve", "cancel"}
                or any(not isinstance(update.get(k, ""), str) for k in fields)
            ):
                results.append({"ok": False, "error": "invalid_working_intent"})
                continue
            results.append(
                await self.update(
                    event,
                    action=update["action"],
                    kind=update.get("kind", ""),
                    **{k: update.get(k, "") for k in fields},
                )
            )
        event.set_extra("ada_working_receipts", results)
        return results

    async def manage(self, origin, item_id, expected_version):
        try:
            state, owner, scopes, authorize = await self._access(origin, None)
            quote = "Authenticated management: close this working object."
            source = {
                "kind": "management",
                "text": quote,
                "origin": origin,
                "received_ns": time.time_ns(),
                "request_id": hashlib.sha256(
                    (owner + item_id + expected_version).encode()
                ).hexdigest(),
            }
            return self._result(
                await state.change(
                    owner=owner,
                    source=source,
                    scopes=scopes,
                    authorize=authorize,
                    change={
                        "action": "resolve",
                        "item_id": item_id,
                        "expected_version": expected_version,
                        "evidence_quote": quote,
                    },
                )
            )
        except Exception as exc:
            return self._failure(exc)
