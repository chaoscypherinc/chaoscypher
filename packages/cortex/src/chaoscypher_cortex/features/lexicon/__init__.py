# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lexicon Integration Feature.

Thin API layer over core LexiconService for web UI access.
All models and business logic are imported from chaoscypher_core.

Components:
- router: FastAPI endpoints for /api/v1/lexicon

Example:
    from chaoscypher_cortex.features.lexicon import router

    app.include_router(router, prefix="/api/v1/lexicon", tags=["lexicon"])
"""

from chaoscypher_cortex.features.lexicon.api import router


__all__ = ["router"]
