"""Inject test storage and external dependencies at their runtime owners."""

import importlib
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from unittest.mock import patch

from astrbot_plugin_meme_manager import config
from astrbot_plugin_meme_manager.backend.auto_collect_support import CollectionPaths


def dependency_owners(runtime_class, name):
    owners = []
    for cls in runtime_class.__mro__:
        module = importlib.import_module(cls.__module__)
        if hasattr(module, name) and module not in owners:
            owners.append(module)
    if not owners:
        raise AttributeError(f"No runtime dependency {runtime_class.__name__}.{name}")
    return owners


@contextmanager
def patch_runtime_dependency(runtime_class, name, *args, **kwargs):
    with ExitStack() as stack:
        mocks = [stack.enter_context(patch.object(owner, name, *args, **kwargs))
                 for owner in dependency_owners(runtime_class, name)]
        yield mocks[0]


def set_runtime_dependency(monkeypatch, runtime_class, name, value):
    for owner in dependency_owners(runtime_class, name):
        monkeypatch.setattr(owner, name, value)


@contextmanager
def collection_path(field, value):
    paths = replace(CollectionPaths.for_root(config.PLUGIN_DATA_DIR), **{field: value})
    with ExitStack() as stack:
        stack.enter_context(patch.object(CollectionPaths, "for_root", return_value=paths))
        if field == "packs":
            stack.enter_context(patch.object(config, "PACKS_DIR", value))
        yield paths
