"""Inject external page dependencies at their actual runtime owners."""
import sys
from contextlib import contextmanager, ExitStack
from unittest.mock import patch

@contextmanager
def patch_page_dependency(page_module, name, *args, **kwargs):
    owners = {sys.modules[c.__module__] for c in page_module.PluginPageApi.__mro__ if c.__module__ in sys.modules}
    owners = [module for module in owners if name in vars(module)]
    if not owners:
        raise AssertionError(f"No page domain owns dependency {name}")
    with ExitStack() as stack:
        values = [stack.enter_context(patch.object(module, name, *args, **kwargs)) for module in owners]
        yield values[0]
