# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI config reference's env-var table matches the real loader.

``packages/docs/docs/reference/cli/config.md`` labels each variable either
"Fallback default" (an explicit ``settings.yaml`` value wins) or "True
override" (the variable wins). Every "Fallback default" row is driven
through ``Settings.load_from_yaml`` with both a file value and the env var
set, so a row whose variable actually overrides the file fails here — the
Lexicon pair was documented as fallbacks while the loader re-applied them
after the YAML merge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.app_config import Settings


_CONFIG_MD = Path(__file__).resolve().parents[5] / "packages/docs/docs/reference/cli/config.md"
_ROW = re.compile(r"^\| `(?P<var>[A-Z_]+)` \| `(?P<field>[a-z_.]+)` \| (?P<behavior>.+) \|$")

# For each fallback row: the value written to settings.yaml and the env
# value that must NOT win. A new fallback row needs an entry here.
_PROBES: dict[str, tuple[str, str]] = {
    "CHAOSCYPHER_LLM_PROVIDER": ("openai", "anthropic"),
    "OPENAI_API_KEY": ("sk-file", "sk-env"),
    "ANTHROPIC_API_KEY": ("sk-ant-file", "sk-ant-env"),
    "GEMINI_API_KEY": ("gm-file", "gm-env"),
}


def _fallback_rows() -> list[tuple[str, str]]:
    rows = []
    for line in _CONFIG_MD.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match and match["behavior"].strip() == "Fallback default":
            rows.append((match["var"], match["field"]))
    return rows


def test_table_is_parsed() -> None:
    assert _fallback_rows(), f"no 'Fallback default' rows parsed from {_CONFIG_MD}"


def _plain(value: Any) -> Any:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


@pytest.mark.parametrize(("var", "field"), _fallback_rows())
def test_documented_fallback_loses_to_settings_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str, field: str
) -> None:
    assert var in _PROBES, f"add a (file, env) probe for the new fallback row {var}"
    file_value, env_value = _PROBES[var]
    section, key = field.split(".")
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(f"{section}:\n  {key}: {file_value}\n", encoding="utf-8")
    monkeypatch.setenv(var, env_value)

    settings = Settings.load_from_yaml(yaml_path)

    assert _plain(getattr(getattr(settings, section), key)) == file_value
