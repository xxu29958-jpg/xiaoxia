"""Apply the same tool policy to both verified owner channels."""

import re

from .web_notebook import BackpackError

WEB_READ_TOOLS = frozenset(
    {
        "ada_browser",
        "ada_bili",
        "ada_find_web_images",
        "ada_collect_online_meme",
        "anysearch_search",
        "anysearch_extract",
        "anysearch_batch_search",
    }
)
WEB_REPLY_TOOLS = WEB_READ_TOOLS | {
    "ada_cover",
    "ada_bookmarks",
    "ada_web_notes",
    "ada_share_web",
    "search_memes",
}


class ToolPolicy:
    def __init__(self, owners):
        self.owner_memory = owners

    async def personal_qq_access(self, event):
        if event.unified_msg_origin.split(":", 1)[0] != "qq-personal-companion":
            return
        if (
            not event.is_private_chat()
            or event.unified_msg_origin not in self.owner_memory.owners
        ):
            event.stop_event()

    async def game_tool_scope(self, event, req):
        try:
            self.owner_memory.scope(event)
            allowed = True
        except (BackpackError, PermissionError):
            allowed = False
        # Public page-reading turns cannot issue game commands from page instructions.
        reading_page = bool(re.search(r"https?://", event.get_message_str()))
        toolset = getattr(req, "func_tool", None)
        current = event.get_extra("turnflow_user_text") or event.get_message_str()
        if (
            toolset
            and event.get_extra("ada_meme_only")
            and event.get_extra("meme_manager_semantic_mode") == "llm"
            and not re.search(
                r"网上|上网|搜索|收集|下载|保存|https?://|\b(?:web|online|download)\b",
                current,
                re.I,
            )
        ):
            # A plain sticker request is fulfilled by the existing library after
            # generation; it must not become an unrelated web collection task.
            event.set_extra("ada_library_sticker", True)
            for name in WEB_READ_TOOLS | {"send_message_to_user"}:
                toolset.remove_tool(name)
            req.system_prompt += (
                "\n[Current sticker request]\nThe existing library will select and send the "
                "requested image automatically after this response. No browsing, collecting "
                "or other tool call is needed. Keep text minimal; delivery removes it if an "
                "image fits. Do not announce an unperformed search or delivery.\n"
            )
        if (
            toolset
            and event.is_private_chat()
            and event.unified_msg_origin in self.owner_memory.owners
        ):
            # Native per-window readers cannot represent this explicitly linked owner.
            # Keep native writes/provenance; shared readers and core edits cover both channels.
            for tool in list(toolset.func_list):
                if tool.name.startswith("memory_companion_") and tool.name not in {
                    "memory_companion_remember",
                    "memory_companion_correct",
                }:
                    toolset.remove_tool(tool.name)
        event.set_extra("ada_current_request", req)
        event.set_extra("ada_original_toolset", toolset)
        if toolset and (not allowed or reading_page):
            for tool in list(toolset.func_list):
                if (reading_page and tool.name not in WEB_REPLY_TOOLS) or getattr(
                    tool, "mcp_server_name", ""
                ) == "stardew-companion":
                    toolset.remove_tool(tool.name)

    async def isolate_web_tools(self, event, tool, tool_args):
        """Remove private and game tools before public content enters this turn.

        Args:
            event: The current conversation event.
            tool: A tool selected by the model.
            tool_args: Current tool arguments, not interpreted here.
        """
        if tool.name not in WEB_READ_TOOLS:
            return
        event.set_extra("ada_web_read_active", True)
        req = event.get_extra("ada_current_request")
        for toolset in (
            event.get_extra("ada_original_toolset"),
            getattr(req, "func_tool", None),
        ):
            if toolset:
                for candidate in list(toolset.func_list):
                    if candidate.name not in WEB_REPLY_TOOLS:
                        toolset.remove_tool(candidate.name)
