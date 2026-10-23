# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""node_embedding_text: a stored node embeds like the extracted entity it came from, on every path."""

from __future__ import annotations

from types import SimpleNamespace

from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
    node_embedding_text,
)


def test_label_aliases_and_definition_in_the_entity_text_shape() -> None:
    node = SimpleNamespace(
        label="Lise Meinen",
        properties={
            "aliases": ["Lise", "the little princess"],
            "definition": "the little princess, wife of Prince Andrew",
        },
    )
    assert node_embedding_text(node) == (
        "Lise Meinen | Also known as: Lise, the little princess | "
        "the little princess, wife of Prince Andrew"
    )


def test_a_bare_node_or_the_commits_placeholder_embeds_the_label_alone() -> None:
    assert node_embedding_text(SimpleNamespace(label="Beta", properties={})) == "Beta"
    placeholder = SimpleNamespace(label="Gamma", properties={"definition": "Entity: Gamma"})
    assert node_embedding_text(placeholder) == "Gamma"


def test_a_committed_node_embeds_exactly_as_its_extracted_entity_did() -> None:
    """Import text == rebuild text: the entity extraction embedded, and the node the commit stored from it."""
    from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
        entity_to_embedding_text,
    )

    entity = {
        "name": "Buonaparte",
        "type": "PERSON",
        "description": "A military leader described by Anna Pávlovna as a villain.",
        "aliases": ["Monsieur Buonaparte", "Napoleon"],
        "properties": {
            "title": "Emperor",
            "nationality": "French",
            "occupation": "Ruler of France",
        },
        "descriptors": ["the Corsican"],
    }
    # the commit's layout: entity properties, then aliases, descriptors, the
    # description as definition, then bookkeeping
    stored = {
        **entity["properties"],
        "aliases": entity["aliases"],
        "descriptors": entity["descriptors"],
        "definition": entity["description"],
        "source_document_id": "doc-1",
        "source_document_name": "war_and_peace_book1.txt",
        "source_type": "file",
        "ingested_at": "2026-10-01T00:00:00+00:00",
        "type": "PERSON",
    }
    node = SimpleNamespace(label="Buonaparte", properties=stored)
    assert node_embedding_text(node) == entity_to_embedding_text(entity)
    assert node_embedding_text(node) == (
        "Buonaparte | Also known as: Monsieur Buonaparte, Napoleon | "
        "A military leader described by Anna Pávlovna as a villain. | Emperor | French | Ruler of France"
    )


def test_a_packaged_description_or_a_template_content_stands_in_for_the_definition() -> None:
    packaged = SimpleNamespace(label="Pierre", properties={"description": "Count Bezúkhov's son"})
    assert node_embedding_text(packaged) == "Pierre | Count Bezúkhov's son"
    template = SimpleNamespace(label="Kutúzov", properties={"content": "commander-in-chief"})
    assert node_embedding_text(template) == "Kutúzov | commander-in-chief"


def test_bookkeeping_never_reaches_the_embedding() -> None:
    node = SimpleNamespace(
        label="Anna Pávlovna",
        properties={
            "source_document_name": "book.txt",
            "ingested_at": "2026",
            "source_type": "file",
            "type": "PERSON",
        },
    )
    assert node_embedding_text(node) == "Anna Pávlovna"


def _committed_properties(entity: dict, *, template_props: list | None = None) -> dict:
    """The properties the commit really stores for ``entity`` (its own builder, not a copy of it)."""
    from chaoscypher_core.services.sources.engine.commit.entity import (
        _DEFAULT_NODE_TEMPLATE,
        EntityCommitHandler,
    )

    handler = EntityCommitHandler.__new__(EntityCommitHandler)  # the builder reads no state
    template = SimpleNamespace(id=_DEFAULT_NODE_TEMPLATE, properties=template_props or [])
    return handler._build_entity_properties(
        entity,
        entity["name"],
        _DEFAULT_NODE_TEMPLATE,
        {template.id: template},
        {"filename": "book.txt"},
        "doc-1",
    )


def test_the_commits_own_property_builder_keeps_import_and_rebuild_text_equal() -> None:
    """Any new field the commit stores must be classified here, or the rebuild text drifts.

    Runs the real ``EntityCommitHandler._build_entity_properties``: a new
    bookkeeping key, a renamed definition, a required template property all
    show up as a mismatch between what extraction embedded and what a
    rebuild of the stored node would embed.
    """
    from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
        entity_to_embedding_text,
    )

    required = [
        SimpleNamespace(name="title", required=True),
        SimpleNamespace(name="content", required=True),
    ]
    entities = [
        {
            "name": "Buonaparte",
            "type": "PERSON",
            "description": "A military leader described by Anna Pávlovna as a villain.",
            "aliases": ["Monsieur Buonaparte"],
            "properties": {"nationality": "French", "occupation": "Ruler of France"},
            "descriptors": ["the Corsican"],
        },
        {"name": "Hardenburg", "type": "PERSON", "description": "", "aliases": []},
        {
            "name": "Lise",
            "type": "PERSON",
            "description": "the little princess",
            "aliases": ["Lise"],
        },
    ]
    for entity in entities:
        for template_props in (None, required):
            stored = _committed_properties(entity, template_props=template_props)
            node = SimpleNamespace(label=entity["name"], properties=stored)
            assert node_embedding_text(node) == entity_to_embedding_text(entity), (
                entity["name"],
                stored,
            )


def test_the_commit_stores_which_aliases_were_given_and_never_embeds_it() -> None:
    """``aliases_given`` reaches the node for the resolver and stays out of both texts (audit W2-1)."""
    from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
        entity_to_embedding_text,
    )
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        GIVEN_ALIASES_KEY,
        MERGED_NAMES_KEY,
    )

    entity = {
        "name": "Anna Pávlovna Schérer",
        "type": "PERSON",
        "description": "the hostess",
        "aliases": ["Annette", "Mlle Schérer"],
        GIVEN_ALIASES_KEY: ["Annette"],
        MERGED_NAMES_KEY: ["Mlle Schérer"],  # wave 4: the resolver guards the node by them
    }
    stored = _committed_properties(entity)
    assert stored[GIVEN_ALIASES_KEY] == ["Annette"]
    assert stored[MERGED_NAMES_KEY] == ["Mlle Schérer"]
    node = SimpleNamespace(label=entity["name"], properties=stored)
    assert node_embedding_text(node) == entity_to_embedding_text(entity)
    assert "Annette |" not in node_embedding_text(node).split("Also known as:")[0]
