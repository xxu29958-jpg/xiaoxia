"""Extract explicit chat destinations without treating markup as URL bytes."""

from linkify_it import LinkifyIt
from markdown_it import MarkdownIt

from .public_transport import browse_url
from .web_notebook import BackpackError

CHAT_PUNCTUATION = str.maketrans(dict.fromkeys("“”‘’「」『』，。！？、；（）【】", " "))


def message_urls(text: str) -> set[str]:
    """Parse message formatting, then validate each explicitly written destination.

    Args:
        text: The current user's message, never model output or page contents.

    Returns:
        Canonical public HTTP(S) destinations. Link labels, Markdown formatting
        and sentence punctuation do not become URL bytes or permissions.
    """
    markdown = MarkdownIt("commonmark", {"html": False})
    linkifier = LinkifyIt(options={"fuzzy_link": False, "fuzzy_email": False})
    candidates = []
    in_link = False
    for block in markdown.parse(text):
        for token in block.children or [block]:
            if token.type == "link_open":
                candidates.append(token.attrGet("href"))
                in_link = True
            elif token.type == "link_close":
                in_link = False
            elif not in_link and token.type in (
                "text",
                "code_inline",
                "fence",
                "code_block",
            ):
                # Chinese prose punctuation delimits bare links. Explicit Markdown
                # targets and code spans retain their original destination bytes.
                content = token.content
                if token.type == "text":
                    content = content.translate(CHAT_PUNCTUATION)
                candidates.extend(
                    match.url
                    for match in linkifier.match(content) or []
                    if match.schema in ("http:", "https:")
                )
    result = set()
    for candidate in candidates:
        try:
            result.add(browse_url(candidate))
        except BackpackError:
            continue
    return result
