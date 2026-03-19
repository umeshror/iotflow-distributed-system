"""
Generic async pipeline for event processing.
Allows chaining multiple handlers (middlewares) to process an event.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar

T = TypeVar("T")

class Handler(Protocol, Generic[T]):
    """Interface for a pipeline handler."""
    async def __call__(self, context: T, next_call: Callable[[], Awaitable[None]]) -> None:
        ...

class Pipeline(Generic[T]):
    """
    Executes a series of handlers in order.
    Each handler can perform actions before and after the next handler in the chain.
    """

    def __init__(self) -> None:
        self._handlers: list[Handler[T]] = []

    def use(self, handler: Handler[T]) -> Pipeline[T]:
        """Add a handler to the pipeline."""
        self._handlers.append(handler)
        return self

    async def execute(self, context: T) -> None:
        """Execute the pipeline for a given context."""
        if not self._handlers:
            return

        index = 0

        async def _dispatch(idx: int) -> None:
            if idx >= len(self._handlers):
                return
            
            handler = self._handlers[idx]
            await handler(context, lambda: _dispatch(idx + 1))

        await _dispatch(0)
