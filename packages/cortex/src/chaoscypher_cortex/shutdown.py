# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graceful shutdown state for the Cortex FastAPI app.

An in-memory flag that dispatch endpoints (those enqueueing background
work) consult so they can refuse new dispatches while Cortex is draining.
Never persisted — a restart brings Cortex back in its normal state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Import boot first so configure_logging() runs before any structlog logger
# elsewhere in the package is created.
from chaoscypher_cortex import boot as _boot  # noqa: F401


if TYPE_CHECKING:
    from fastapi import Request


class CortexShutdownState:
    """In-memory shutdown flag for the Cortex FastAPI app.

    Checked by dispatch paths (endpoints that enqueue background work)
    so they can refuse new dispatches while draining. Never persisted —
    a restart brings Cortex back in its normal state.
    """

    def __init__(self) -> None:
        """Initialize with shutdown flag unset."""
        self._flag = False

    @property
    def is_shutting_down(self) -> bool:
        """Whether the shutdown sequence has started."""
        return self._flag

    def initiate(self) -> None:
        """Set the flag. Called during the FastAPI lifespan shutdown."""
        self._flag = True


def get_shutdown_state(request: Request) -> CortexShutdownState:
    """FastAPI dependency — extract the shutdown state from app.state.

    Used by dispatch endpoints that want to reject new work during a
    graceful shutdown.
    """
    state: CortexShutdownState = request.app.state.shutdown_state
    return state
