# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests that seed_templates propagates exceptions.

Previously the method returned a ``ResetResponse`` with
``{status: error, message: str(e)}`` at HTTP 200. ``str(exception)`` can
contain DB paths, SQL fragments, or other internal details that must
never reach the client body. These tests assert the method now re-raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset import ResetOperations


class _BoomError(RuntimeError):
    pass


def test_seed_templates_propagates_underlying_exception() -> None:
    """seed_templates must raise when the seeding function raises."""
    settings_manager = MagicMock()
    settings_manager.get_settings.return_value = MagicMock(current_database="test_db")

    ops = ResetOperations(database_name="test_db", settings_manager=settings_manager)

    with (
        patch(
            "chaoscypher_core.database.seed.seed_default_templates",
            side_effect=_BoomError("sensitive /var/lib/... path leaked"),
        ),
        pytest.raises(_BoomError),
    ):
        ops.seed_templates()


def test_seed_templates_does_not_embed_str_exc_in_response_body() -> None:
    """Regression guard: the error branch that returned str(e) to the client is gone."""
    import inspect

    src = inspect.getsource(ResetOperations.seed_templates)
    # The old code contained f"Failed to seed templates: {e!s}". Any future
    # change that re-introduces that leak will fail this check.
    assert "{e!s}" not in src
    assert "Failed to seed templates:" not in src
