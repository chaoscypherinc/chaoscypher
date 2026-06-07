# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Schema tests for SourceRow pause fields."""

import datetime

from chaoscypher_core.adapters.sqlite.models import SourceRow


def test_is_paused_defaults_false() -> None:
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
    )
    assert source.is_paused is False


def test_paused_at_defaults_none() -> None:
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
    )
    assert source.paused_at is None


def test_paused_reason_defaults_none() -> None:
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
    )
    assert source.paused_reason is None


def test_all_three_accept_values() -> None:
    now = datetime.datetime(2026, 4, 11, 12, 0, 0, tzinfo=datetime.UTC)
    source = SourceRow(
        id="s-1",
        database_name="default",
        filename="test.pdf",
        status="pending",
        is_paused=True,
        paused_at=now,
        paused_reason="maintenance",
    )
    assert source.is_paused is True
    assert source.paused_at == now
    assert source.paused_reason == "maintenance"
