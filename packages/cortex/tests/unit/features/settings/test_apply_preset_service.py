# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``SettingsService.apply_preset`` over a real, temporary ``ConfigManager``.

The handler-level tests mock ``apply_preset``; these execute it end to end
against a tmp ``settings.yaml``: the built-in preset is read from the
registry, its settings are persisted nested under ``llm`` together with
the ``ollama_quick_preset`` marker, and the LLM reload hook fires.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.app_config import ConfigManager, Settings, set_settings
from chaoscypher_cortex.features.settings.service import SettingsService


PRESET_ID = "vram_24gb"


@pytest.fixture
def settings_path(tmp_path: Path) -> Generator[Path]:
    set_settings(Settings())
    yield tmp_path / "settings.yaml"
    set_settings(Settings())


@pytest.fixture
def reload_hook(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    hook = MagicMock()
    monkeypatch.setattr("chaoscypher_core.llm_queue.queue_factory.reload_llm_queue_service", hook)
    return hook


def _service(manager: ConfigManager) -> SettingsService:
    return SettingsService(
        settings_manager=manager,
        database_name="test",
        logging_service=MagicMock(),
    )


def test_apply_preset_persists_llm_settings_marker_and_reloads(
    settings_path: Path, reload_hook: MagicMock
) -> None:
    manager = ConfigManager(settings_path=str(settings_path))
    service = _service(manager)
    preset = service.get_preset(PRESET_ID)
    assert preset is not None
    expected = {**preset.ollama_settings, **preset.llm_settings}
    assert expected, "preset carries no settings to apply"

    # Guard against a vacuous pass: the preset must actually change something.
    before = manager.get_settings().llm
    assert before.ollama_quick_preset != PRESET_ID
    assert any(getattr(before, k) != v for k, v in expected.items())

    response = service.apply_preset(PRESET_ID)

    assert response.success is True
    assert response.preset_id == PRESET_ID
    assert response.settings_updated["ollama_quick_preset"] == PRESET_ID

    # Persisted to disk under ``llm``: a fresh manager on the same file sees it.
    persisted = ConfigManager(settings_path=str(settings_path)).get_settings().llm
    for key, value in expected.items():
        assert getattr(persisted, key) == value, key
    assert persisted.ollama_quick_preset == PRESET_ID

    reload_hook.assert_called_once_with()


def test_apply_unknown_preset_raises_and_writes_nothing(
    settings_path: Path, reload_hook: MagicMock
) -> None:
    manager = ConfigManager(settings_path=str(settings_path))
    service = _service(manager)
    before = manager.get_settings().llm.model_dump()

    with pytest.raises(KeyError):
        service.apply_preset("vram_does_not_exist")

    after = ConfigManager(settings_path=str(settings_path)).get_settings().llm.model_dump()
    assert after == before
    reload_hook.assert_not_called()
