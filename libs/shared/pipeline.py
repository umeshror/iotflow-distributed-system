"""
Generic async pipeline for event processing.
Allows chaining multiple handlers (middlewares) to process an event.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar

# T must have a trace_id attribute
T = TypeVar("T", bound="HasTraceId")

class HasTraceId(Protocol):
    """Interface for a context that has a trace_id."""
    trace_id: str

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

        # Ensure a trace ID exists for observability
        if not getattr(context, "trace_id", None):
            setattr(context, "trace_id", str(uuid.uuid4()))

        async def _dispatch(idx: int) -> None:
            if idx >= len(self._handlers):
                return
            
            handler = self._handlers[idx]
            handler_name = handler.__class__.__name__
            
            # observability: Automatic timing for each handler
            start_time = time.perf_counter()
            try:
                await handler(context, lambda: _dispatch(idx + 1))
            finally:
                duration = time.perf_counter() - start_time
                # In a real system, we'd emit this to Prometheus/OTEL here
                # For now, we'll just ensure the structure is there.

        await _dispatch(0)
