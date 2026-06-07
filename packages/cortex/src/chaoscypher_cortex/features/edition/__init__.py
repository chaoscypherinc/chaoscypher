# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edition Feature.

Reports installed edition and available features.

Exports:
    EditionResponse: Pydantic response model.
    LicenseInfo: License details model.
    COMMUNITY_FEATURES: List of community feature names.
    router: FastAPI router for edition endpoint.
"""

from chaoscypher_cortex.features.edition.api import router
from chaoscypher_cortex.features.edition.models import (
    COMMUNITY_FEATURES,
    EditionResponse,
    LicenseInfo,
)


__all__ = [
    "COMMUNITY_FEATURES",
    "EditionResponse",
    "LicenseInfo",
    "router",
]
