# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Local auth feature (single-user, nginx-gated)."""

from chaoscypher_cortex.features.local_auth.api import build_router
from chaoscypher_cortex.features.local_auth.service import LocalAuthService


__all__ = ["LocalAuthService", "build_router"]
