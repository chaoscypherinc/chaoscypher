# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for PathSettings sensitive-root detection."""

import sys
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.settings import PathSettings


def test_warns_when_data_dir_is_etc() -> None:
    # /etc is a sensitive root on POSIX; skip on Windows where resolve()
    # normalizes /etc into a drive-relative oddity.
    if sys.platform == "win32":
        pytest.skip("/etc not applicable on Windows")

    with capture_logs() as logs:
        PathSettings(
            data_dir="/etc",
            config_dir="/tmp/cfg",
            cache_dir="/tmp/cache",
        )

    warnings = [
        e
        for e in logs
        if e.get("event") == "path_settings_suspicious_data_dir" and e.get("log_level") == "warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["data_dir"] == str(Path("/etc").resolve())


def test_warns_when_data_dir_is_root_home() -> None:
    if sys.platform == "win32":
        pytest.skip("/root not applicable on Windows")

    with capture_logs() as logs:
        PathSettings(
            data_dir="/root",
            config_dir="/tmp/cfg",
            cache_dir="/tmp/cache",
        )

    warnings = [e for e in logs if e.get("event") == "path_settings_suspicious_data_dir"]
    assert len(warnings) == 1


def test_does_not_warn_for_normal_path(tmp_path: Path) -> None:
    with capture_logs() as logs:
        PathSettings(
            data_dir=str(tmp_path / "data"),
            config_dir=str(tmp_path / "cfg"),
            cache_dir=str(tmp_path / "cache"),
        )

    warnings = [e for e in logs if e.get("event") == "path_settings_suspicious_data_dir"]
    assert warnings == []


def test_does_not_raise_on_suspicious_path() -> None:
    if sys.platform == "win32":
        pytest.skip("/etc not applicable on Windows")

    # Suspicious path is advisory -- must not block construction.
    ps = PathSettings(
        data_dir="/etc",
        config_dir="/tmp/cfg",
        cache_dir="/tmp/cache",
    )
    assert ps.data_dir == str(Path("/etc").resolve())
