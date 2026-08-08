# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings loading edge cases.

Phase 5 (settings truthfulness): schema-retired keys in an existing
settings.yaml are scrubbed with a warning instead of failing the
sections' ``extra="forbid"`` validation — an upgrade must never brick
startup over a knob we deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_retired_keys_in_old_yaml_are_scrubbed(tmp_path: Path) -> None:
    """A settings.yaml still carrying schema-retired keys loads cleanly."""
    from chaoscypher_core.app_config import Settings

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "llm:\n"
        "  thinking_auto_detect: true\n"
        "  chat_interactive_streaming: false\n"
        "  ollama_num_ctx: 12345\n"
        "chat:\n"
        "  enable_response_validation: false\n"
        "  max_tool_iterations: 7\n",
        encoding="utf-8",
    )
    settings = Settings.load_from_yaml(yaml_path)
    # Retired keys are gone from the schema entirely…
    assert not hasattr(settings.llm, "thinking_auto_detect")
    assert not hasattr(settings.llm, "chat_interactive_streaming")
    assert not hasattr(settings.chat, "enable_response_validation")
    # …while the surviving keys in the same sections still load.
    assert settings.llm.ollama_num_ctx == 12345
    assert settings.chat.max_tool_iterations == 7


def test_nested_typo_in_app_local_group_raises(tmp_path: Path) -> None:
    """A misspelled key inside an app-local settings group fails loudly.

    The composed core models already set ``extra="forbid"``, but the
    app-local groups (QueueSettings, TimeoutSettings, ...) historically
    omitted ``model_config`` — so ``timeouts: {llm_chat_waitt: 5}`` was
    silently discarded and the operator's intended override never applied.
    """
    import pydantic

    from chaoscypher_core.app_config import Settings

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("timeouts:\n  llm_chat_waitt: 5\n", encoding="utf-8")

    with pytest.raises(pydantic.ValidationError, match="llm_chat_waitt"):
        Settings.load_from_yaml(yaml_path)


def test_nested_typo_in_local_auth_group_raises(tmp_path: Path) -> None:
    import pydantic

    from chaoscypher_core.app_config import Settings

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("local_auth:\n  cookie_naem: cc_session\n", encoding="utf-8")

    with pytest.raises(pydantic.ValidationError, match="cookie_naem"):
        Settings.load_from_yaml(yaml_path)


def test_all_nested_settings_groups_forbid_unknown_keys() -> None:
    """Every nested settings group on Settings must set extra='forbid'.

    Pins the invariant for future groups: without it a nested typo is
    silently discarded rather than surfaced to the operator.
    """
    from pydantic import BaseModel

    from chaoscypher_core.app_config import Settings

    missing: list[str] = []
    for name, field in Settings.model_fields.items():
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            if ann.model_config.get("extra") != "forbid":
                missing.append(f"{name} ({ann.__name__})")
    assert not missing, f"settings groups without extra='forbid': {missing}"
