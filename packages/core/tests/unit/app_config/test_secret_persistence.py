# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SecretStr settings fields must persist as plaintext through ConfigManager.

``model_dump(mode="json")`` masks SecretStr to ``"**********"`` unless the
field has a plaintext field_serializer (the LexiconSettings pattern). Without
one, any API key saved via ``ConfigManager.update_settings()`` is corrupted on
disk and replaced by literal asterisks after the next process restart.
"""

import pytest
import yaml

from chaoscypher_core.app_config.manager import ConfigManager


@pytest.fixture(autouse=True)
def _isolate_global_settings():
    """update_settings() mutates the app_config global — reset after each test."""
    yield
    from chaoscypher_core.app_config import Settings, set_settings

    set_settings(Settings())


def test_llm_and_embedding_api_keys_round_trip_through_config_manager(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    manager = ConfigManager(settings_path=str(settings_path))

    manager.update_settings(
        {
            "llm": {
                "chat_provider": "openai",
                "openai_api_key": "sk-real-123",
                "anthropic_api_key": "ant-real-456",
                "gemini_api_key": "gem-real-789",
            },
            "embedding": {"api_key": "emb-real-000"},
        }
    )

    on_disk = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert on_disk["llm"]["openai_api_key"] == "sk-real-123"
    assert on_disk["llm"]["anthropic_api_key"] == "ant-real-456"
    assert on_disk["llm"]["gemini_api_key"] == "gem-real-789"
    assert on_disk["embedding"]["api_key"] == "emb-real-000"

    # A fresh manager loads them back as usable secrets (full round trip).
    reloaded = ConfigManager(settings_path=str(settings_path)).get_settings()
    assert reloaded.llm.openai_api_key.get_secret_value() == "sk-real-123"
    assert reloaded.embedding.api_key.get_secret_value() == "emb-real-000"


def test_queue_password_round_trips(tmp_path):
    settings_path = tmp_path / "settings.yaml"
    manager = ConfigManager(settings_path=str(settings_path))
    manager.update_settings({"queue": {"queue_password": "vk-secret"}})
    on_disk = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert on_disk["queue"]["queue_password"] == "vk-secret"
