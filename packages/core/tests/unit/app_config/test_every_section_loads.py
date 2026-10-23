# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every section of ``Settings`` loads from settings.yaml and from the environment, listed by hand or not.

Until 2026-10-05 the loader built each section by name, so a section added
to the model (``graphrag``) validated in the YAML and then did nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from chaoscypher_core.app_config import Settings


def _sections() -> list[tuple[str, type[BaseModel]]]:
    return [
        (name, f.annotation)
        for name, f in Settings.model_fields.items()
        if isinstance(f.annotation, type) and issubclass(f.annotation, BaseModel)
    ]


def _a_bool_field(model: type[BaseModel]) -> str | None:
    return next((n for n, f in model.model_fields.items() if f.annotation is bool), None)


def test_the_graph_and_graphrag_sections_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        yaml.safe_dump({"graph": {"resolve_identities": False}, "graphrag": {"vector_reserve": 3}}),
        encoding="utf-8",
    )
    settings = Settings.load_from_yaml(path)
    assert settings.graph.resolve_identities is False
    assert settings.graphrag.vector_reserve == 3


def test_the_graph_section_loads_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("app_name: t\n", encoding="utf-8")
    monkeypatch.setenv("CHAOSCYPHER_GRAPH__RESOLVE_IDENTITIES", "false")
    assert Settings.load_from_yaml(path).graph.resolve_identities is False


@pytest.mark.parametrize(("name", "model"), [(n, m) for n, m in _sections() if _a_bool_field(m)])
def test_every_section_with_a_flag_loads_it_from_yaml(
    tmp_path: Path, name: str, model: type[BaseModel]
) -> None:
    flag = _a_bool_field(model)
    assert flag is not None
    flipped = not model.model_fields[flag].default
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({name: {flag: flipped}}), encoding="utf-8")
    assert getattr(getattr(Settings.load_from_yaml(path), name), flag) is flipped
