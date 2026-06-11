# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex Database Module — FastAPI session integration.

The database initialization (engine, schema migrator, seed) and the
per-request adapter factory live in ``chaoscypher_core.database``.
This module retains only the FastAPI-specific session dependency that
the cortex feature endpoints use as a ``Depends()`` target.
"""

from chaoscypher_cortex.shared.database.session import get_current_session


__all__ = ["get_current_session"]
