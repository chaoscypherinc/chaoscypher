# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Cortex graceful shutdown state.

CortexShutdownState is a tiny in-memory flag. It does not persist
anything — a container restart brings Cortex back in its normal
state. Endpoints that dispatch background work check this flag and
return 503 during shutdown so new work isn't enqueued after the
drain has started.
"""

from chaoscypher_cortex.shutdown import CortexShutdownState


def test_flag_defaults_false() -> None:
    s = CortexShutdownState()
    assert s.is_shutting_down is False


def test_initiate_sets_flag() -> None:
    s = CortexShutdownState()
    s.initiate()
    assert s.is_shutting_down is True


def test_initiate_is_idempotent() -> None:
    s = CortexShutdownState()
    s.initiate()
    s.initiate()
    assert s.is_shutting_down is True
