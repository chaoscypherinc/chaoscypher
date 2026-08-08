# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FastAPI routes for the upgrade feature."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database.migrations.upgrade import (
    ApplyResponse,
    PendingMigrationsResponse,
    RollbackResponse,
    UpgradeService,
)
from chaoscypher_cortex.shared.api.errors import operation_error
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


def get_upgrade_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> UpgradeService:
    """Build an UpgradeService bound to the currently selected database."""
    return UpgradeService(settings.current_database)


router = APIRouter(prefix="/upgrade", tags=["upgrade"])


@router.get("/pending", response_model=PendingMigrationsResponse)
async def list_pending(
    _: CurrentUsername,
    service: Annotated[UpgradeService, Depends(get_upgrade_service)],
) -> PendingMigrationsResponse:
    """Return the current upgrade state + any pending migrations."""
    return service.pending()


@router.post("/apply", response_model=ApplyResponse)
async def apply_upgrades(
    _: CurrentUsername,
    service: Annotated[UpgradeService, Depends(get_upgrade_service)],
) -> ApplyResponse:
    """Apply all pending migrations.

    Operator-triggered. Backs up the DB before applying if the startup
    runner didn't already do so.
    """
    try:
        return service.apply()
    except Exception as exc:
        raise operation_error("apply_migrations", exc) from exc


@router.post("/rollback", response_model=RollbackResponse)
async def rollback_upgrade(
    _: CurrentUsername,
    service: Annotated[UpgradeService, Depends(get_upgrade_service)],
) -> RollbackResponse:
    """Restore the DB from the pre-upgrade backup.

    Core UpgradeService raises ValidationError (no backup recorded) and
    NotFoundError (backup file missing); the global domain handler maps
    them to 400/404 — same statuses as the previous builtin catches.
    """
    return service.rollback()
