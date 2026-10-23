# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The fake web site (``chaoscypher_core.testing.fake_web``) for the web source tests."""

from __future__ import annotations

from chaoscypher_core.testing.fake_web import (
    PRIVATE_HOST,
    PUBLIC_HOSTS,
    FakeClock,
    FakeSite,
    fake_site,
)


__all__ = ["PRIVATE_HOST", "PUBLIC_HOSTS", "FakeClock", "FakeSite", "fake_site"]
