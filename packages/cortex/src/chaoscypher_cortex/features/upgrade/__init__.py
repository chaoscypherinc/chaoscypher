# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Upgrade feature — maintenance-mode API for applying/rolling back migrations.

The ``UpgradeService`` and its DTOs live in
``chaoscypher_core.database.migrations.upgrade`` so the CLI can use the
same code path without crossing the cortex → core dependency direction.
This module only owns the FastAPI router.
"""

from chaoscypher_cortex.features.upgrade.api import router


__all__ = ["router"]
