"""Read explicitly linked private conversations without rewriting their provenance."""

import asyncio
import hashlib
import json
from pathlib import Path


class OwnerMemory:
    def __init__(self, path: Path, owners: list[str]):
        self.path = path
        self.owners = tuple(dict.fromkeys(owners))
        self.contexts = {}
        self.healthy = True
        if path.exists():
            try:
                self.contexts = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(self.contexts, dict) or any(
                    not isinstance(v, dict) for v in self.contexts.values()
                ):
                    raise ValueError("Invalid owner identity file")
            except (ValueError, OSError):
                self.contexts = {}
                self.healthy = False
        self.last = {}

    def observe(self, event):
        """Persist native identities only for locally configured private owners.

        Args:
            event: A real incoming platform event.
        """
        origin = event.unified_msg_origin
        if not self.healthy or origin not in self.owners or not event.is_private_chat():
            return
        context = {
            "session_id": origin,
            "scope": "private",
            "platform": event.get_platform_name(),
            "user_id": str(event.get_sender_id() or ""),
            "bot_id": str(event.get_self_id() or ""),
            "strict_session_only": True,
        }
        if not context["bot_id"] or context["user_id"] != origin.split(":", 2)[-1]:
            return
        previous = self.contexts.get(origin, {})
        aliases = [previous.get("bot_id", ""), *previous.get("bot_id_aliases", [])]
        aliases = [x for x in dict.fromkeys(aliases) if x and x != context["bot_id"]]
        if aliases:
            context["bot_id_aliases"] = aliases[:1]
        if self.contexts.get(origin) != context:
            self.contexts[origin] = context
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self.contexts, ensure_ascii=False), encoding="utf-8"
            )
            temp.replace(self.path)

    @property
    def shared_scope(self):
        return hashlib.sha256(b"owner").hexdigest()[:24]

    def scope(self, event):
        """Return one shared tool-state key for the verified private owner.

        Args:
            event: A platform event; the model cannot select the account.

        Returns:
            The shared owner's stable key, or an isolated admin test key.

        Raises:
            PermissionError: The sender is not a configured private owner/admin.
        """
        origin = event.unified_msg_origin
        owner = origin in self.owners
        administrator = (
            event.get_platform_name() == "webchat"
            and str(event.get_sender_id()) == "astrbot"
        )
        if not event.is_private_chat() or not (owner or administrator):
            raise PermissionError("private_owner_only")
        principal = "owner" if owner else "admin:" + origin
        return hashlib.sha256(principal.encode()).hexdigest()[:24]

    def context_variants(self, peer):
        """Validate a configured identity once for all native memory readers.

        Args:
            peer: A configured private session, never a model-selected identity.

        Returns:
            Native contexts for the observed bot and its verified historical alias.

        Raises:
            ValueError: The stored identity is absent, invalid, or unreadable.
        """
        ctx = self.contexts.get(peer, {})
        if (
            not self.healthy
            or peer not in self.owners
            or ctx.get("session_id") != peer
            or ctx.get("scope") != "private"
            or ctx.get("user_id") != peer.split(":", 2)[-1]
            or not ctx.get("bot_id")
            or not ctx.get("platform")
        ):
            raise ValueError("identity_not_observed")
        aliases = ctx.get("bot_id_aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(x, str) for x in aliases
        ):
            raise ValueError("identity_not_observed")
        native = {k: v for k, v in ctx.items() if k != "bot_id_aliases"}
        return [
            {**native, "bot_id": bot, "strict_session_only": True}
            for bot in dict.fromkeys([ctx["bot_id"], *aliases[:1]])
        ]

    async def compose(
        self, origin, query, bridge, conversations, *, include_current=False
    ):
        """Retrieve the owner's other channels through their native memory scopes.

        Args:
            origin: Verified current private-chat origin, never an LLM argument.
            query: Current user message or an explicit recall query.
            bridge: MemoryCompanion's permission-aware read API.
            conversations: AstrBot's conversation manager.
            include_current: Include this channel in the same shared result.

        Returns:
            Bounded source-labelled data, with explicit partial-read failures.
        """
        if origin not in self.owners:
            return {"ok": False, "error": "unlinked_session", "sources": []}
        sources, errors = [], []
        for peer in self.owners:
            if peer == origin and not include_current:
                continue
            try:
                variants = self.context_variants(peer)
            except ValueError:
                errors.append(
                    {"channel": peer.split(":", 1)[0], "error": "identity_not_observed"}
                )
                continue
            source = {"channel": peer.split(":", 1)[0], "memories": "", "recent": []}
            if bridge is not None:
                chunks = []
                for native_ctx in variants:
                    try:
                        chunk = await asyncio.wait_for(
                            bridge.compose_context(
                                query=query[:1200],
                                session_context=native_ctx,
                                top_k=3,
                                max_chars=1600,
                            ),
                            timeout=8,
                        )
                        if chunk and chunk not in chunks:
                            chunks.append(chunk[:1600])
                    except Exception as exc:
                        errors.append(
                            {"channel": source["channel"], "error": type(exc).__name__}
                        )
                source["memories"] = "\n\n".join(chunks)
            else:
                errors.append(
                    {"channel": source["channel"], "error": "memory_unavailable"}
                )
            try:
                cid = await conversations.get_curr_conversation_id(peer)
                conversation = (
                    await conversations.get_conversation(peer, cid) if cid else None
                )
                if conversation:
                    source["conversation_updated_at"] = conversation.updated_at
                    history = json.loads(conversation.history or "[]")
                    budget = 1800
                    for turn in reversed(history):
                        content = turn.get("content")
                        if turn.get("role") not in ("user", "assistant"):
                            continue
                        if isinstance(content, list):
                            content = "\n".join(
                                x.get("text", "")
                                for x in content
                                if isinstance(x, dict)
                                and x.get("type") in ("text", "input_text")
                            )
                        if not isinstance(content, str) or not content.strip():
                            continue
                        text = content[: min(budget, 700)]
                        source["recent"].append({"role": turn["role"], "text": text})
                        budget -= len(text)
                        if budget <= 0 or len(source["recent"]) >= 6:
                            break
                    source["recent"].reverse()
            except Exception as exc:
                errors.append(
                    {"channel": source["channel"], "error": type(exc).__name__}
                )
            if source["memories"] or source["recent"]:
                sources.append(source)
        self.last = {
            "source_channels": [x["channel"] for x in sources],
            "errors": errors,
        }
        return {"ok": not errors, "sources": sources, "errors": errors}
