# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

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
        "guidance": "Extract demo entities.",
        "templates": {
            "node_templates": [{"id": "n1", "name": "Widget", "description": "a widget"}],
            "edge_templates": [{"id": "e1", "name": "uses", "description": "uses"}],
        },
        "examples": {"entity_examples": [{"text": "Widget A"}]},
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
        _domain(guidance="Different guidance.")
    )


def test_hash_changes_when_templates_change():
    changed = _domain()
    changed.config["templates"]["node_templates"][0]["description"] = "changed"
    assert compute_domain_content_hash(_domain()) != compute_domain_content_hash(changed)


def test_hash_stable_across_dict_key_order():
    a = _domain()
    b = _domain()
    b.config["examples"] = {"entity_examples": [{"text": "Widget A"}]}
    assert compute_domain_content_hash(a) == compute_domain_content_hash(b)
