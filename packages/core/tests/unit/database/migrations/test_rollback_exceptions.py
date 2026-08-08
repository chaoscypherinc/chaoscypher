# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for UpgradeService.rollback failure paths.

Rollback raised bare RuntimeError (no backup recorded) and stdlib
FileNotFoundError (backup file missing). Both migrate to
ChaosCypherException subclasses so the Cortex boundary maps them via the
global domain handler with unchanged HTTP statuses:

- no backup recorded  → ValidationError → 400 (was validation_error 400)
- backup file missing → NotFoundError  → 404 (was resource_not_found 404)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.database.migrations import upgrade as upgrade_mod
from chaoscypher_core.exceptions import NotFoundError, ValidationError


if TYPE_CHECKING:
    from pathlib import Path


def _service(tmp_path: Path) -> upgrade_mod.UpgradeService:
    """Build an UpgradeService bound to a tmp db path."""
    with patch.object(upgrade_mod, "get_db_path", return_value=tmp_path / "app.db"):
        return upgrade_mod.UpgradeService("test")


def test_rollback_without_backup_raises_validation_error(tmp_path: Path) -> None:
    """No recorded backup → core ValidationError (mapped to HTTP 400)."""
    state = MagicMock(last_backup=None)
    svc = _service(tmp_path)
    with patch.object(upgrade_mod, "get_upgrade_state", return_value=state):
        with pytest.raises(ValidationError, match="No backup available"):
            svc.rollback()


def test_rollback_with_missing_backup_file_raises_not_found(tmp_path: Path) -> None:
    """Recorded backup file gone from disk → core NotFoundError (HTTP 404)."""
    missing = tmp_path / "backups" / "gone.db"
    state = MagicMock(last_backup=str(missing))
    svc = _service(tmp_path)
    with patch.object(upgrade_mod, "get_upgrade_state", return_value=state):
        with pytest.raises(NotFoundError, match="not found"):
            svc.rollback()
