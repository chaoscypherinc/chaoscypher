# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which stage is calling the model, for the per-call-site stats.

The stages set it (``with call_site("extraction"):``); the cache wrappers
read it when they record a hit, miss or bypass. A ContextVar, so
concurrent tasks keep their own site and nested sites restore on exit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from chaoscypher_core.exceptions import ValidationError


CALL_SITES: tuple[str, ...] = (
    "extraction",
    "chat",
    "judge",
    "embeddings.index",
    "embeddings.query",
    "dedup",
    "search",
    "router",
    "other",
)
"""Every call site the stats break down by; ``other`` when no stage set one."""

DEFAULT_CALL_SITE = "other"
"""The site recorded when no stage set one."""

_CURRENT: ContextVar[str] = ContextVar("llm_cache_call_site", default=DEFAULT_CALL_SITE)


def current_call_site() -> str:
    """The call site the innermost ``call_site`` block set, else ``other``."""
    return _CURRENT.get()


@contextmanager
def call_site(name: str) -> Iterator[None]:
    """Attribute the model calls inside the block to ``name`` (one of :data:`CALL_SITES`)."""
    if name not in CALL_SITES:
        msg = f"unknown cache call site {name!r}; expected one of {', '.join(CALL_SITES)}"
        raise ValidationError(msg, field="call_site")
    token = _CURRENT.set(name)
    try:
        yield
    finally:
        _CURRENT.reset(token)


__all__ = ["CALL_SITES", "DEFAULT_CALL_SITE", "call_site", "current_call_site"]
