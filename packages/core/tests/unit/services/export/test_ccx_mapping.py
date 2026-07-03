# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pure CCX 3.0 domain-dict -> JSON-LD / RDF mapping helpers."""

import rdflib

from chaoscypher_core.services.export import ccx_mapping


# ---------------------------------------------------------------------------
# Task 2.2 — node_to_jsonld
# ---------------------------------------------------------------------------


class TestNodeToJsonld:
    """A GraphNode dict maps to a JSON-LD node object."""

    def test_maps_alice_person(self):
        """Alice/Person node with age=30 maps to the documented shape."""
        node = {
            "id": "n1",
            "label": "Alice",
            "entity_type": "Person",
            "template_id": "t-person",
            "properties": {"age": 30},
        }
        templates_by_id = {
            "t-person": {"id": "t-person", "name": "Person", "template_type": "node"}
        }
        result = ccx_mapping.node_to_jsonld(node, templates_by_id)
        assert result["@id"] == "urn:ccx:chaoscypher:node/n1"
        assert result["@type"] == "Person"
        assert result["name"] == "Alice"
        assert result["age"] == 30

    def test_type_falls_back_to_entity_type(self):
        """With no template, @type falls back to entity_type."""
        node = {
            "id": "n2",
            "label": "Acme",
            "entity_type": "Organization",
            "template_id": "missing",
            "properties": None,
        }
        result = ccx_mapping.node_to_jsonld(node, {})
        assert result["@type"] == "Organization"

    def test_type_falls_back_to_ccx_entity(self):
        """With no template and no entity_type, @type is ccx:Entity."""
        node = {
            "id": "n3",
            "label": "Mystery",
            "entity_type": None,
            "template_id": "missing",
            "properties": None,
        }
        result = ccx_mapping.node_to_jsonld(node, {})
        assert result["@type"] == "ccx:Entity"

    def test_reserved_keys_win_over_properties(self):
        """Reserved keys (name/@type/@id) are not clobbered by properties."""
        node = {
            "id": "n4",
            "label": "Real Name",
            "entity_type": "Person",
            "template_id": "t",
            "properties": {"name": "Bogus", "@type": "Bogus", "extra": "kept"},
        }
        templates_by_id = {"t": {"id": "t", "name": "Person"}}
        result = ccx_mapping.node_to_jsonld(node, templates_by_id)
        assert result["name"] == "Real Name"
        assert result["@type"] == "Person"
        assert result["extra"] == "kept"

    def test_prefers_persisted_ccx_iri(self):
        """A persisted ccx_iri is used for @id over a minted one."""
        node = {
            "id": "n5",
            "ccx_iri": "https://example.org/foreign/alice",
            "label": "Alice",
            "entity_type": "Person",
            "template_id": "t",
            "properties": {},
        }
        result = ccx_mapping.node_to_jsonld(node, {})
        assert result["@id"] == "https://example.org/foreign/alice"


# ---------------------------------------------------------------------------
# Task 2.3 — edge_to_jsonld
# ---------------------------------------------------------------------------


class TestEdgeToJsonld:
    """A GraphEdge dict maps to either a triple tuple or a relationship resource."""

    def test_simple_edge_is_triple(self):
        """An edge with no properties becomes a ('triple', s, p, {@id: o})."""
        edge = {
            "id": "e1",
            "label": "knows",
            "template_id": "t-knows",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "properties": None,
        }
        nodes_iri = {
            "n1": "urn:ccx:chaoscypher:node/n1",
            "n2": "urn:ccx:chaoscypher:node/n2",
        }
        result = ccx_mapping.edge_to_jsonld(edge, nodes_iri, {})
        assert result == (
            "triple",
            "urn:ccx:chaoscypher:node/n1",
            "knows",
            {"@id": "urn:ccx:chaoscypher:node/n2"},
        )

    def test_property_edge_is_relationship_resource(self):
        """An edge with properties becomes a reified relationship resource."""
        edge = {
            "id": "e2",
            "label": "worked_at",
            "template_id": "t-emp",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "properties": {"since": 2020},
        }
        nodes_iri = {
            "n1": "urn:ccx:chaoscypher:node/n1",
            "n2": "urn:ccx:chaoscypher:node/n2",
        }
        kind, resource = ccx_mapping.edge_to_jsonld(edge, nodes_iri, {})
        assert kind == "relationship"
        assert resource["@id"] == "urn:ccx:chaoscypher:rel/e2"
        assert resource["@type"] == "ccx:Relationship"
        assert resource["ccx:subject"] == {"@id": "urn:ccx:chaoscypher:node/n1"}
        assert resource["ccx:predicate"] == "worked_at"
        assert resource["ccx:object"] == {"@id": "urn:ccx:chaoscypher:node/n2"}
        assert resource["since"] == 2020

    def test_relationship_prefers_persisted_ccx_iri(self):
        """A persisted ccx_iri is used for the relationship resource @id."""
        edge = {
            "id": "e3",
            "ccx_iri": "https://example.org/foreign/rel",
            "label": "rel",
            "template_id": "t",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "properties": {"k": "v"},
        }
        nodes_iri = {"n1": "iri-1", "n2": "iri-2"}
        _kind, resource = ccx_mapping.edge_to_jsonld(edge, nodes_iri, {})
        assert resource["@id"] == "https://example.org/foreign/rel"

    def test_relationship_carries_template_iri(self):
        """A relationship resource carries the minted edge template IRI."""
        edge = {
            "id": "e4",
            "label": "worked_at",
            "template_id": "t-emp",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "properties": {"since": 2020},
        }
        nodes_iri = {
            "n1": "urn:ccx:chaoscypher:node/n1",
            "n2": "urn:ccx:chaoscypher:node/n2",
        }
        _kind, resource = ccx_mapping.edge_to_jsonld(edge, nodes_iri, {})
        assert resource["ccx:relationshipTemplate"] == {"@id": "urn:ccx:chaoscypher:template/t-emp"}


# ---------------------------------------------------------------------------
# Task 2.4 — build_knowledge_graph
# ---------------------------------------------------------------------------


class TestBuildKnowledgeGraph:
    """Assemble nodes + edges into a {'@graph': [...]} object."""

    def _nodes(self):
        return [
            {
                "id": "n1",
                "label": "Alice",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
            {
                "id": "n2",
                "label": "Bob",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
            {
                "id": "n3",
                "label": "Carol",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
        ]

    def test_members_present(self):
        """Both node objects appear as @graph members."""
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), [], {})
        ids = {m["@id"] for m in graph["@graph"]}
        assert "urn:ccx:chaoscypher:node/n1" in ids
        assert "urn:ccx:chaoscypher:node/n2" in ids

    def test_simple_edge_attached_to_subject(self):
        """A simple-edge predicate is attached to its subject node object."""
        edges = [
            {
                "id": "e1",
                "label": "knows",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": None,
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        assert subject["knows"] == {"@id": "urn:ccx:chaoscypher:node/n2"}

    def test_repeated_predicate_becomes_list(self):
        """A repeated predicate on the same subject is promoted to a list."""
        edges = [
            {
                "id": "e1",
                "label": "knows",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": None,
            },
            {
                "id": "e2",
                "label": "knows",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n3",
                "properties": None,
            },
        ]
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        assert isinstance(subject["knows"], list)
        assert {"@id": "urn:ccx:chaoscypher:node/n2"} in subject["knows"]
        assert {"@id": "urn:ccx:chaoscypher:node/n3"} in subject["knows"]

    def test_relationship_resource_present(self):
        """A property edge contributes a relationship resource member."""
        edges = [
            {
                "id": "e1",
                "label": "worked_at",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": {"since": 2020},
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert len(rels) == 1
        assert rels[0]["@id"] == "urn:ccx:chaoscypher:rel/e1"

    def test_empty_inputs_empty_graph(self):
        """Empty inputs produce an empty graph (à-la-carte sources-only)."""
        assert ccx_mapping.build_knowledge_graph([], [], {}) == {"@graph": []}

    def test_predicate_collision_with_property_emits_relationship(self):
        """A simple edge whose label collides with a node property does not
        corrupt that property; it is emitted as a ccx:Relationship instead.
        """
        nodes = [
            {
                "id": "n1",
                "label": "Alice",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {"knows": "a string literal property"},
            },
            {
                "id": "n2",
                "label": "Bob",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
        ]
        edges = [
            {
                "id": "e1",
                "label": "knows",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": None,
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(nodes, edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        # The node property value is untouched (not blended into a list/object).
        assert subject["knows"] == "a string literal property"
        # The edge surfaces as a ccx:Relationship resource in @graph.
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert len(rels) == 1
        assert rels[0]["@id"] == "urn:ccx:chaoscypher:rel/e1"
        assert rels[0]["ccx:subject"] == {"@id": "urn:ccx:chaoscypher:node/n1"}
        assert rels[0]["ccx:predicate"] == "knows"
        assert rels[0]["ccx:object"] == {"@id": "urn:ccx:chaoscypher:node/n2"}

    def test_predicate_collision_with_reserved_key_emits_relationship(self):
        """A simple edge whose label is a reserved JSON-LD key is emitted as a
        ccx:Relationship rather than attached as a bare key.
        """
        nodes = [
            {
                "id": "n1",
                "label": "Alice",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
            {
                "id": "n2",
                "label": "Bob",
                "entity_type": "Person",
                "template_id": "t",
                "properties": {},
            },
        ]
        edges = [
            {
                "id": "e1",
                "label": "name",  # reserved JSON-LD key
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": None,
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(nodes, edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        # The reserved key keeps its real value.
        assert subject["name"] == "Alice"
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert len(rels) == 1
        assert rels[0]["ccx:predicate"] == "name"

    def test_non_colliding_simple_edge_stays_bare_predicate(self):
        """A non-colliding simple edge still attaches as a bare predicate."""
        edges = [
            {
                "id": "e1",
                "label": "likes",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "n2",
                "properties": None,
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        assert subject["likes"] == {"@id": "urn:ccx:chaoscypher:node/n2"}
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert rels == []

    def test_dangling_edge_is_skipped(self):
        """An edge referencing a node not in the batch is silently omitted."""
        edges = [
            {
                "id": "e1",
                "label": "knows",
                "template_id": "t",
                "source_node_id": "n1",
                "target_node_id": "missing",  # not in node list
                "properties": None,
            }
        ]
        # Must not raise KeyError.
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        subject = next(m for m in graph["@graph"] if m["@id"] == "urn:ccx:chaoscypher:node/n1")
        assert "knows" not in subject
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert rels == []

    def test_dangling_property_edge_is_skipped(self):
        """A property-bearing edge with a missing endpoint is also skipped."""
        edges = [
            {
                "id": "e1",
                "label": "worked_at",
                "template_id": "t",
                "source_node_id": "missing",  # not in node list
                "target_node_id": "n2",
                "properties": {"since": 2020},
            }
        ]
        graph = ccx_mapping.build_knowledge_graph(self._nodes(), edges, {})
        rels = [m for m in graph["@graph"] if m.get("@type") == "ccx:Relationship"]
        assert rels == []


# ---------------------------------------------------------------------------
# Task 2.5 — source_records
# ---------------------------------------------------------------------------


class TestSourceRecords:
    """A source + its chunks map to a ccx:Source record + ccx:Chunk records."""

    def test_source_record_shape(self):
        """The source record carries the documented CCX 3.0 fields."""
        source = {
            "id": "s1",
            "title": "My Doc",
            "full_text": "Alice knows Bob.",
            "extraction_mode": "mcp",
        }
        records = ccx_mapping.source_records(source, [], {"strategy": "fixed"})
        src = records[0]
        assert src["@id"] == "urn:ccx:chaoscypher:source/s1"
        assert src["@type"] == "ccx:Source"
        assert src["sourceMode"] == "derived-only"
        assert src["extractedBy"] == "mcp"
        assert src["title"] == "My Doc"
        assert src["chunking"] == {"strategy": "fixed"}
        assert src["text"] == ccx_mapping.TEXT_ASSET_PENDING

    def test_source_omits_absent_optionals(self):
        """Absent title / extractedBy / chunking / text are omitted, not null."""
        source = {"id": "s2", "title": None, "full_text": None}
        records = ccx_mapping.source_records(source, [], None)
        src = records[0]
        assert "title" not in src
        assert "extractedBy" not in src
        assert "chunking" not in src
        assert "text" not in src

    def test_chunk_with_offsets_uses_selector(self):
        """With full_text and offsets, the chunk uses a TextPositionSelector."""
        source = {"id": "s3", "full_text": "Alice knows Bob.", "title": "Doc"}
        chunks = [
            {"id": "c1", "chunk_index": 0, "content": "Alice", "char_start": 0, "char_end": 5}
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert chunk["@id"] == "urn:ccx:chaoscypher:source/s3#chunk-0"
        assert chunk["@type"] == "ccx:Chunk"
        assert chunk["source"] == {"@id": "urn:ccx:chaoscypher:source/s3"}
        assert chunk["selector"] == {
            "type": "TextPositionSelector",
            "start": 0,
            "end": 5,
        }
        assert "content" not in chunk

    def test_chunk_without_offsets_uses_inline_content(self):
        """With no offsets, the chunk falls back to inline content."""
        source = {"id": "s4", "full_text": "Alice knows Bob.", "title": "Doc"}
        chunks = [
            {"id": "c1", "chunk_index": 0, "content": "Alice", "char_start": None, "char_end": None}
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert chunk["content"] == "Alice"
        assert "selector" not in chunk

    def test_chunk_inline_when_no_full_text(self):
        """With no source full_text, chunks are inline even if offsets exist."""
        source = {"id": "s5", "full_text": None, "title": "Doc"}
        chunks = [
            {"id": "c1", "chunk_index": 0, "content": "Alice", "char_start": 0, "char_end": 5}
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert chunk["content"] == "Alice"
        assert "selector" not in chunk

    def test_chunk_id_falls_back_to_id(self):
        """When chunk_index is None, the chunk id is used in the fragment."""
        source = {"id": "s6", "full_text": None}
        chunks = [{"id": "c-xyz", "chunk_index": None, "content": "x"}]
        records = ccx_mapping.source_records(source, chunks, None)
        assert records[1]["@id"] == "urn:ccx:chaoscypher:source/s6#chunk-c-xyz"

    def test_chunk_without_offsets_or_content_omits_content(self):
        """A chunk with content=None and no offsets emits no content key."""
        source = {"id": "s8", "full_text": None}
        chunks = [
            {"id": "c1", "chunk_index": 0, "content": None, "char_start": None, "char_end": None}
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert "content" not in chunk
        assert "selector" not in chunk

    def test_chunk_with_empty_content_omits_content(self):
        """A chunk with empty-string content and no offsets emits no content key."""
        source = {"id": "s9", "full_text": None}
        chunks = [{"id": "c1", "chunk_index": 0, "content": ""}]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert "content" not in chunk

    def test_chunk_citations_mapped(self):
        """Chunk citations carry the entity ref + confidence/method.

        The cited entity's node IRI is minted from the citation's
        ``entity_uri`` (the real ``SourceCitation`` column, holding the node
        id) and emitted as ``ccx:citation: {"@id": <node IRI>}`` so the entity
        link round-trips.
        """
        source = {"id": "s7", "full_text": None}
        chunks = [
            {
                "id": "c1",
                "chunk_index": 0,
                "content": "x",
                "citations": [
                    {
                        "entity_uri": "node_42",
                        "confidence": 0.9,
                        "extraction_method": "ai_extraction",
                    },
                ],
            }
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        chunk = records[1]
        assert chunk["ccx:citation"] == [
            {
                "ccx:citation": {"@id": "urn:ccx:chaoscypher:node/node_42"},
                "ccx:confidence": 0.9,
                "ccx:extractionMethod": "ai_extraction",
            }
        ]

    def test_chunk_citation_prefers_persisted_node_ccx_iri(self):
        """A persisted node ccx_iri on the citation wins over minting."""
        source = {"id": "s8", "full_text": None}
        chunks = [
            {
                "id": "c1",
                "chunk_index": 0,
                "content": "x",
                "citations": [
                    {
                        "entity_uri": "node_42",
                        "node_ccx_iri": "urn:ccx:chaoscypher:node/foreign-1",
                        "confidence": 0.5,
                    },
                ],
            }
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        entry = records[1]["ccx:citation"][0]
        assert entry["ccx:citation"] == {"@id": "urn:ccx:chaoscypher:node/foreign-1"}

    def test_chunk_citation_without_entity_ref_omits_link(self):
        """A citation lacking any entity reference emits no ccx:citation @id."""
        source = {"id": "s9", "full_text": None}
        chunks = [
            {
                "id": "c1",
                "chunk_index": 0,
                "content": "x",
                "citations": [{"confidence": 0.9, "extraction_method": "ai_extraction"}],
            }
        ]
        records = ccx_mapping.source_records(source, chunks, None)
        assert records[1]["ccx:citation"] == [
            {"ccx:confidence": 0.9, "ccx:extractionMethod": "ai_extraction"}
        ]


# ---------------------------------------------------------------------------
# Task 2.6 — embeddings
# ---------------------------------------------------------------------------


class TestEmbeddingDescriptor:
    """embedding_descriptor builds the provenance/inclusion shape."""

    def test_provenance_only(self):
        """A provenance-only descriptor has no provider and included=False."""
        desc = ccx_mapping.embedding_descriptor(coverage="full", model="m", dimensions=384)
        assert desc == {
            "model": "m",
            "dimensions": 384,
            "coverage": "full",
            "included": False,
        }

    def test_with_provider_and_included(self):
        """Provider is added when given; included reflects the flag."""
        desc = ccx_mapping.embedding_descriptor(
            coverage="partial",
            model="m",
            dimensions=768,
            provider="openai",
            included=True,
        )
        assert desc["provider"] == "openai"
        assert desc["included"] is True


class TestEmbeddingsToParquetBytes:
    """embeddings_to_parquet_bytes round-trips id/vector rows."""

    def test_roundtrip(self):
        """Parquet bytes read back to the same id/vector rows."""
        import io

        import pyarrow.parquet as pq

        rows = [
            {"id": "n1", "vector": [0.1, 0.2, 0.3]},
            {"id": "n2", "vector": [0.4, 0.5, 0.6]},
        ]
        data = ccx_mapping.embeddings_to_parquet_bytes(rows)
        assert isinstance(data, bytes)
        table = pq.read_table(io.BytesIO(data))
        out = table.to_pylist()
        assert out[0]["id"] == "n1"
        assert out[1]["id"] == "n2"
        assert [round(v, 1) for v in out[0]["vector"]] == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# Task 2.7 — templates -> context / SHACL / app graph
# ---------------------------------------------------------------------------


def _person_template():
    return {
        "id": "t-person",
        "name": "Person",
        "template_type": "node",
        "icon": "user",
        "color": "#ff0000",
        "properties": [
            {
                "name": "age",
                "display_name": "Age",
                "property_type": "integer",
                "required": True,
            },
            {
                "name": "bio",
                "display_name": "Biography",
                "property_type": "text",
                "required": False,
            },
        ],
    }


class TestTemplatesToContext:
    """templates_to_context maps template/property names to full-IRI terms."""

    def test_namespace_and_terms(self):
        """The context defines cc and maps type + property names to full IRIs."""
        ctx = ccx_mapping.templates_to_context([_person_template()])
        context = ctx["@context"]
        assert context["cc"] == "https://chaoscypher.com/ns/"
        assert context["Person"]["@id"] == "https://chaoscypher.com/ns/Person"
        assert context["Person"]["@type"] == "@id"
        assert context["age"] == "https://chaoscypher.com/ns/age"
        assert context["bio"] == "https://chaoscypher.com/ns/bio"

    def test_names_with_spaces_map_to_encoded_full_iri(self):
        """A template/property name with spaces maps to a percent-encoded full IRI."""
        template = {
            "id": "t-wf",
            "name": "Works For",
            "template_type": "node",
            "properties": [
                {
                    "name": "start date",
                    "property_type": "date",
                    "required": True,
                }
            ],
        }
        ctx = ccx_mapping.templates_to_context([template])
        context = ctx["@context"]
        assert context["Works For"]["@id"] == "https://chaoscypher.com/ns/Works%20For"
        assert context["Works For"]["@type"] == "@id"
        assert context["start date"] == "https://chaoscypher.com/ns/start%20date"


class TestTemplatesToShacl:
    """templates_to_shacl emits parseable Turtle with NodeShapes."""

    def test_parses_and_has_nodeshape(self):
        """The Turtle parses and contains at least one sh:NodeShape."""
        ttl = ccx_mapping.templates_to_shacl([_person_template()])
        assert isinstance(ttl, bytes)
        graph = rdflib.Graph()
        graph.parse(data=ttl.decode("utf-8"), format="turtle")
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        node_shapes = list(graph.subjects(rdflib.RDF.type, sh.NodeShape))
        assert len(node_shapes) >= 1

    def test_required_property_has_min_count(self):
        """A required property yields a sh:property with sh:minCount 1."""
        ttl = ccx_mapping.templates_to_shacl([_person_template()])
        graph = rdflib.Graph()
        graph.parse(data=ttl.decode("utf-8"), format="turtle")
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        min_counts = list(graph.objects(predicate=sh.minCount))
        assert any(int(mc) == 1 for mc in min_counts)

    def test_edge_template_skipped(self):
        """Edge-type templates do not produce NodeShapes."""
        edge_template = {
            "id": "t-edge",
            "name": "Knows",
            "template_type": "edge",
            "properties": [],
        }
        ttl = ccx_mapping.templates_to_shacl([edge_template])
        graph = rdflib.Graph()
        graph.parse(data=ttl.decode("utf-8"), format="turtle")
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        assert list(graph.subjects(rdflib.RDF.type, sh.NodeShape)) == []

    def test_name_with_spaces_parses(self):
        """A template/property name with spaces emits valid, parseable Turtle."""
        template = {
            "id": "t-wf",
            "name": "Works For",
            "template_type": "node",
            "properties": [
                {
                    "name": "start date",
                    "property_type": "date",
                    "required": True,
                }
            ],
        }
        ttl = ccx_mapping.templates_to_shacl([template])
        graph = rdflib.Graph()
        # Must not raise on a name containing a space.
        graph.parse(data=ttl.decode("utf-8"), format="turtle")
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        target_classes = list(graph.objects(predicate=sh.targetClass))
        assert any(str(tc) == "https://chaoscypher.com/ns/Works%20For" for tc in target_classes)
        paths = list(graph.objects(predicate=sh.path))
        assert any(str(p) == "https://chaoscypher.com/ns/start%20date" for p in paths)


class TestTemplatesToAppGraph:
    """templates_to_app_graph carries editor metadata keyed by template IRI."""

    def test_carries_icon_color_and_properties(self):
        """Each template member carries icon, color, and full PropertyDefinitions."""
        graph = ccx_mapping.templates_to_app_graph([_person_template()])
        member = graph["@graph"][0]
        assert member["@id"] == "urn:ccx:chaoscypher:template/t-person"
        assert member["icon"] == "user"
        assert member["color"] == "#ff0000"
        assert member["properties"][0]["name"] == "age"


# ---------------------------------------------------------------------------
# Task 2.8 — app_named_graph
# ---------------------------------------------------------------------------


class TestAppNamedGraph:
    """app_named_graph is a thin {'@graph': members} wrapper."""

    def test_wraps_members(self):
        """The members list is wrapped under @graph."""
        members = [{"@id": "a"}, {"@id": "b"}]
        assert ccx_mapping.app_named_graph(members) == {"@graph": members}

    def test_empty(self):
        """An empty members list yields an empty graph."""
        assert ccx_mapping.app_named_graph([]) == {"@graph": []}
