"""Persistent delivery policy, independent of the chat framework."""

import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=8))


def wants_voice(text: str, enabled: bool = False) -> bool:
    lowered = text.lower()
    # Accept this common phone-input typo only in an explicit speaking request.
    lowered = re.sub(r"用语言(?=\s*(?:说|回|讲|聊))", "用语音", lowered)
    if re.search(
        r"(?:不要|别|不用|不需要)[^，。！？,.;!?\n]{0,5}(?:语音|声音|读出来)|(?:只|仅).{0,2}(?:文字|打字)|(?:no|without|don't|do not)\s+(?:send\s+|use\s+)?(?:a\s+)?(?:voice|audio)|text only",
        lowered,
    ):
        return False
    return enabled or bool(
        re.search(
            r"(?:用|发|来|给我|听).{0,8}语音|语音(?:回复|说|聊)|(?:想听|听听|听一下).{0,6}声音|(?:给我|帮我|请).{0,4}(?:读出来|念出来)|voice (?:note|message|reply)|(?:say|read).{0,100}out loud",
            lowered,
        )
    )


def speech_text(text: str) -> str:
    text = re.sub(r"&&[^&\n]+&&", "", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"!?(\[([^\]]*)\])\([^)]*\)", r"\2", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#_`]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class DeliveryPolicy:
    """Reserve before network I/O; uncertain sends consume quota after restart."""

    def __init__(self, path: Path, target: str, now: float | None = None):
        self.path = path
        self.target = target
        self.healthy = True
        now = time.time() if now is None else now
        self.data = {
            "version": 1,
            "target": target,
            "enabled": True,
            "channels": ["wechat"],
            "channel_revision": 0,
            "last_user": now,
            "next_due": now + 150 * 60,
            "unanswered": 0,
            "deliveries": [],
            "voice_sessions": {},
            "closing_until": 0,
        }
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("Invalid delivery state")
                if loaded.get("version") != 1 or loaded.get("target") != target:
                    raise ValueError("State identity mismatch")
                for key in ("last_user", "next_due", "closing_until"):
                    value = loaded.get(key, self.data[key])
                    if not isinstance(value, (float, int)) or not math.isfinite(value):
                        raise ValueError("Invalid delivery timestamp")
                if (
                    not isinstance(loaded.get("enabled"), bool)
                    or not isinstance(loaded.get("unanswered"), int)
                    or loaded["unanswered"] < 0
                    or not isinstance(loaded.get("voice_sessions", {}), dict)
                    or not isinstance(loaded.get("deliveries"), list)
                ):
                    raise ValueError("Invalid delivery state fields")
                for item in loaded["deliveries"]:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("at"), (int, float))
                        or not math.isfinite(item["at"])
                        or not isinstance(item.get("text"), str)
                    ):
                        raise ValueError("Invalid delivery reservation")
                channels = loaded.get("channels", ["wechat"])
                revision = loaded.get("channel_revision", 0)
                if (
                    not isinstance(channels, list)
                    or any(channel not in ("wechat", "qq") for channel in channels)
                    or len(channels) != len(set(channels))
                    or type(revision) is not int
                    or revision < 0
                ):
                    raise ValueError("Invalid contact channel state")
                self.data.update(loaded)
            except (ValueError, OSError):
                self.healthy = False
                self.data["enabled"] = False
        else:
            self.save()

    def save(self):
        if not self.healthy:
            raise RuntimeError("Proactive state is unreadable; refusing to overwrite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.flush()
            import os

            os.fsync(f.fileno())
        temp.replace(self.path)

    def daily_count(self, now: float) -> int:
        day = datetime.fromtimestamp(now, LOCAL_TZ).date()
        return sum(
            datetime.fromtimestamp(x["at"], LOCAL_TZ).date() == day
            for x in self.data["deliveries"]
        )

    def set_channels(self, channels: list[str]):
        """Persist channel preferences atomically without resetting shared limits.

        Args:
            channels: Ordered, unique channel labels resolved by the owner controls.

        Raises:
            ValueError: A requested channel is unsupported or repeated.
            OSError: The durable update failed; the old preferences remain active.
            RuntimeError: Existing state is unreadable.
        """
        if any(name not in ("wechat", "qq") for name in channels) or len(
            channels
        ) != len(set(channels)):
            raise ValueError("Invalid contact channels")
        previous = self.data
        self.data = {
            **previous,
            "channels": list(channels),
            "channel_revision": previous["channel_revision"] + 1,
        }
        try:
            self.save()
        except Exception:
            self.data = previous
            raise

    def status(self, configured: bool) -> dict:
        """Return current delivery state without mutating it.

        Args:
            configured: Whether the installation enables proactive delivery.

        Returns:
            The shared state consumed by chat controls and admin inspection.
        """
        now = time.time()
        return {
            "enabled": bool(
                configured
                and self.healthy
                and self.data["enabled"]
                and self.data["channels"]
            ),
            "gate": self.gate(now)
            if configured or not self.healthy
            else "config_disabled",
            "today_count": self.daily_count(now) if self.healthy else 0,
            "channels": list(self.data["channels"]),
        }

    def gate(self, now: float, *, manual: bool = False) -> str:
        if not self.healthy:
            return "state_unreadable"
        if not self.target.startswith("wechat-companion:FriendMessage:"):
            return "invalid_target"
        if not self.data["enabled"]:
            return "paused"
        if not self.data["channels"]:
            return "no_channels"
        if not 9 <= datetime.fromtimestamp(now, LOCAL_TZ).hour < 23:
            return "quiet_hours"
        if self.daily_count(now) >= 4:
            return "daily_limit"
        if (
            self.data["unanswered"] >= 2
            and self.data["deliveries"]
            and now - self.data["deliveries"][-1]["at"] < 24 * 3600
        ):
            return "awaiting_reply"
        if not manual:
            if now < self.data.get("closing_until", 0):
                return "conversation_closed"
            if now - self.data["last_user"] < 45 * 60:
                return "recent_conversation"
            if now < self.data["next_due"]:
                return "cooldown"
        elif (
            self.data["deliveries"] and now - self.data["deliveries"][-1]["at"] < 5 * 60
        ):
            return "manual_cooldown"
        return "ready"

    def on_user(self, target: str, now: float, message: str = ""):
        if target != self.target or not self.healthy:
            return
        self.data["last_user"] = now
        self.data["unanswered"] = 0
        self.data["closing_until"] = 0
        # Narrow full-message matches avoid interpreting quotations as departures.
        closing = message.strip().lower().rstrip("。.!！~～")
        if re.fullmatch(
            r"(?:那|好|嗯|行)?[，, ]*(?:我(?:先|要)?(?:去)?(?:睡了|睡觉了)|晚安|good ?night|going to bed)(?:[，, ]*(?:晚安|睡了|明天聊))?",
            closing,
        ):
            current = datetime.fromtimestamp(now, LOCAL_TZ)
            morning = current.replace(hour=9, minute=0, second=0, microsecond=0)
            if morning <= current:
                morning += timedelta(days=1)
            self.data["closing_until"] = morning.timestamp()
        elif re.fullmatch(
            r"(?:那|好|嗯|行)?[，, ]*(?:我(?:先)?(?:去)?(?:忙了|忙会儿?|洗澡了|吃饭了)|先忙|晚点聊|回头聊|gotta go|talk later)(?:[，, ]*(?:晚点聊|回头聊|拜拜|bye))?",
            closing,
        ):
            self.data["closing_until"] = now + 2 * 3600
        self.data["next_due"] = max(self.data["next_due"], now + 45 * 60)
        self.save()

    def duplicate(self, text: str) -> bool:
        normalized = re.sub(r"\W", "", text.casefold())
        return any(
            SequenceMatcher(
                None, normalized, re.sub(r"\W", "", x.get("text", "").casefold())
            ).ratio()
            > 0.8
            for x in self.data["deliveries"][-8:]
        )

    def reserve(
        self,
        text: str,
        now: float,
        *,
        manual: bool = False,
        target: str = "",
        meme_sha256: str = "",
        thread_id: str = "",
        material: dict | None = None,
    ) -> dict:
        reason = self.gate(now, manual=manual)
        if reason != "ready" or self.duplicate(text):
            raise ValueError(reason if reason != "ready" else "duplicate")
        item = {
            "id": uuid.uuid4().hex,
            "at": now,
            "text": text,
            "status": "reserved",
            "memory_id": "",
            "target": target or self.target,
            "meme_sha256": meme_sha256,
            "thread_id": thread_id,
            "material_key": material["key"] if material else "",
            "source_url": material["url"] if material else "",
            "source_title": material["title"] if material else "",
        }
        previous = self.data
        self.data = {**previous, "deliveries": list(previous["deliveries"])}
        self.data["deliveries"] = [
            x for x in self.data["deliveries"] if x["at"] > now - 30 * 86400
        ]
        self.data["deliveries"].append(item)
        self.data["unanswered"] += 1
        self.data["next_due"] = now + 180 * 60
        try:
            self.save()
        except Exception:
            self.data = previous
            raise
        return item
