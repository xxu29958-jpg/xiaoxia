"""Prepare model-chosen chat paragraphs for the native delivery pipeline."""

import re

from astrbot.api.message_components import Plain


def prepare_bubbles(event):
    """Keep short semantic paragraphs separate without rewriting their content.

    Args:
        event: A private event already processed by the companion and media hooks.

    Returns:
        None. The existing result owns delivery; this function never sends messages.
    """
    event.set_extra("segmented_reply_prepared", False)
    result = event.get_result()
    if (
        not event.get_extra("ada_context_prepared", False)
        or not event.is_private_chat()
        or not result
        or not result.is_model_result()
        or event.get_extra("ada_voice_requested", False)
    ):
        return
    text = "\n".join(x.text for x in result.chain if isinstance(x, Plain))
    # Detailed/structured answers and links retain their exact formatting.
    if len(text) > 360 or re.search(
        r"```|`|https?://|www\.|\|.*\||(?m:^\s*(?:[-*#>]\s|\d+[.)]\s))", text
    ):
        return
    chain = []
    for component in result.chain:
        if not isinstance(component, Plain):
            chain.append(component)
            continue
        parts = [
            part.strip()
            for part in re.split(r"\n\s*\n", component.text)
            if part.strip()
        ]
        if 2 <= len(parts) <= 3 and all(2 <= len(part) <= 180 for part in parts):
            chain.extend(Plain(part) for part in parts)
        else:
            chain.append(component)
    if sum(isinstance(x, Plain) for x in chain) > 3 or len(chain) > 5:
        return
    if len(chain) > 1:
        result.chain = chain
        event.set_extra("segmented_reply_prepared", True)
