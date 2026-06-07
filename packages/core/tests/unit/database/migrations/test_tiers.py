# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the tier-reading helper."""

from __future__ import annotations

import pytest

from chaoscypher_core.database.migrations.tiers import (
    MigrationInfo,
    MigrationTier,
    read_migration_info,
)


def test_read_baseline_info() -> None:
    info = read_migration_info("0001")
    assert isinstance(info, MigrationInfo)
    assert info.revision == "0001"
    assert info.tier is MigrationTier.SAFE_AUTO
    assert info.description  # whatever the baseline says, must be non-empty


def test_tier_enum_accepts_all_valid_values() -> None:
    assert MigrationTier("safe_auto") is MigrationTier.SAFE_AUTO
    assert MigrationTier("needs_confirmation") is MigrationTier.NEEDS_CONFIRMATION
    assert MigrationTier("manual") is MigrationTier.MANUAL


def test_read_unknown_revision_raises() -> None:
    with pytest.raises(ValueError, match="Unknown revision"):
        read_migration_info("9999_not_real")
