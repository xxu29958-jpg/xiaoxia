"""Drain active plugin work before replacing its state owners on reload."""

import asyncio
import functools
from contextlib import asynccontextmanager


class Lifecycle:
    def __init__(self):
        self.closed = False
        self.active = {}

    @asynccontextmanager
    async def operation(self):
        """Track a request until all its effects or cancellation cleanup finish."""
        if self.closed:
            raise RuntimeError("Ada is reloading; retry after reload completes")
        task = asyncio.current_task()
        self.active[task] = self.active.get(task, 0) + 1
        try:
            yield
        finally:
            self.active[task] -= 1
            if not self.active[task]:
                del self.active[task]

    def wrap(self, handler):
        """Apply the same drain boundary to authenticated admin operations."""

        @functools.wraps(handler)
        async def wrapped(*args, **kwargs):
            async with self.operation():
                return await handler(*args, **kwargs)

        return wrapped

    async def close(self):
        """Stop admission, then drain or cancel old-instance work before reload."""
        self.closed = True
        tasks = set(self.active) - {asyncio.current_task()}
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=120)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
