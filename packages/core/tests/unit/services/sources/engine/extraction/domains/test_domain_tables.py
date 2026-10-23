# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The naming and relation tables live in the domain config, and a change to one is seen.

The name rule's conventions (``feminine_titles``, ``masculine_titles``,
``generation_words``, ``name_forms``) and the evidence validator's
``relation_vocabulary`` are domain ``.jsonld`` fields: validated at load,
read by ``ConfigurableDomain``, hashed into the domain's content fingerprint
when declared, and so into the benchmark's extraction recipe.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from chaoscypher_core.services.sources.engine.extraction.domains import (
    clear_domain_registry_cache,
)
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    DomainConfigModel,
)
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    compute_domain_content_hash,
)
from chaoscypher_core.settings import EngineSettings


def _config(**tables: Any) -> dict[str, Any]:
    """A minimal domain config with ``tables`` added."""
    return {
        "name": "demo",
        "role": "pack",
        "extends": "core",
        "entity_guidance": "Extract demo entities.",
        "subtypes": [{"name": "Hero", "of": "Person", "description": "a hero"}],
        "templates": {
            "edge_templates": [{"id": "e1", "name": "godparent_of", "description": "godparents"}],
        },
        **tables,
    }


def test_the_schema_accepts_the_tables_and_rejects_a_bad_form() -> None:
    """A form needs an ending and a stem; a gender is f, m or null; a relation needs labels and words."""
    DomainConfigModel.model_validate(
        _config(
            feminine_titles=["queen"],
            masculine_titles=["king"],
            generation_words={"old": "elder"},
            name_forms={"ru": [{"ending": "ova", "stem": "ov", "gender": "f"}]},
            relation_vocabulary={"sibling": {"labels": ["sibling_of"], "words": ["sister"]}},
        )
    )
    bad = (
        {"name_forms": {"ru": [{"ending": "ova", "stem": "ov", "gender": "x"}]}},
        {"name_forms": {"ru": [{"ending": "ova"}]}},
        {"relation_vocabulary": {"sibling": {"labels": [], "words": ["sister"]}}},
        {"relation_vocabulary": {"sibling": {"labels": ["sibling_of"], "word": ["sister"]}}},
    )
    for tables in bad:
        with pytest.raises(ValidationError):
            DomainConfigModel.model_validate(_config(**tables))


def test_every_shipped_plugin_validates() -> None:
    """The registry drops a plugin whose config fails the schema: every shipped one must pass."""
    import json
    from pathlib import Path

    from chaoscypher_core.services.sources.engine.extraction.domains import registry

    plugins = Path(registry.__file__).parent / "plugins"
    for path in sorted(plugins.glob("*.jsonld")):
        DomainConfigModel.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_the_fingerprint_moves_with_a_declared_table_and_not_without_one() -> None:
    """A domain with no tables hashes as before they existed; each declared table counts."""
    plain = compute_domain_content_hash(ConfigurableDomain(_config()))
    empty = compute_domain_content_hash(
        ConfigurableDomain(_config(feminine_titles=[], name_forms={}, relation_vocabulary={}))
    )
    assert plain == empty
    hashes = {
        compute_domain_content_hash(ConfigurableDomain(_config(**tables)))
        for tables in (
            {"feminine_titles": ["queen"]},
            {"feminine_titles": ["queen", "empress"]},
            {"masculine_titles": ["king"]},
            {"generation_words": {"old": "elder"}},
            {"name_forms": {"ru": [{"ending": "ova", "stem": "ov", "gender": "f"}]}},
            {"relation_vocabulary": {"sibling": {"labels": ["sibling_of"], "words": ["sister"]}}},
        )
    }
    assert len(hashes) == 6
    assert plain not in hashes


@pytest.fixture
def fresh_registry() -> Iterator[None]:
    """Drop the cached registries before and after (a patched getter must not leak)."""
    clear_domain_registry_cache()
    yield
    clear_domain_registry_cache()


@pytest.mark.parametrize(
    ("getter", "edit"),
    [
        # a genre title of the domain's own (the floor's ``countess`` stays whatever it lists)
        ("get_feminine_titles", lambda v: [w for w in v if w != "dowager"]),
        ("get_name_forms", lambda v: {}),
        ("get_relation_vocabulary", lambda v: {k: e for k, e in v.items() if k != "godparent"}),
    ],
)
def test_the_extraction_recipe_moves_when_a_domain_table_changes(
    monkeypatch: pytest.MonkeyPatch, fresh_registry: None, getter: str, edit: Any
) -> None:
    """The literary plugin as a new file would load it: its fingerprint, so the recipe, moves.

    With no domain named (the cache key per corpus) and with ``literary`` named.
    """
    from chaoscypher_core.benchmark.extraction_recipe import extraction_recipe

    before = (
        extraction_recipe(EngineSettings()),
        extraction_recipe(EngineSettings(), domain="literary"),
    )
    clear_domain_registry_cache()
    original = getattr(ConfigurableDomain, getter)

    def edited(self: ConfigurableDomain) -> Any:
        """The literary domain's table, edited; every other domain's as it is."""
        value = original(self)
        return edit(value) if self.name == "literary" else value

    monkeypatch.setattr(ConfigurableDomain, getter, edited)
    after = (
        extraction_recipe(EngineSettings()),
        extraction_recipe(EngineSettings(), domain="literary"),
    )
    assert after[0] != before[0]
    assert after[1] != before[1]
