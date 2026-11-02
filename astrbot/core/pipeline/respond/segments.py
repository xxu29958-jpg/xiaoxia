"""Deliver native message segments with optional interruption and failure fencing."""

import asyncio

from astrbot.core import logger
from astrbot.core.message.components import ComponentType


async def send_segments(event, result, headers, interval):
    """Return false when a prepared reply cannot safely send its remaining bubbles."""
    prepared = event.get_extra("segmented_reply_prepared", False)
    sent = 0

    def stop(status):
        event.set_extra("reply_delivery", {"status": status, "sent": sent})
        event.clear_result()
        return False

    for component in result.chain:
        if prepared and event.is_stopped():
            return stop("interrupted")
        delay = await interval(component)
        if not prepared or sent:
            await asyncio.sleep(min(delay, 0.2) if prepared else delay)
        if prepared and event.is_stopped():
            return stop("interrupted")
        try:
            if component.type == ComponentType.Record:
                await event.send(result.derive([component]))
            else:
                await event.send(result.derive([*headers, component]))
                headers.clear()
            # Turnflow may decline a stale delivery without throwing an exception.
            if prepared and event.is_stopped():
                return stop("interrupted")
            sent += 1
        except Exception:
            logger.exception("Message segment delivery failed")
            if prepared:
                return stop("failed_or_uncertain")
    if prepared:
        event.set_extra("reply_delivery", {"status": "sent", "sent": sent})
    return True
