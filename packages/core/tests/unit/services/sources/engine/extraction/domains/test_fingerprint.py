# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)
from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
    compute_domain_content_hash,
)


def _domain(**overrides):
    config = {
        "name": "demo",
        "version": "1.2.3",
        "role": "pack",
        "extends": "core",
        "entity_guidance": "Extract demo entities.",
        "subtypes": [{"name": "Widget", "of": "Object", "description": "a widget"}],
        "templates": {
            "edge_templates": [{"id": "e1", "name": "uses", "description": "uses"}],
        },
        "examples": {"entity_examples": [{"line": "E|Widget A|Object||1.0|S1|A widget"}]},
    }
    config.update(overrides)
    return ConfigurableDomain(config)


def test_hash_is_deterministic():
    assert compute_domain_content_hash(_domain()) == compute_domain_content_hash(_domain())


def test_hash_is_64_char_hex():
    h = compute_domain_content_hash(_domain())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_changes_when_guidance_changes():
    assert compute_domain_content_hash(_domain()) != compute_domain_content_hash(
        _domain(entity_guidance="Different guidance.")
    )


def test_hash_changes_when_templates_change():
    changed = _domain()
    changed.config["subtypes"][0]["description"] = "changed"
    assert compute_domain_content_hash(_domain()) != compute_domain_content_hash(changed)


def test_hash_stable_across_dict_key_order():
    a = _domain()
    b = _domain()
    b.config["examples"] = {"entity_examples": [{"line": "E|Widget A|Object||1.0|S1|A widget"}]}
    assert compute_domain_content_hash(a) == compute_domain_content_hash(b)


# --- The whole validated config is hashed, presentation left out (domain audit, 2026-10-06).


_EVERY_SHAPING_KEY = {
    "entity_exclusions": [{"description": "Bare titles", "examples": ["the prince"]}],
    "extraction_limits": {"max_entity_degree": 90},
    # strict_entity_types, evidence_validation_mode, extraction_overrides,
    # normalizer_overrides and vocabulary_exclusions are no longer pack keys (spec 2026-10-07 § 3)
    "extraction_filtering_mode": "strict",
    "type_aliases": {"Gadget": "Widget"},
    "content_exclusions": {"categories": ["toc"]},
    "core_properties": {"Event": [{"name": "host"}]},
    "detection": {"keywords": {"core": {"terms": ["widget"], "weight": 1.0}}},
    "title_words": ["mr"],
    "relation_vocabulary": {"use": {"labels": ["uses"], "words": ["employs"]}},
}
"""Every key that shapes a graph, each with a value the base config lacks."""


@pytest.mark.parametrize("key", list(_EVERY_SHAPING_KEY))
def test_every_key_that_shapes_a_graph_moves_the_hash(key: str) -> None:
    """Exclusions, limits, strict types, modes, aliases, exclusions, overrides: each one moves it."""
    assert compute_domain_content_hash(_domain()) != compute_domain_content_hash(
        _domain(**{key: _EVERY_SHAPING_KEY[key]})
    )


def test_presentation_keys_do_not_move_the_hash() -> None:
    """Icons, colours, the author, the version and the domain's own description only present it."""
    shown = _domain(icon="Star", author="someone", version="9.9.9", description="Widgets.")
    shown.config["subtypes"][0]["color"] = "#ffffff"
    shown.config["templates"]["edge_templates"][0]["icon"] = "Link"
    assert compute_domain_content_hash(shown) == compute_domain_content_hash(_domain())


def test_an_absent_table_and_an_empty_one_hash_alike() -> None:
    """The validated config fills the defaults: declaring an empty table changes nothing."""
    assert compute_domain_content_hash(_domain()) == compute_domain_content_hash(
        _domain(feminine_titles=[], relation_vocabulary={}, core_properties={})
    )
