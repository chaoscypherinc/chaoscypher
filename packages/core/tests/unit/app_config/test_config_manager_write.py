# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ConfigManager write hardening: atomic replace + owner-only permissions."""

import stat
import sys

import pytest
import yaml

from chaoscypher_core.app_config.manager import ConfigManager


@pytest.fixture(autouse=True)
def _isolate_global_settings():
    yield
    from chaoscypher_core.app_config import Settings, set_settings

    set_settings(Settings())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes are a no-op on Windows")
def test_update_settings_writes_owner_only_permissions(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    manager = ConfigManager(settings_path=str(settings_path))
    manager.update_settings({"llm": {"chat_provider": "ollama"}})
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600


def test_update_settings_leaves_no_temp_file_and_writes_content(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    manager = ConfigManager(settings_path=str(settings_path))
    # Use a non-default provider: update_settings dumps with
    # exclude_defaults=True, so a value equal to chat_provider's "ollama"
    # default would be omitted from the file entirely and never reach disk.
    # "openai" exercises the atomic-write content round-trip we want to pin.
    manager.update_settings({"llm": {"chat_provider": "openai"}})
    leftovers = sorted(p.name for p in tmp_path.iterdir())
    assert leftovers == ["settings.yaml"]
    on_disk = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert on_disk["llm"]["chat_provider"] == "openai"
