# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the backup free-disk preflight."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from chaoscypher_core.database import backup as backup_mod
from chaoscypher_core.database.backup import free_space_ok


def _make_db(tmp_path: Path, size: int) -> Path:
    db = tmp_path / "app.db"
    db.write_bytes(b"\0" * size)
    return db


def test_free_space_ok_true_when_ample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _make_db(tmp_path, 1000)  # need 1200; 5000 free
    monkeypatch.setattr(backup_mod.shutil, "disk_usage", lambda _p: SimpleNamespace(free=5_000))
    assert free_space_ok(db) is True


def test_free_space_ok_false_when_tight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _make_db(tmp_path, 1000)  # need 1200; only 500 free
    monkeypatch.setattr(backup_mod.shutil, "disk_usage", lambda _p: SimpleNamespace(free=500))
    assert free_space_ok(db) is False
