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
