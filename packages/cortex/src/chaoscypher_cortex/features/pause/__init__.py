# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source pause/resume feature slice.

Exposes the PauseService that orchestrates per-source and system-wide
pause/resume, plus the Pydantic DTOs that shape the REST surface.
"""

from chaoscypher_cortex.features.pause.api import (
    sources_router,
    system_router,
)
from chaoscypher_cortex.features.pause.models import (
    BulkPauseRequest,
    BulkResumeRequest,
    PauseSourceRequest,
    PauseSystemRequest,
    SystemPauseStatusResponse,
)
from chaoscypher_cortex.features.pause.repository import PauseRepository
from chaoscypher_cortex.features.pause.service import PauseService


__all__ = [
    "BulkPauseRequest",
    "BulkResumeRequest",
    "PauseRepository",
    "PauseService",
    "PauseSourceRequest",
    "PauseSystemRequest",
    "SystemPauseStatusResponse",
    "sources_router",
    "system_router",
]
