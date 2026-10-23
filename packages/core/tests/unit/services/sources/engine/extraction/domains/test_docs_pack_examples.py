# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The domains docs' pack examples load, and their built-in pack table is the shipped packs.

Public docs must stay runnable from the public tree: every complete pack
config the two domain pages show (a ```json block declaring ``"role":
"pack"``) is validated by the schema, registered from a user plugin
directory with no load error, and stacked on the floor; the user guide's
table of built-in packs names each shipped pack with its subtype and extra
label counts and its filtering preset, read from the plugin files.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
)
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.testing.repo_files import repo_file_or_skip


if TYPE_CHECKING:
    from pathlib import Path


PAGES = (
    "packages/docs/docs/user-guide/domains.md",
    "packages/docs/docs/developer-guide/building-domains.md",
)

_JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
_ROW = re.compile(r"^\| \*\*(\w+)\*\* \| [^|]+ \| (\d+) \(e\.g\. [^)]*\) \| (\d+) \| ([^|]+) \|$")


def _pack_examples(page: str) -> list[dict[str, Any]]:
    """Every complete pack config in ``page`` (a json block that parses and declares role pack)."""
    text = repo_file_or_skip(page).read_text(encoding="utf-8")
    out = []
    for block in _JSON_BLOCK.findall(text):
        try:
            config = json.loads(block)
        except json.JSONDecodeError:
            continue  # a fragment ("detection": {...}) or an elided skeleton
        if isinstance(config, dict) and config.get("role") == "pack" and config.get("detection"):
            out.append(config)
    return out


@pytest.mark.parametrize("page", PAGES)
def test_every_pack_example_loads_on_the_floor(page: str, tmp_path: Path) -> None:
    examples = _pack_examples(page)
    assert examples, f"{page} shows no complete pack example"
    for index, config in enumerate(examples):
        DomainConfigModel.model_validate(config)
        plugin_dir = tmp_path / f"{index}"
        plugin_dir.mkdir()
        (plugin_dir / f"{config['name']}.jsonld").write_text(json.dumps(config), encoding="utf-8")
        registry = DomainRegistry(user_plugin_dirs=[plugin_dir])
        assert registry.load_errors() == {}, (page, config["name"])
        stack = DomainStack.resolve(registry, [config["name"]])
        for subtype in config.get("subtypes", []):
            line = next(ln for ln in stack.node_lines() if ln.startswith(f"- {subtype['of']}:"))
            assert "_".join(subtype["name"].lower().split()) in line, (page, subtype["name"])


def test_the_built_in_pack_table_is_the_shipped_packs() -> None:
    text = repo_file_or_skip(PAGES[0]).read_text(encoding="utf-8")
    rows = {m[1]: m for line in text.splitlines() if (m := _ROW.match(line))}
    shipped = {}
    for path in sorted(DomainRegistry.builtin_plugin_dir().glob("*.jsonld")):
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("role") == "pack":
            shipped[config["name"]] = config
    assert sorted(rows) == sorted(shipped)
    for name, config in shipped.items():
        row = rows[name]
        assert int(row[2]) == len(config.get("subtypes", [])), name
        assert int(row[3]) == len(config["templates"].get("edge_templates", [])), name
        preset = config.get("extraction_filtering_mode")
        assert row[4].strip() == (f"`{preset}`" if preset else "the floor's"), name
