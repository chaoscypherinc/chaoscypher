# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema tests for SourceRow.last_activity_at and recovery_attempts."""

import datetime

from chaoscypher_core.adapters.sqlite.models import SourceRow


def test_last_activity_at_defaults_none() -> None:
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
    )
    assert source.last_activity_at is None


def test_recovery_attempts_defaults_zero() -> None:
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
    )
    assert source.recovery_attempts == 0


def test_fields_accept_values() -> None:
    now = datetime.datetime(2026, 4, 11, 12, 0, 0, tzinfo=datetime.UTC)
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="indexing",
        last_activity_at=now,
        recovery_attempts=3,
    )
    assert source.last_activity_at == now
    assert source.recovery_attempts == 3
