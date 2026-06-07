# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cheap settings.yaml peek helpers used by CLI startup paths."""

import yaml

from chaoscypher_cli import engine_config


def _write_settings(data_dir, data):
    (data_dir / "settings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_settings_yaml_path_honours_data_dir_env(isolated_settings):
    assert engine_config.settings_yaml_path() == isolated_settings / "settings.yaml"


def test_setup_completed_flag_satisfies_gate(isolated_settings):
    _write_settings(isolated_settings, {"setup_completed": True})
    assert engine_config.is_setup_completed() is True


def test_explicit_chat_provider_counts_as_configured(isolated_settings):
    _write_settings(isolated_settings, {"llm": {"chat_provider": "ollama"}})
    assert engine_config.is_setup_completed() is True


def test_missing_file_means_not_configured(isolated_settings):
    assert engine_config.is_setup_completed() is False


def test_env_provider_short_circuits(isolated_settings, monkeypatch):
    monkeypatch.setenv("CHAOSCYPHER_LLM_PROVIDER", "ollama")
    assert engine_config.is_setup_completed() is True


def test_read_current_database(isolated_settings):
    _write_settings(isolated_settings, {"current_database": "proj"})
    assert engine_config.read_current_database() == "proj"


def test_read_current_database_missing(isolated_settings):
    assert engine_config.read_current_database() is None


def test_corrupt_yaml_returns_empty_peek(isolated_settings):
    (isolated_settings / "settings.yaml").write_text("{not: valid: yaml", encoding="utf-8")
    assert engine_config.peek_settings_yaml() == {}
    assert engine_config.is_setup_completed() is False
