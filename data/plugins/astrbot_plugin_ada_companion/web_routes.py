"""Register the management surface under the plugin lifecycle barrier."""


def register_management_routes(plugin):
    routes = (
        ("manage/overview", "GET", plugin.management.overview),
        ("manage/action", "POST", plugin.management.action),
        ("manage/image", "GET", plugin.management.image),
        ("manage/memory", "GET", plugin.management.memory),
        ("manage/memory", "POST", plugin.management.memory),
        ("status", "GET", plugin.admin.status),
        ("preview-proactive", "POST", plugin.admin.preview_proactive),
        ("preview-linked-memory", "GET", plugin.admin.preview_linked_memory),
        ("verify-continuity", "POST", plugin.admin.verify_continuity),
        ("shared-state", "GET", plugin.admin.shared_state),
    )
    for path, method, handler in routes:
        plugin.context.register_web_api(
            "/astrbot_plugin_ada_companion/" + path,
            plugin.lifecycle.wrap(handler),
            [method],
            handler.__name__,
        )
