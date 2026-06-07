# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the migrations auto-apply setting."""

from __future__ import annotations

import pytest

from chaoscypher_core.settings import EngineSettings, MigrationsSettings


def test_auto_apply_destructive_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE", raising=False)
    assert EngineSettings().migrations.auto_apply_destructive is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
    ],
)
def test_auto_apply_destructive_env_override(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE", raw)
    assert MigrationsSettings().auto_apply_destructive is expected
