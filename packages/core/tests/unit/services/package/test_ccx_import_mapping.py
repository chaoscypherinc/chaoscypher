# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the pure CCX 3.0 -> domain import mapping (Task 4.2).

These functions invert ``services.export.ccx_mapping``: they consume the
JSON-LD objects / source records that a CCX 3.0 package carries and emit the
plain kwargs/dicts the importer feeds into ``NodeCreate`` / ``EdgeCreate`` and
the source/chunk repos. Everything here is pure (stdlib + ``ccx_identity``), so
it is fully unit-testable with no adapter or package fixtures.
"""

from __future__ import annotations

import hashlib

from chaoscypher_core.services.export import ccx_identity
from chaoscypher_core.services.package.importer import ccx_import_mapping as m


# ---------------------------------------------------------------------------
# jsonld_entity_to_node
# ---------------------------------------------------------------------------


def test_entity_to_node_maps_type_name_and_properties() -> None:
    """@id -> ccx_iri, @type -> entity_type hint, name -> label, rest -> properties."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "@type": "Person",
        "name": "Alice",
        "age": 30,
        "city": "Berlin",
    }
    ccx_iri, kwargs = m.jsonld_entity_to_node(obj)

    assert ccx_iri == "urn:ccx:chaoscypher:node/n1"
    assert kwargs["label"] == "Alice"
    assert kwargs["entity_type"] == "Person"
    # The raw @type is carried so the importer can resolve a template_id.
    assert kwargs["type_term"] == "Person"
    assert kwargs["properties"] == {"age": 30, "city": "Berlin"}
    # local_id recovered for one of our own IRIs.
    assert kwargs["local_id"] == "n1"


def test_entity_to_node_drops_reserved_keys_and_object_refs() -> None:
    """Reserved JSON-LD keys and simple-edge object refs never leak into properties."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "@type": "Person",
        "@context": {"x": "y"},
        "name": "Alice",
        "knows": {"@id": "urn:ccx:chaoscypher:node/n2"},
        "tags": [{"@id": "urn:ccx:chaoscypher:node/n3"}],
        "age": 30,
    }
    _, kwargs = m.jsonld_entity_to_node(obj)

    assert kwargs["properties"] == {"age": 30}
    assert "knows" not in kwargs["properties"]
    assert "tags" not in kwargs["properties"]
    assert "@context" not in kwargs["properties"]


def test_entity_to_node_foreign_iri_has_no_local_id() -> None:
    """A foreign @id keeps its IRI as a merge key but yields no local id."""
    obj = {"@id": "urn:other:thing/42", "@type": "Person", "name": "Zed"}
    ccx_iri, kwargs = m.jsonld_entity_to_node(obj)

    assert ccx_iri == "urn:other:thing/42"
    assert kwargs["local_id"] is None
    assert kwargs["entity_type"] == "Person"


def test_entity_to_node_missing_type_falls_back_to_none() -> None:
    """A bare ccx:Entity / missing type leaves entity_type unset for the importer."""
    obj = {"@id": "urn:ccx:chaoscypher:node/n9", "@type": "ccx:Entity", "name": "X"}
    _, kwargs = m.jsonld_entity_to_node(obj)

    assert kwargs["entity_type"] is None
    assert kwargs["type_term"] == "ccx:Entity"


# ---------------------------------------------------------------------------
# relationship_to_edge
# ---------------------------------------------------------------------------


def test_relationship_to_edge_carries_template_iri_and_properties() -> None:
    """A ccx:Relationship resource -> edge dict with predicate + props + template iri."""
    resource = {
        "@id": "urn:ccx:chaoscypher:rel/e2",
        "@type": "ccx:Relationship",
        "ccx:subject": {"@id": "urn:ccx:chaoscypher:node/n1"},
        "ccx:predicate": "worksFor",
        "ccx:object": {"@id": "urn:ccx:chaoscypher:node/n2"},
        "ccx:relationshipTemplate": {"@id": "urn:ccx:chaoscypher:template/t-emp"},
        "since": 2020,
    }
    edge = m.relationship_to_edge(resource)

    assert edge["ccx_iri"] == "urn:ccx:chaoscypher:rel/e2"
    assert edge["subject_iri"] == "urn:ccx:chaoscypher:node/n1"
    assert edge["object_iri"] == "urn:ccx:chaoscypher:node/n2"
    assert edge["predicate"] == "worksFor"
    assert edge["template_iri"] == "urn:ccx:chaoscypher:template/t-emp"
    assert edge["properties"] == {"since": 2020}


def test_relationship_to_edge_without_template_iri() -> None:
    """Template iri is None when ccx:relationshipTemplate is absent."""
    resource = {
        "@id": "urn:ccx:chaoscypher:rel/e3",
        "@type": "ccx:Relationship",
        "ccx:subject": {"@id": "urn:ccx:chaoscypher:node/n1"},
        "ccx:predicate": "near",
        "ccx:object": {"@id": "urn:ccx:chaoscypher:node/n2"},
    }
    edge = m.relationship_to_edge(resource)

    assert edge["template_iri"] is None
    assert edge["properties"] == {}


# ---------------------------------------------------------------------------
# plain_triples_to_edges
# ---------------------------------------------------------------------------


def _expected_triple_iri(subj: str, pred: str, obj: str) -> str:
    digest = hashlib.sha1(f"{subj}|{pred}|{obj}".encode()).hexdigest()
    return ccx_identity.mint_iri("rel", digest)


def test_plain_triples_yields_one_edge_per_object_ref() -> None:
    """A node object's object-reference predicate becomes a deterministic edge."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "@type": "Person",
        "name": "Alice",
        "knows": {"@id": "urn:ccx:chaoscypher:node/n2"},
        "age": 30,
    }
    edges = m.plain_triples_to_edges(obj)

    assert len(edges) == 1
    edge = edges[0]
    assert edge["subject_iri"] == "urn:ccx:chaoscypher:node/n1"
    assert edge["object_iri"] == "urn:ccx:chaoscypher:node/n2"
    assert edge["predicate"] == "knows"
    assert edge["ccx_iri"] == _expected_triple_iri(
        "urn:ccx:chaoscypher:node/n1", "knows", "urn:ccx:chaoscypher:node/n2"
    )


def test_plain_triples_is_deterministic_across_calls() -> None:
    """The same triple produces the same IRI twice (idempotent upsert key)."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "knows": {"@id": "urn:ccx:chaoscypher:node/n2"},
    }
    first = m.plain_triples_to_edges(obj)
    second = m.plain_triples_to_edges(obj)

    assert first[0]["ccx_iri"] == second[0]["ccx_iri"]


def test_plain_triples_handles_list_valued_predicate() -> None:
    """A list of object refs under one predicate yields one edge per ref."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "tags": [
            {"@id": "urn:ccx:chaoscypher:node/n2"},
            {"@id": "urn:ccx:chaoscypher:node/n3"},
        ],
    }
    edges = m.plain_triples_to_edges(obj)

    assert len(edges) == 2
    objects = {e["object_iri"] for e in edges}
    assert objects == {"urn:ccx:chaoscypher:node/n2", "urn:ccx:chaoscypher:node/n3"}
    # Distinct objects -> distinct deterministic IRIs.
    assert edges[0]["ccx_iri"] != edges[1]["ccx_iri"]


def test_plain_triples_skips_reserved_keys_and_literals() -> None:
    """Reserved keys and literal-valued terms never become edges."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "@type": "Person",
        "@context": {"x": "y"},
        "name": "Alice",
        "age": 30,
        "bio": "a string, not an @id ref",
        "knows": {"@id": "urn:ccx:chaoscypher:node/n2"},
    }
    edges = m.plain_triples_to_edges(obj)

    assert len(edges) == 1
    assert edges[0]["predicate"] == "knows"


def test_plain_triples_skips_non_ref_dicts() -> None:
    """A dict value without an @id is a literal object, not an edge."""
    obj = {
        "@id": "urn:ccx:chaoscypher:node/n1",
        "metadata": {"weight": 5},
    }
    assert m.plain_triples_to_edges(obj) == []


# ---------------------------------------------------------------------------
# ccx_chunk_to_chunk
# ---------------------------------------------------------------------------


def test_chunk_selector_resolves_against_full_text() -> None:
    """A TextPositionSelector slices the full text into content + char offsets."""
    rec = {
        "@id": "urn:ccx:chaoscypher:source/s1#chunk-0",
        "@type": "ccx:Chunk",
        "source": {"@id": "urn:ccx:chaoscypher:source/s1"},
        "selector": {"type": "TextPositionSelector", "start": 0, "end": 5},
    }
    chunk = m.ccx_chunk_to_chunk(rec, "Alice works with Bob.")

    assert chunk["content"] == "Alice"
    assert chunk["char_start"] == 0
    assert chunk["char_end"] == 5
    assert chunk["source_iri"] == "urn:ccx:chaoscypher:source/s1"


def test_chunk_inline_content_passthrough() -> None:
    """An inline-content chunk passes content straight through, no offsets."""
    rec = {
        "@id": "urn:ccx:chaoscypher:source/s2#chunk-0",
        "@type": "ccx:Chunk",
        "source": {"@id": "urn:ccx:chaoscypher:source/s2"},
        "content": "hi there",
    }
    chunk = m.ccx_chunk_to_chunk(rec, None)

    assert chunk["content"] == "hi there"
    assert chunk["char_start"] is None
    assert chunk["char_end"] is None
    assert chunk["source_iri"] == "urn:ccx:chaoscypher:source/s2"


def test_chunk_selector_without_full_text_yields_empty_content() -> None:
    """A selector chunk with no full text available cannot resolve content."""
    rec = {
        "@id": "urn:ccx:chaoscypher:source/s3#chunk-0",
        "@type": "ccx:Chunk",
        "source": {"@id": "urn:ccx:chaoscypher:source/s3"},
        "selector": {"type": "TextPositionSelector", "start": 0, "end": 5},
    }
    chunk = m.ccx_chunk_to_chunk(rec, None)

    assert chunk["content"] == ""
    assert chunk["char_start"] == 0
    assert chunk["char_end"] == 5


def test_chunk_carries_citations() -> None:
    """Citations under ccx:citation are surfaced for the importer."""
    rec = {
        "@id": "urn:ccx:chaoscypher:source/s4#chunk-0",
        "@type": "ccx:Chunk",
        "source": {"@id": "urn:ccx:chaoscypher:source/s4"},
        "content": "x",
        "ccx:citation": [{"ccx:confidence": 0.9, "ccx:extractionMethod": "llm"}],
    }
    chunk = m.ccx_chunk_to_chunk(rec, None)

    assert chunk["citations"] == [{"ccx:confidence": 0.9, "ccx:extractionMethod": "llm"}]
