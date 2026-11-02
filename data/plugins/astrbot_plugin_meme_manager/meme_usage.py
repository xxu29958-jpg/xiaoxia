"""Persistent sticker preferences and bounded delivery history, scoped by person."""

import hashlib
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

AUTO_MIN_SECONDS = 5 * 60
AUTO_MIN_TURN_GAP = 3  # Two intervening replies before the next eligible one.
NEW_CHAT_GAP_SECONDS = 30 * 60


def event_scope(event):
    """Use a trusted integration identity, otherwise keep native chats isolated."""
    return str(event.get_extra("meme_manager_scope") or event.unified_msg_origin)


class MemeUsage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS preferences (
                    scope TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS blocked (
                    scope TEXT, digest TEXT, at REAL, PRIMARY KEY(scope,digest));
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, scope TEXT, digest TEXT, at REAL, state TEXT);
                CREATE INDEX IF NOT EXISTS delivery_scope ON deliveries(scope,at);
                CREATE TABLE IF NOT EXISTS reaction_cadence (
                    scope TEXT PRIMARY KEY, turn INTEGER NOT NULL DEFAULT 0,
                    last_key TEXT NOT NULL DEFAULT '', image_turn INTEGER NOT NULL DEFAULT -3,
                    image_at REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO reaction_cadence(scope, image_turn, image_at)
                    SELECT scope, 0, MAX(at) FROM deliveries WHERE state!='failed' GROUP BY scope;
            """)

    def observe_event(self, event):
        """Count one reply turn once across hooks, using the shared owner identity.

        Args:
            event: Native event, retaining its turn ticket through all reply hooks.

        Returns:
            Durable sequence number used to reject stale or adjacent reactions.
        """
        cached = event.get_extra("meme_manager_reaction_turn")
        if isinstance(cached, int):
            return cached
        message_id = getattr(getattr(event, "message_obj", None), "message_id", "")
        key = event.unified_msg_origin + ":" + str(message_id or uuid.uuid4().hex)
        scope = event_scope(event)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO reaction_cadence(scope) VALUES (?)", (scope,)
            )
            db.execute(
                "UPDATE reaction_cadence SET turn=turn+1,last_key=? WHERE scope=? AND last_key!=?",
                (key, scope, key),
            )
            turn = db.execute(
                "SELECT turn FROM reaction_cadence WHERE scope=?", (scope,)
            ).fetchone()[0]
        event.set_extra("meme_manager_reaction_turn", turn)
        return turn

    @staticmethod
    def _automatic_ready(db, scope, turn=None):
        state = db.execute(
            "SELECT turn,image_turn,image_at FROM reaction_cadence WHERE scope=?",
            (scope,),
        ).fetchone()
        current, last_turn, last_at = state or (0, -3, 0)
        if turn is not None and turn != current:
            return False
        # Include earlier deliveries when upgrading an existing installation.
        latest = db.execute(
            "SELECT MAX(at) FROM deliveries WHERE scope=? AND state!='failed'", (scope,)
        ).fetchone()[0]
        elapsed = time.time() - max(last_at, latest or 0)
        return elapsed >= AUTO_MIN_SECONDS and (
            elapsed >= NEW_CHAT_GAP_SECONDS or current - last_turn >= AUTO_MIN_TURN_GAP
        )

    def automatic_ready(self, scope, turn=None):
        with closing(sqlite3.connect(self.path)) as db:
            return self._automatic_ready(db, scope, turn)

    @staticmethod
    def _state(db, scope):
        row = db.execute(
            "SELECT enabled FROM preferences WHERE scope=?", (scope,)
        ).fetchone()
        blocked = {
            x[0]
            for x in db.execute("SELECT digest FROM blocked WHERE scope=?", (scope,))
        }
        recent = list(
            db.execute(
                "SELECT id,digest,at,state FROM deliveries WHERE scope=? AND at>? "
                "AND state!='failed' ORDER BY at DESC LIMIT 8",
                (scope, time.time() - 86400),
            )
        )
        return {
            "enabled": not row or bool(row[0]),
            "blocked": sorted(blocked),
            "recent": [dict(zip(("id", "digest", "at", "state"), x)) for x in recent],
        }

    def status(self, scope):
        with closing(sqlite3.connect(self.path)) as db:
            return self._state(db, scope)

    def exclusions(self, scope):
        state = self.status(scope)
        return set(state["blocked"]) | {x["digest"] for x in state["recent"]}

    def set_enabled(self, scope, enabled):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES (?,?)",
                (scope, bool(enabled)),
            )

    def block(self, scope, digest, blocked=True):
        if len(digest) != 64 or any(x not in "0123456789abcdef" for x in digest):
            raise ValueError("Invalid image digest")
        with closing(sqlite3.connect(self.path)) as db, db:
            if blocked:
                db.execute(
                    "INSERT OR REPLACE INTO blocked VALUES (?,?,?)",
                    (scope, digest, time.time()),
                )
            else:
                db.execute(
                    "DELETE FROM blocked WHERE scope=? AND digest=?", (scope, digest)
                )

    def block_latest(self, scope):
        state = self.status(scope)
        if not state["recent"]:
            return False
        self.block(scope, state["recent"][0]["digest"])
        return True

    def claim(self, scope, digest, *, automatic=False, turn=None):
        """Atomically reserve an image immediately before actual dispatch.

        Args:
            scope: Trusted shared owner or isolated native conversation identity.
            digest: Hash of the verified image.
            automatic: Enforce spacing for an unsolicited reaction.
            turn: Native reply ticket; reject automatic reactions from older turns.

        Returns:
            Reservation token, or None for a disabled, repeated or premature image.
            Explicit requests bypass only automatic spacing. Every reservation,
            including an uncertain delivery, restarts the automatic quiet interval.
        """
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db, scope)
            if (
                not state["enabled"]
                or digest in state["blocked"]
                or any(x["digest"] == digest for x in state["recent"])
                or (automatic and not self._automatic_ready(db, scope, turn))
            ):
                return None
            token = uuid.uuid4().hex
            db.execute(
                "INSERT INTO deliveries VALUES (?,?,?,?,?)",
                (token, scope, digest, time.time(), "uncertain"),
            )
            db.execute("DELETE FROM deliveries WHERE at<?", (time.time() - 30 * 86400,))
            db.execute(
                "INSERT OR IGNORE INTO reaction_cadence(scope) VALUES (?)", (scope,)
            )
            db.execute(
                "UPDATE reaction_cadence SET image_turn=turn,image_at=? WHERE scope=?",
                (time.time(), scope),
            )
            return token

    def finish(self, token, sent):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE deliveries SET state=? WHERE id=?",
                ("sent" if sent else "uncertain", token),
            )

    def claim_event(self, event, selected_id="", *, path=None):
        record = (event.get_extra("meme_manager_semantic_candidates") or {}).get(
            selected_id, {}
        )
        digest = record.get("content_sha256", "")
        if path is not None:
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        reaction = event.get_extra("meme_manager_reaction") or {}
        if event.get_extra("meme_manager_suppress") or not digest:
            return None
        if reaction.get("action") not in {"requested", "react"}:
            return None
        token = self.claim(
            event_scope(event),
            digest,
            automatic=reaction["action"] == "react",
            turn=event.get_extra("meme_manager_reaction_turn"),
        )
        if token:
            event.set_extra(
                "meme_manager_delivery_tokens",
                [*(event.get_extra("meme_manager_delivery_tokens") or []), token],
            )
        return token
