# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures for health feature tests."""

from collections.abc import Generator

import pytest

from chaoscypher_cortex.features.health.service import HealthService


@pytest.fixture(autouse=True)
def _isolate_health_cache() -> Generator[None]:
    """Reset the class-level health response cache around every test.

    The cache is shared across HealthService instances (by design — it
    must survive per-request construction), so without a reset a cached
    response from one test would satisfy the TTL check in the next.
    """
    HealthService.reset_cache()
    yield
    HealthService.reset_cache()
