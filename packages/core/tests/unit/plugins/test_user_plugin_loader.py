# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the shared user-plugin loader helper."""

import hashlib
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.plugins.user_plugin_loader import (
    audit_log_user_plugin_file,
    load_user_python_plugin,
    user_plugins_allowed,
)


def test_user_plugins_allowed_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAOSCYPHER_ALLOW_USER_PLUGINS", raising=False)
    assert user_plugins_allowed() is True


def test_user_plugins_allowed_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_USER_PLUGINS", "0")
    assert user_plugins_allowed() is False


def test_user_plugins_allowed_other_values_enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_USER_PLUGINS", "1")
    assert user_plugins_allowed() is True
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_USER_PLUGINS", "yes")
    assert user_plugins_allowed() is True


def test_audit_log_emits_warning_with_hash(tmp_path: Path) -> None:
    f = tmp_path / "my_loader.py"
    # Use write_bytes so line endings are identical on every OS -- the helper
    # hashes raw file bytes, and Path.write_text with "\n" would be translated
    # to "\r\n" on Windows and desync the expected digest.
    f.write_bytes(b"x = 1\n")
    expected_sha = hashlib.sha256(b"x = 1\n").hexdigest()

    with capture_logs() as logs:
        audit_log_user_plugin_file(f, registry="LoaderRegistry")

    warnings = [
        e
        for e in logs
        if e.get("event") == "user_plugin_loaded" and e.get("log_level") == "warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["sha256"] == expected_sha
    assert warnings[0]["path"] == str(f.resolve())
    assert warnings[0]["registry"] == "LoaderRegistry"


def test_load_user_python_plugin_respects_kill_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "my_loader.py"
    f.write_text("loaded = True\n", encoding="utf-8")
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_USER_PLUGINS", "0")

    module = load_user_python_plugin(f, module_name="test_loader", registry="LoaderRegistry")

    assert module is None


def test_load_user_python_plugin_executes_and_returns_module(tmp_path: Path) -> None:
    f = tmp_path / "my_loader.py"
    f.write_text("LOADED = 42\n", encoding="utf-8")

    module = load_user_python_plugin(f, module_name="utp_test_loader", registry="LoaderRegistry")

    assert module is not None
    assert module.LOADED == 42


def test_load_user_python_plugin_logs_audit_before_exec(tmp_path: Path) -> None:
    f = tmp_path / "my_loader.py"
    f.write_text("x = 1\n", encoding="utf-8")

    with capture_logs() as logs:
        load_user_python_plugin(f, module_name="utp_test_audit", registry="LoaderRegistry")

    events = [e.get("event") for e in logs]
    assert "user_plugin_loaded" in events
