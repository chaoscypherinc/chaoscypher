# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceRecoverySettings defaults and overrides."""

import pytest
from pydantic import ValidationError

from chaoscypher_core.app_config import Settings, SourceRecoverySettings


def test_defaults_match_spec() -> None:
    s = SourceRecoverySettings()
    assert s.worker_scan_interval_seconds == 60
    assert s.cortex_scan_interval_seconds == 300
    # Raised from 120s to 600s to fit local-LLM chunk latencies — the
    # 120s default consistently false-fired on healthy long-running
    # extractions. See `recovery false-positive design notes`.
    assert s.stalled_threshold_seconds == 600
    # Stream-activity heartbeat rate-limit floor (Slice 1).
    assert s.stream_heartbeat_min_interval_seconds == 5.0


def test_overrides_accepted() -> None:
    s = SourceRecoverySettings(
        worker_scan_interval_seconds=30,
        cortex_scan_interval_seconds=600,
        stalled_threshold_seconds=60,
    )
    assert s.worker_scan_interval_seconds == 30
    assert s.cortex_scan_interval_seconds == 600


def test_settings_has_source_recovery_field() -> None:
    settings = Settings()
    assert isinstance(settings.source_recovery, SourceRecoverySettings)
    assert settings.source_recovery.worker_scan_interval_seconds == 60


def test_heartbeat_at_exactly_half_threshold_accepted() -> None:
    """Boundary: heartbeat * 2 == stall_threshold passes (the strict ``>`` check)."""
    s = SourceRecoverySettings(
        stream_heartbeat_min_interval_seconds=30.0,
        stalled_threshold_seconds=60,
    )
    assert s.stream_heartbeat_min_interval_seconds == 30.0
    assert s.stalled_threshold_seconds == 60


def test_heartbeat_below_half_threshold_accepted() -> None:
    """Healthy combo: 5s heartbeat with 600s stall threshold leaves wide margin."""
    s = SourceRecoverySettings(
        stream_heartbeat_min_interval_seconds=5.0,
        stalled_threshold_seconds=600,
    )
    assert s.stream_heartbeat_min_interval_seconds == 5.0


def test_heartbeat_greater_than_half_threshold_rejected() -> None:
    """Bad combo: heartbeat * 2 > stall_threshold raises with a helpful message.

    Regression for PR 3 medium F56 — startup must fail loudly so the operator
    sees the misconfiguration before the recovery loop starts firing
    false-positive recoveries on healthy long-running streams.
    """
    with pytest.raises(ValidationError) as exc:
        SourceRecoverySettings(
            stream_heartbeat_min_interval_seconds=60.0,
            stalled_threshold_seconds=60,
        )

    message = str(exc.value)
    assert "heartbeat interval must be at most half" in message.lower()
    assert "stream_heartbeat_min_interval_seconds=60.0" in message
    assert "stalled_threshold_seconds=60" in message


def test_heartbeat_validation_uses_strict_greater_than() -> None:
    """Just over the boundary (heartbeat * 2 > stall by epsilon) is rejected."""
    with pytest.raises(ValidationError):
        SourceRecoverySettings(
            stream_heartbeat_min_interval_seconds=30.5,
            stalled_threshold_seconds=60,
        )
