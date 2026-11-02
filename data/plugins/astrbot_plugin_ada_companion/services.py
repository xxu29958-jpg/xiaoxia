"""Resolve active plugin services without keeping stale instances after reload."""


class PluginServices:
    def __init__(self, context):
        self.context = context

    def plugin(self, root_dir_name):
        for star in self.context.get_all_stars():
            if star.root_dir_name == root_dir_name and star.activated:
                return star.star_cls
        return None

    def memory(self):
        plugin = self.plugin("astrbot_plugin_memory_companion")
        return getattr(plugin, "memory_companion", None)
