# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Top-level YAML key typos must raise ConfigError, not silently default.

The typo used here is ``embedding_settings:`` instead of the canonical
``embedding:``.  difflib.get_close_matches gives a ratio of ~0.67 for this
pair, safely above the 0.6 cutoff used in the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.app_config import ConfigError, Settings


def test_unknown_top_level_key_raises(tmp_path: Path) -> None:
    """A misspelled top-level key must raise ConfigError naming the typo and suggesting the fix."""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        """
embedding_settings:      # typo: should be 'embedding'
  provider: ollama
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc:
        Settings.load_from_yaml(cfg)
    assert "embedding_settings" in str(exc.value)
    assert "did you mean 'embedding'?" in str(exc.value).lower()


def test_known_top_level_key_passes(tmp_path: Path) -> None:
    """A correctly spelled top-level key must load without error."""
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(
        """
embedding:
  provider: ollama
""",
        encoding="utf-8",
    )
    s = Settings.load_from_yaml(cfg)
    assert s.embedding.provider == "ollama"
