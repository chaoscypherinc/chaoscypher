# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edition API.

Reports the installed edition (community or enterprise),
license status, and available features.
"""

from importlib.metadata import entry_points

import structlog
from fastapi import APIRouter

from chaoscypher_cortex.features.edition.models import (
    COMMUNITY_FEATURES,
    EditionResponse,
    LicenseInfo,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_enterprise_info() -> dict | None:
    """Query installed enterprise extension for edition info.

    Scans the ``chaoscypher.edition`` entry-point group. The enterprise
    package registers a callable that returns edition metadata including
    license details and additional feature names.

    Returns:
        Enterprise info dict, or None if not installed.
    """
    eps = entry_points(group="chaoscypher.edition")
    for ep in eps:
        try:
            info_fn = ep.load()
            result: dict | None = info_fn()
            return result
        except Exception:
            logger.warning(
                "enterprise_edition_check_failed",
                name=ep.name,
                exc_info=True,
            )
    return None


@router.get("", response_model=EditionResponse)
async def get_edition(_: CurrentUsername) -> EditionResponse:
    """Get the installed edition, license status, and available features.

    Returns community edition info by default. When the enterprise
    package is installed and licensed, returns enterprise edition info
    with additional features appended.
    """
    enterprise = _get_enterprise_info()

    if enterprise is None:
        return EditionResponse(
            edition="community",
            license=None,
            features=list(COMMUNITY_FEATURES),
        )

    license_data = enterprise.get("license")
    license_info = LicenseInfo(**license_data) if license_data else None

    enterprise_features = enterprise.get("features", [])

    return EditionResponse(
        edition=enterprise.get("edition", "enterprise"),
        license=license_info,
        features=list(COMMUNITY_FEATURES) + enterprise_features,
    )
