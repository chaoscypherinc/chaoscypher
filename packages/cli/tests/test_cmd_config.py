# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher config` operates on settings.yaml (Tier 3: cli.yaml retired).

Every subcommand (show/get/set/edit/path/reset) now targets the unified
``settings.yaml`` via ``ConfigManager`` — there is no longer a separate
client-only ``cli.yaml``. Reads mask secrets; writes are validated by the
``Settings`` model and persisted atomically.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.config_cmd import (
    _get_nested_value,
    _parse_value,
    _set_nested_value,
    config,
)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_tree_renders_settings_yaml_sections(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["show"])
    assert result.exit_code == 0, result.output
    # settings.yaml-backed groups; no cli.yaml client section anymore
    assert "lexicon" in result.output
    assert "engine" in result.output or "llm" in result.output
    assert "cli.yaml" not in result.output


def test_show_json_format(isolated_settings) -> None:
    import json

    result = CliRunner().invoke(config, ["show", "--format", "json"])
    assert result.exit_code == 0, result.output
    # The command emits clean JSON on stdout; in a bare (non-__main__)
    # invocation structlog's default INFO config may prepend log lines, so
    # parse from the first ``{`` rather than the whole captured stream.
    json_text = result.output[result.output.index("{") :]
    parsed = json.loads(json_text)
    assert "lexicon" in parsed
    assert "llm" in parsed


def test_show_yaml_format(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["show", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    assert "lexicon:" in result.output


def test_show_masks_secrets(isolated_settings) -> None:
    """A configured secret must never round-trip in plaintext through show."""
    from chaoscypher_core.app_config import get_config_manager

    get_config_manager().update_settings({"lexicon": {"token": "super-secret-token"}})

    result = CliRunner().invoke(config, ["show", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert "super-secret-token" not in result.output


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_reads_settings_yaml_value(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["get", "llm.chat_provider"])
    assert result.exit_code == 0, result.output
    assert result.output.strip()  # e.g. "ollama"


def test_get_masks_secrets(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["get", "lexicon.token"])
    assert result.exit_code == 0, result.output
    assert "configured" in result.output or "not set" in result.output
    # never the plaintext


def test_get_unknown_key_exits_1(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["get", "llm.definitely_not_a_field"])
    assert result.exit_code == 1
    assert "not" in result.output.lower()


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


def test_set_persists_via_config_manager_roundtrip(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["set", "lexicon.timeout", "77"])
    assert result.exit_code == 0, result.output
    from chaoscypher_core.app_config import get_config_manager

    assert get_config_manager().load_settings().lexicon.timeout == 77


def test_set_current_database_points_to_db_switch(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["set", "current_database", "other"])
    assert result.exit_code != 0
    assert "db switch" in result.output


def test_set_invalid_key_fails_with_validation_error(isolated_settings) -> None:
    result = CliRunner().invoke(config, ["set", "llm.definitely_not_a_field", "1"])
    assert result.exit_code != 0


def test_set_out_of_range_value_fails(isolated_settings) -> None:
    """lexicon.timeout has ge=5/le=300 — a value outside the range is rejected."""
    result = CliRunner().invoke(config, ["set", "lexicon.timeout", "1"])
    assert result.exit_code != 0


def test_set_masks_secret_in_echo(isolated_settings) -> None:
    """Setting a secret path must not echo the plaintext value.

    `get` masks it, so `set` must not defeat that masking via its confirmation line.
    """
    result = CliRunner().invoke(config, ["set", "lexicon.token", "super-secret-token"])
    assert result.exit_code == 0, result.output
    assert "super-secret-token" not in result.output
    assert "configured" in result.output


def test_set_echoes_non_secret_value(isolated_settings) -> None:
    """A non-secret value is still shown back for confirmation."""
    result = CliRunner().invoke(config, ["set", "lexicon.timeout", "77"])
    assert result.exit_code == 0, result.output
    assert "77" in result.output


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------


def test_path_prints_settings_yaml(isolated_settings) -> None:
    from chaoscypher_cli import engine_config

    result = CliRunner().invoke(config, ["path"])
    assert result.exit_code == 0, result.output
    assert str(engine_config.settings_yaml_path()) in result.output


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_force_recreates_defaults(isolated_settings) -> None:
    from chaoscypher_core.app_config import get_config_manager

    get_config_manager().update_settings({"lexicon": {"timeout": 99}})

    result = CliRunner().invoke(config, ["reset", "--force"])
    assert result.exit_code == 0, result.output
    # Override is gone on disk; back to the code default.
    assert get_config_manager().load_settings().lexicon.timeout == 30
    # ...and a subsequent read in the same process is not stale.
    get_result = CliRunner().invoke(config, ["get", "lexicon.timeout"])
    assert get_result.output.strip().endswith("30")


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


def test_edit_creates_missing_file_and_opens_editor(isolated_settings, monkeypatch) -> None:
    from chaoscypher_cli import engine_config

    monkeypatch.setenv("EDITOR", "true")  # a no-op "editor" on POSIX
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], check: bool) -> None:
        captured["cmd"] = cmd
        captured["check"] = check

    with patch("chaoscypher_cli.commands.config_cmd.subprocess.run", _fake_run):
        result = CliRunner().invoke(config, ["edit"])

    assert result.exit_code == 0, result.output
    assert engine_config.settings_yaml_path().exists()
    assert str(engine_config.settings_yaml_path()) in captured["cmd"]


# ---------------------------------------------------------------------------
# stale cli.yaml notice (root group guard)
# ---------------------------------------------------------------------------


def test_stale_cli_yaml_notice(isolated_settings) -> None:
    """A leftover cli.yaml triggers a one-line ignored-file notice at startup."""
    import os
    import sys
    from pathlib import Path

    from chaoscypher_cli.__main__ import main as main_cli

    cfg_dir = Path(os.environ["CHAOSCYPHER_CONFIG_DIR"])
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "cli.yaml").write_text("ui:\n  color: true\n", encoding="utf-8")

    # LazyGroup inspects sys.argv to decide whether to load the real
    # subcommand vs a stub, so we mirror the invocation there.
    argv = ["chaoscypher", "config", "path"]
    with patch.object(sys, "argv", argv):
        result = CliRunner().invoke(main_cli, ["config", "path"])
    assert "cli.yaml" in result.output and "ignored" in result.output.lower()


# ---------------------------------------------------------------------------
# nested-dict helpers (retained from the pre-Tier-3 module)
# ---------------------------------------------------------------------------


class TestNestedHelpers:
    def test_get_nested_value_found(self) -> None:
        data = {"a": {"b": {"c": 1}}}
        assert _get_nested_value(data, "a.b.c") == 1

    def test_get_nested_value_missing(self) -> None:
        assert _get_nested_value({"a": {}}, "a.b.c") is None

    def test_set_nested_value_creates_intermediate(self) -> None:
        data: dict = {}
        _set_nested_value(data, "a.b.c", 5)
        assert data == {"a": {"b": {"c": 5}}}

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("false", False),
            ("42", 42),
            ("3.14", 3.14),
            ("hello", "hello"),
        ],
    )
    def test_parse_value(self, raw: str, expected: object) -> None:
        assert _parse_value(raw) == expected
