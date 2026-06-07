# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the HealthProbe Protocol and HealthStatus dataclass."""

from __future__ import annotations

import pytest

from chaoscypher_cortex.shared.health.probes import HealthProbe, HealthStatus


def test_health_status_defaults():
    status = HealthStatus(ok=True)
    assert status.ok is True
    assert status.detail == ""
    assert status.metrics is None


def test_health_status_with_metrics():
    status = HealthStatus(ok=False, detail="down", metrics={"latency_ms": 250})
    assert status.metrics == {"latency_ms": 250}


class _FakeProbe:
    name = "fake"

    async def check(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="fake healthy")


@pytest.mark.asyncio
async def test_probe_protocol_structural_conformance():
    """A class with name + async check() satisfies the Protocol."""
    probe: HealthProbe = _FakeProbe()
    status = await probe.check()
    assert status.ok is True
