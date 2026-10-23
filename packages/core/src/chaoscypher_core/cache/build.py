# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The model build a wrapper keys on, resolved lazily and at most a few times.

Wrapping a provider must not touch the network, so a build that needs the
server (an Ollama digest) is resolved on the first call through it and kept.
A resolver that returns None (the server could not say) leaves the wrapper
without a build, and a wrapper without a build bypasses: keying on a name
alone would serve a stale answer after an ``ollama pull``. Resolution is
retried on later calls up to :attr:`LazyModelBuild.MAX_ATTEMPTS` times.

A replay pins the builds it serves (:func:`pinned_builds`, the build each
model was recorded under, from a reference pack's manifest): a pinned build
wins over any resolver, so a replay keys exactly as the record run did and
never asks a server for a digest.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType


BuildResolver = Callable[[], Awaitable[str | None]]
"""An async callable answering the model build id, or None when it cannot be known."""


class LazyModelBuild:
    """A model build id given up front, or resolved on first use and memoised."""

    MAX_ATTEMPTS = 3
    """How many calls may try to resolve a build before the wrapper settles on bypassing."""

    def __init__(self, static: str | None = None, resolve: BuildResolver | None = None) -> None:
        """Hold ``static`` when known, else resolve with ``resolve`` on the first :meth:`get`."""
        self._value = static
        self._resolve = resolve
        self._attempts = 0
        self._lock = asyncio.Lock()

    async def get(self) -> str | None:
        """The build id, or None when it is unknown (the caller bypasses)."""
        if self._value is not None or self._resolve is None:
            return self._value
        async with self._lock:
            if self._value is None and self._attempts < self.MAX_ATTEMPTS:
                self._attempts += 1
                self._value = await self._resolve()
        return self._value


ModelRef = tuple[str, str]
"""``(provider type, model name)``: what a pinned build is looked up by."""

_PINNED: ContextVar[Mapping[ModelRef, str]] = ContextVar(
    "llm_cache_pinned_builds", default=MappingProxyType({})
)


def pinned_build(provider: str, model: str) -> str | None:
    """The build the innermost :func:`pinned_builds` block pins for ``provider``/``model``, or None."""
    return _PINNED.get().get((provider, model))


@contextmanager
def pinned_builds(builds: Mapping[ModelRef, str]) -> Iterator[None]:
    """Key every call to the given models on the given builds inside the block (a replay).

    ``builds`` maps ``(provider type, model name)`` to a build id
    (:func:`~chaoscypher_core.cache.keys.model_build_id`). A ContextVar, so
    the event loop's tasks and ``asyncio.to_thread`` workers started inside
    the block see the pins, and an outer block's pins return on exit.
    """
    token = _PINNED.set(MappingProxyType({**_PINNED.get(), **builds}))
    try:
        yield
    finally:
        _PINNED.reset(token)


__all__ = ["BuildResolver", "LazyModelBuild", "ModelRef", "pinned_build", "pinned_builds"]
