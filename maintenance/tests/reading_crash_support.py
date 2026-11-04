"""Windows spawn target for actual notebook transaction termination windows."""

from astrbot_plugin_ada_companion import reading_store
from astrbot_plugin_ada_companion.web_notebook import WebNotebook


def capture_until_terminated(folder, window, ready, wait):
    store = WebNotebook(folder)
    if window == "before_commit":
        original = reading_store.provided

        def hold(*args, **kwargs):
            result = original(*args, **kwargs)
            ready.send("transaction_open")
            wait.wait(20)
            return result

        reading_store.provided = hold
    store.save_visit(
        "owner",
        {
            "page_id": "crash",
            "url": "https://example.org/crash",
            "title": "Crash source",
            "text": "已取得片段",
            "_source_text": "已取得片段和还未返回的后半段。",
        },
        "",
    )
    ready.send("committed")
    wait.wait(20)
