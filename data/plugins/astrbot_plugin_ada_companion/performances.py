"""Background performances share verified ownership across chat entry points."""

import asyncio
import json
import math
from pathlib import Path

from astrbot.api.message_components import File

from .performance_client import PerformanceClient
from .performance_delivery import PerformanceDelivery
from .performance_files import snapshot
from .performance_store import PerformanceError, PerformanceStore
from .performance_worker import PerformanceWorker


def normalized_parameters(parameters):
    start, pitch = float(parameters["start_seconds"]), float(parameters["pitch"])
    duration = parameters["duration_seconds"]
    duration = float(duration) if duration is not None else None
    if not (
        math.isfinite(start)
        and start >= 0
        and -12 <= pitch <= 12
        and pitch == int(pitch)
        and isinstance(parameters["acapella"], bool)
        and isinstance(parameters["source_url"], str)
    ):
        raise PerformanceError("invalid_performance_parameters")
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise PerformanceError("invalid_performance_parameters")
    return {
        **parameters,
        "pitch": int(pitch),
        "start_seconds": start,
        "duration_seconds": duration,
        "source_url": parameters["source_url"].strip(),
    }


def public(request):
    job = request["generation"] or {}
    return {
        "request_id": request["id"],
        "title": request["title"],
        "generation": {
            k: job.get(k) for k in ("id", "state", "stage", "done", "total", "error")
        },
        "delivery": request["state"],
        "delivery_content": (
            "failure_notice"
            if request["artifact"]["format"] == "text"
            else "performance"
        )
        if request["artifact"]
        else None,
        "receipt": request["receipt"],
        "cancel_requested": request["cancel_requested"],
        "error": request["error"],
        "observed_at": request["updated"],
    }


class Performances:
    def __init__(self, folder: Path, config, owners, context):
        self.root = folder / "performances"
        self.config, self.owners = config, owners
        self.store = PerformanceStore(self.root / "requests.sqlite3")
        self.client = PerformanceClient(config)
        self.worker = PerformanceWorker(
            self.root,
            self.store,
            self.client,
            PerformanceDelivery(context, owners),
            config,
        )

    async def submit(self, event, title, parameters, use_voice_recording=False):
        try:
            return await self._submit(event, title, parameters, use_voice_recording)
        except PerformanceError as error:
            return json.dumps(
                {
                    "ok": False,
                    "reason": str(error),
                    "next": "Correct the source or settings before submitting again.",
                }
            )
        except (TypeError, ValueError):
            return '{"ok":false,"reason":"invalid_performance_parameters"}'

    async def _submit(self, event, title, parameters, use_voice_recording):
        owner = self.owners.scope(event)
        if not self.config.get("voice_enabled", False):
            raise PerformanceError("voice_disabled")
        message_id = getattr(event.message_obj, "message_id", None)
        if not message_id:
            raise PerformanceError("missing_native_message_id")
        parameters = normalized_parameters(parameters)
        files = [item for item in event.get_messages() if isinstance(item, File)]
        if files:
            parameters.update(source_path=await files[0].get_file(), source_url="")
        elif use_voice_recording and event.get_extra("voice_input_files"):
            parameters.update(
                source_path=event.get_extra("voice_input_files")[0], source_url=""
            )
        elif not parameters["source_url"]:
            raise PerformanceError("source_required")
        elif not parameters["source_url"].startswith(("http://", "https://")):
            raise PerformanceError("invalid_performance_source_url")
        payload, identity = await asyncio.to_thread(snapshot, self.root, parameters)
        title = "".join(c for c in title if c.isprintable()).strip()[:64] or "翻唱"
        request = self.store.submit(
            owner,
            {"origin": event.unified_msg_origin, "platform": event.get_platform_name()},
            message_id,
            title,
            payload,
            identity,
        )
        self.worker.wake.set()
        event.set_extra("ada_voice_done", True)
        return json.dumps(
            {
                "ok": True,
                **public(request),
                "next": "The performance is durably queued. Continue chatting; delivery returns to this original chat. "
                "Use ada_performance to query or cancel. Do not claim it is generated, sent or played yet.",
            },
            ensure_ascii=False,
        )

    async def control(self, event, action="status", request_id=""):
        owner = self.owners.scope(event)
        if action not in {"status", "cancel"}:
            raise PerformanceError("unknown_performance_action")
        request = (
            self.store.cancel(owner, request_id)
            if action == "cancel"
            else self.store.get(owner, request_id)
        )
        self.worker.wake.set()
        if action == "status" and request["job_id"]:
            try:
                job = await self.client.status(request)
            except PerformanceError:
                result = public(request)
                result["generation_observation"] = "cached_gateway_unavailable"
            else:
                result = public({**request, "generation": job})
                result["generation_observation"] = "live"
        else:
            result = public(request)
        result["meaning"] = (
            "Generation, channel acceptance and playback are separate. Unknown delivery is never automatically resent."
        )
        return json.dumps(result, ensure_ascii=False)
