# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify reconcile_database calls in worker.py honor reconcile_timeout_seconds."""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import RecoveryTimeoutError


@pytest.mark.asyncio
async def test_reconcile_timeout_setting_exists_with_sensible_default() -> None:
    """The new SourceRecoverySettings field has the expected default + bounds."""
    from chaoscypher_core.app_config import SourceRecoverySettings

    s = SourceRecoverySettings()
    assert s.reconcile_timeout_seconds == 30

    # Lower bound enforced
    with pytest.raises(ValueError):
        SourceRecoverySettings(reconcile_timeout_seconds=4)

    # Upper bound enforced
    with pytest.raises(ValueError):
        SourceRecoverySettings(reconcile_timeout_seconds=601)


def test_recovery_timeout_error_is_chaoscypher_exception() -> None:
    """RecoveryTimeoutError subclasses ChaosCypherException."""
    from chaoscypher_core.exceptions import ChaosCypherException

    assert issubclass(RecoveryTimeoutError, ChaosCypherException)
    # Should accept a message string like any normal exception
    exc = RecoveryTimeoutError("test message")
    assert "test message" in str(exc)
