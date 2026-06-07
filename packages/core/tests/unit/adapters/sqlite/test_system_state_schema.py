# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema tests for SystemState singleton table."""

import datetime

from chaoscypher_core.adapters.sqlite.models import SystemState


def test_defaults() -> None:
    s = SystemState()
    assert s.id == 1
    assert s.processing_paused is False
    assert s.processing_paused_at is None
    assert s.processing_paused_reason is None


def test_accepts_paused_values() -> None:
    now = datetime.datetime(2026, 4, 11, 12, 0, 0, tzinfo=datetime.UTC)
    s = SystemState(
        processing_paused=True,
        processing_paused_at=now,
        processing_paused_reason="deploy",
    )
    assert s.processing_paused is True
    assert s.processing_paused_at == now
    assert s.processing_paused_reason == "deploy"
