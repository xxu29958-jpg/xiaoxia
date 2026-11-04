"""Synthetic messages bound to real native capture and interpretation transactions."""

from astrbot_plugin_ada_companion.appraisals import EventUnderstanding
from astrbot_plugin_memory_companion.core.appraisal_state import AppraisalState
from astrbot_plugin_memory_companion.core.models import utc_now
from astrbot_plugin_memory_companion.core.source_capture import SourceCapture
from support import QQ, event
from working_support import WorkingHarness

FIRST = "今天值夜班，先别催我听山歌，我有点烦。"
LATER = "烦是因为临时加活，不是嫌那首歌。下班了，想接着听那个版本。"
SAVE = {
    "item_id": "",
    "title": "夜班时暂缓听歌",
    "understanding": "值夜班时忙且烦，希望暂缓，不能推为讨厌山歌。",
    "response_implication": "值班时先留空间，时机变化后再确认。",
    "uncertainty": "尚不确定烦躁原因及下班时间。",
    "status": "active",
    "evidence_quote": FIRST,
}


class AppraisalHarness(WorkingHarness):
    def __init__(self, test):
        super().__init__(test)
        self.appraisals = EventUnderstanding(self.dialogue)
        self.first = event(text=FIRST, message_id="first-event")

    async def change(self, source=None, *, read=True, **changes):
        source = source or self.first
        self.links.observe(source)
        if read:
            await self.appraisals.snapshot(source.unified_msg_origin, event=source)
        return await self.appraisals.update(source, {**SAVE, **changes})

    async def snapshot(self, origin=QQ):
        result = await self.appraisals.snapshot(origin)
        if not result["ok"]:
            raise AssertionError(result)
        return result

    async def history(self, item_id):
        return await AppraisalState(self.service.store).history(
            self.links.shared_scope,
            item_id,
            authorize=lambda: True,
        )

    def capture(self, source):
        capture = SourceCapture(self.service.store)
        capture.activate("appraisal-test")
        identity = {
            "installation": capture.installation,
            "platform": source.get_platform_name(),
            "bot": source.get_self_id(),
            "persona": "Ada",
            "scope": "private",
            "session": source.unified_msg_origin,
            "speaker": source.get_sender_id(),
            "audience": source.get_sender_id(),
            "direction": "incoming",
            "message": source.message_obj.message_id,
        }
        prepared = capture.prepare(
            identity, body=source.get_message_str(), observed_at=utc_now()
        )
        capture.stage(prepared, generation="appraisal-test")
        setattr(
            source,
            "_memory_capture_receipt",
            capture.commit(
                prepared["operation"],
                generation="appraisal-test",
            ),
        )
        return capture, prepared
