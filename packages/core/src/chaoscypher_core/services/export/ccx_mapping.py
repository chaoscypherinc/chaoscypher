# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure CCX 3.0 mapping: domain dicts -> JSON-LD / RDF (no I/O, no backend).

Every function here takes plain dicts (the shapes the SQLite repos emit
via ``_entity_to_dict``) and returns JSON-LD objects, RDF bytes, or
relationship-resource tuples. There is deliberately no I/O and no adapter
import: the exporter (Phase 3) feeds these functions repo dicts and writes
the results into a CCX package; the importer (Phase 4) reverses them. Keep
this module dependency-free (stdlib + ``ccx`` + sibling ``ccx_identity``)
so it stays import-linter clean (CC010/CC012).

CCX 3.0 record forms produced here (matching the ccx-format fixtures):

* Source: ``{"@id", "@type": "ccx:Source", "sourceMode": "derived-only",
  "extractedBy"?, "title"?, "chunking"?, "text"?}``.
* Chunk (one JSONL line each): ``{"@id": "<src-iri>#chunk-N",
  "@type": "ccx:Chunk", "source": {"@id": <src-iri>}, ...}`` with EITHER a
  ``selector`` (TextPositionSelector offset, requires the source ``text``
  asset) OR inline ``content`` — never both.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from chaoscypher_core.services.export import ccx_identity


# Reserved JSON-LD keys on a node object that a simple-edge predicate must not
# overwrite. A simple edge whose label collides with one of these (or with an
# existing node property of the same name) is reified into a ``ccx:Relationship``
# resource instead of being attached as a bare top-level key.
RESERVED_KEYS = {"@id", "@type", "@context", "name", "source"}

# Sentinel the exporter swaps for the real full-text asset path once it has
# written the text asset into the package. Mapping is pure, so it cannot know
# the final asset path — it only records that the source HAS a full-text asset.
TEXT_ASSET_PENDING = "__ccx_full_text_asset__"

# Application namespace for editor-side terms (templates -> context / SHACL).
_CC_NAMESPACE = "https://chaoscypher.com/ns/"

# Minimal property_type -> xsd datatype map for SHACL. Unknown types omit the
# datatype constraint (kept minimal per the plan).
_XSD_BY_PROPERTY_TYPE = {
    "string": "xsd:string",
    "text": "xsd:string",
    "integer": "xsd:integer",
    "float": "xsd:decimal",
    "boolean": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "url": "xsd:anyURI",
    "email": "xsd:string",
}


# ---------------------------------------------------------------------------
# Task 2.2 — node mapping
# ---------------------------------------------------------------------------


def node_to_jsonld(node: dict, templates_by_id: dict[str, dict]) -> dict:
    """Map a GraphNode dict to a JSON-LD node object.

    ``@type`` resolves to the node's template name, falling back to the
    extracted ``entity_type``, then to ``"ccx:Entity"``. Domain
    ``properties`` are spread as terms but never overwrite the reserved
    ``@id`` / ``@type`` / ``name`` keys (those win).

    Args:
        node: A GraphNode dict (``id, ccx_iri?, label, entity_type,
            template_id, properties``).
        templates_by_id: Map of template id -> template dict, for resolving
            the type term from the template ``name``.

    Returns:
        A JSON-LD node object.
    """
    template_id = node.get("template_id")
    template = templates_by_id.get(template_id) if template_id is not None else None
    type_term = (template or {}).get("name") or node.get("entity_type") or "ccx:Entity"

    obj: dict[str, Any] = {
        "@id": ccx_identity.resolve_iri("node", node),
        "@type": type_term,
        "name": node.get("label"),
    }
    for key, value in (node.get("properties") or {}).items():
        obj.setdefault(key, value)
    return obj


# ---------------------------------------------------------------------------
# Task 2.3 — edge mapping
# ---------------------------------------------------------------------------


def edge_to_jsonld(
    edge: dict,
    nodes_iri: dict[str, str],
    templates_by_id: dict[str, dict],
) -> tuple:
    """Map a GraphEdge dict to a triple tuple or a relationship resource.

    A property-less edge is a plain triple and is returned as
    ``("triple", subject_iri, predicate, {"@id": object_iri})`` so the
    caller can attach it to the subject node. An edge carrying
    ``properties`` is reified into a relationship resource and returned as
    ``("relationship", resource_dict)``.

    Args:
        edge: A GraphEdge dict (``id, ccx_iri?, label, template_id,
            source_node_id, target_node_id, properties``).
        nodes_iri: Map of local node id -> resolved node IRI.
        templates_by_id: Reserved for future template-aware predicates;
            the predicate is currently the edge ``label``.

    Returns:
        Either a ``("triple", s, p, o)`` 4-tuple or a
        ``("relationship", resource)`` 2-tuple.
    """
    predicate = edge.get("label")
    subject_iri = nodes_iri[edge["source_node_id"]]
    object_iri = nodes_iri[edge["target_node_id"]]
    properties = edge.get("properties") or {}

    if not properties:
        return ("triple", subject_iri, predicate, {"@id": object_iri})

    return ("relationship", _relationship_resource(edge, subject_iri, object_iri))


def _relationship_resource(edge: dict, subj_iri: str, obj_iri: str) -> dict:
    """Build a reified ``ccx:Relationship`` resource for an edge.

    Shared by the property-bearing path of :func:`edge_to_jsonld` and the
    predicate-collision fallback in :func:`build_knowledge_graph`, so both
    produce an identically shaped resource. The edge ``template_id`` (when
    present) is carried as ``ccx:relationshipTemplate``; edge ``properties``
    are spread as terms but never overwrite the reserved relationship keys.

    Args:
        edge: A GraphEdge dict (``id, ccx_iri?, label, template_id?,
            properties?``).
        subj_iri: The resolved subject node IRI.
        obj_iri: The resolved object node IRI.

    Returns:
        A ``ccx:Relationship`` JSON-LD resource object.
    """
    resource: dict[str, Any] = {
        "@id": ccx_identity.resolve_iri("rel", edge),
        "@type": "ccx:Relationship",
        "ccx:subject": {"@id": subj_iri},
        "ccx:predicate": edge.get("label"),
        "ccx:object": {"@id": obj_iri},
    }
    template_id = edge.get("template_id")
    if template_id:
        resource["ccx:relationshipTemplate"] = {
            "@id": ccx_identity.mint_iri("template", template_id)
        }
    for key, value in (edge.get("properties") or {}).items():
        resource.setdefault(key, value)
    return resource


def _attach_predicate(node_obj: dict, predicate: str, object_ref: dict) -> None:
    """Attach a simple-edge predicate to a subject node object.

    The first value for a predicate is stored directly; a second value for
    the same predicate promotes it to a list (and subsequent values append).

    Args:
        node_obj: The subject node JSON-LD object to mutate.
        predicate: The predicate term (edge label).
        object_ref: The object reference (``{"@id": iri}``).
    """
    if predicate not in node_obj:
        node_obj[predicate] = object_ref
        return
    existing = node_obj[predicate]
    if isinstance(existing, list):
        existing.append(object_ref)
    else:
        node_obj[predicate] = [existing, object_ref]


# ---------------------------------------------------------------------------
# Task 2.4 — knowledge graph assembly
# ---------------------------------------------------------------------------


def build_knowledge_graph(
    nodes: list[dict],
    edges: list[dict],
    templates_by_id: dict[str, dict],
) -> dict:
    """Assemble nodes + edges into a ``{"@graph": [...]}`` object.

    Node objects and reified relationship resources are members of the
    graph. Simple-edge predicates are attached to their subject node object
    (promoted to a list on a repeated predicate). Empty inputs yield an
    empty graph — the à-la-carte "sources-only" export case.

    Args:
        nodes: GraphNode dicts.
        edges: GraphEdge dicts.
        templates_by_id: Map of template id -> template dict.

    Returns:
        ``{"@graph": [node objects + relationship resources]}``.
    """
    node_objs: dict[str, dict] = {}
    nodes_iri: dict[str, str] = {}
    members: list[dict] = []
    # Per-subject set of predicates already attached as bare simple-edge keys,
    # so a repeated simple edge promotes to a list (existing behavior) rather
    # than being mistaken for a property collision.
    attached_predicates: dict[str, set[str]] = {}

    for node in nodes:
        obj = node_to_jsonld(node, templates_by_id)
        node_objs[node["id"]] = obj
        nodes_iri[node["id"]] = obj["@id"]
        members.append(obj)

    for edge in edges:
        source_id = edge.get("source_node_id")
        target_id = edge.get("target_node_id")
        # Skip dangling edges: either endpoint absent from this batch (a
        # legitimate case for source-scoped / partial exports). The exporter
        # layer counts/warns separately — this function stays pure.
        if source_id not in nodes_iri or target_id not in nodes_iri:
            continue

        mapped = edge_to_jsonld(edge, nodes_iri, templates_by_id)
        if mapped[0] == "triple":
            _, subject_iri, predicate, object_ref = mapped
            subject_obj = node_objs[source_id]
            already = attached_predicates.setdefault(source_id, set())
            # A bare predicate that collides with an existing key on the
            # subject (a node property) or a reserved JSON-LD key would
            # corrupt that value — reify it as a ccx:Relationship instead. A
            # predicate we ourselves attached earlier is a repeat and is
            # promoted to a list by _attach_predicate.
            collides = predicate in subject_obj and predicate not in already
            if collides or predicate in RESERVED_KEYS:
                members.append(_relationship_resource(edge, subject_iri, nodes_iri[target_id]))
            else:
                _attach_predicate(subject_obj, predicate, object_ref)
                already.add(predicate)
        else:
            members.append(mapped[1])

    return {"@graph": members}


# ---------------------------------------------------------------------------
# Task 2.5 — source + chunk records
# ---------------------------------------------------------------------------


def _citation_to_jsonld(citation: dict) -> dict:
    """Map one chunk citation to the CCX citation term shape.

    Emits the cited-entity reference as ``ccx:citation: {"@id": <node IRI>}``
    so the link to the knowledge node round-trips, alongside the optional
    ``ccx:confidence`` / ``ccx:extractionMethod`` provenance. The cited node's
    IRI is resolved from the citation's own node ``ccx_iri`` when present (a
    cross-package import set it), else minted from the node's local id (the
    citation repo dict carries it as ``entity_uri``; ``entity_id`` is accepted
    as an alias for robustness against shape drift).
    """
    mapped: dict[str, Any] = {}

    entity_iri = _citation_entity_iri(citation)
    if entity_iri is not None:
        mapped["ccx:citation"] = {"@id": entity_iri}

    if citation.get("confidence") is not None:
        mapped["ccx:confidence"] = citation["confidence"]
    if citation.get("extraction_method") is not None:
        mapped["ccx:extractionMethod"] = citation["extraction_method"]
    return mapped


def _citation_entity_iri(citation: dict) -> str | None:
    """Resolve the cited entity's node IRI from a citation repo dict.

    A persisted node ``ccx_iri`` on the citation wins (keeps the entity
    anchored to its original cross-package identity). Otherwise the IRI is
    minted from the cited node's local id — ``entity_uri`` is the real
    ``SourceCitation`` column (it stores the node id), with ``entity_id`` kept
    as a defensive alias. Returns ``None`` when no entity reference is present.
    """
    node_ccx_iri = citation.get("node_ccx_iri") or citation.get("entity_ccx_iri")
    if node_ccx_iri:
        return str(node_ccx_iri)
    entity_id = citation.get("entity_uri") or citation.get("entity_id")
    if entity_id:
        return ccx_identity.mint_iri("node", str(entity_id))
    return None


def chunk_iri(src_iri: str, chunk: dict[str, Any]) -> str:
    """Deterministic ``ccx:Chunk`` ``@id`` = source IRI + chunk-index fragment.

    Shared by ``source_records`` (which emits the chunk records) and the embedding
    sidecar (which keys chunk vectors), so an exported chunk vector and its
    ``ccx:Chunk`` record carry the IDENTICAL ``@id`` — the importer joins them by
    that string. Falls back to the chunk's local id when it has no index.
    """
    fragment = chunk.get("chunk_index")
    if fragment is None:
        fragment = chunk.get("id")
    return f"{src_iri}#chunk-{fragment}"


def source_records(
    source: dict,
    chunks: list[dict],
    chunking_config: dict | None,
) -> list[dict]:
    """Build a ``ccx:Source`` record plus one ``ccx:Chunk`` record per chunk.

    The source record is graduated: optional ``extractedBy`` / ``title`` /
    ``chunking`` / ``text`` fields are omitted when absent rather than set
    to null. When the source has ``full_text``, ``text`` is set to
    :data:`TEXT_ASSET_PENDING` (the exporter replaces this sentinel with the
    real asset path).

    Each chunk carries EITHER a ``selector`` (TextPositionSelector — used
    only when the source has ``full_text`` AND the chunk has both offsets)
    OR inline ``content`` — never both. Citations, if present, map to a
    ``ccx:citation`` list.

    Args:
        source: A SourceRow dict (``id, ccx_iri?, title?, full_text?,
            extraction_mode?, extraction_domain?, tags?``). ``extraction_mode``
            supplies ``extractedBy``; ``extraction_domain`` supplies
            ``extractionDomain`` (the graph view's per-source icon); ``tags``
            (a list of names) supplies ``keywords``.
        chunks: DocumentChunk dicts (``id, chunk_index, content,
            char_start, char_end``, optional ``citations``).
        chunking_config: Provenance for the ``chunking`` field, or ``None``.

    Returns:
        ``[source_record, *chunk_records]``.
    """
    src_iri = ccx_identity.resolve_iri("source", source)

    src_rec: dict[str, Any] = {
        "@id": src_iri,
        "@type": "ccx:Source",
        "sourceMode": "derived-only",
    }
    extracted_by = source.get("extraction_mode")
    if extracted_by:
        src_rec["extractedBy"] = extracted_by
    # The extraction domain drives the per-source group icon in the graph view;
    # carry it so an imported source shows the same domain pictogram an
    # extracted one does (without it the imported source-group node is iconless).
    extraction_domain = source.get("extraction_domain")
    if extraction_domain:
        src_rec["extractionDomain"] = extraction_domain
    if source.get("tags"):
        # schema.org-aligned keyword list; round-trips a source's tags and feeds
        # the package-level manifest ``tags`` the hub search indexes.
        src_rec["keywords"] = list(source["tags"])
    if source.get("title"):
        src_rec["title"] = source["title"]
    if chunking_config:
        src_rec["chunking"] = chunking_config

    has_full_text = bool(source.get("full_text"))
    if has_full_text:
        src_rec["text"] = TEXT_ASSET_PENDING

    records: list[dict] = [src_rec]

    for chunk in chunks:
        chunk_rec: dict[str, Any] = {
            "@id": chunk_iri(src_iri, chunk),
            "@type": "ccx:Chunk",
            "source": {"@id": src_iri},
        }

        char_start = chunk.get("char_start")
        char_end = chunk.get("char_end")
        if has_full_text and char_start is not None and char_end is not None:
            chunk_rec["selector"] = {
                "type": "TextPositionSelector",
                "start": char_start,
                "end": char_end,
            }
        else:
            # Inline-content fallback: only set ``content`` when there is a
            # non-empty value. A chunk with neither a selector nor usable
            # content carries neither key rather than emitting ``content: null``.
            content = chunk.get("content")
            if content:
                chunk_rec["content"] = content

        citations = chunk.get("citations")
        if citations:
            chunk_rec["ccx:citation"] = [_citation_to_jsonld(c) for c in citations]

        records.append(chunk_rec)

    return records


# ---------------------------------------------------------------------------
# Task 2.6 — embeddings
# ---------------------------------------------------------------------------


def embedding_descriptor(
    *,
    coverage: str,
    model: str,
    dimensions: int,
    provider: str | None = None,
    included: bool = False,
) -> dict:
    """Build an embedding provenance / inclusion descriptor.

    Args:
        coverage: Coverage descriptor (e.g. ``"full"`` / ``"partial"``).
        model: Embedding model identifier.
        dimensions: Vector dimensionality.
        provider: Optional provider name; included only when given.
        included: Whether the vectors themselves are bundled in the package.

    Returns:
        ``{"model", "dimensions", "coverage", "included"[, "provider"]}``.
    """
    descriptor: dict[str, Any] = {
        "model": model,
        "dimensions": dimensions,
        "coverage": coverage,
        "included": included,
    }
    if provider is not None:
        descriptor["provider"] = provider
    return descriptor


def embeddings_to_parquet_bytes(rows: list[dict]) -> bytes:
    """Serialize ``{id, vector}`` rows to Parquet bytes.

    pyarrow is imported lazily so this module stays importable without the
    ``ccx-format[embeddings]`` extra installed (only callers that bundle
    vectors need it).

    Args:
        rows: Rows of ``{"id": str, "vector": list[float]}``.

    Returns:
        Parquet-serialized bytes of a ``{id, vector}`` table.
    """
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": [row["id"] for row in rows],
            "vector": [row["vector"] for row in rows],
        }
    )
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Task 2.7 — templates -> context / SHACL / app graph
# ---------------------------------------------------------------------------


def _cc_iri(name: str) -> str:
    """Full Chaos Cypher namespace IRI for a human term, percent-encoded.

    Template / property ``name`` values may carry spaces or punctuation
    (e.g. ``"Works For"``), which cannot appear in a ``cc:<name>`` Turtle
    prefixed name or a bare JSON-LD term. Percent-encoding the local part
    and emitting a full IRI keeps both the generated Turtle and the JSON-LD
    context valid.

    Args:
        name: The human-facing template or property name.

    Returns:
        ``https://chaoscypher.com/ns/<percent-encoded name>``.
    """
    return f"{_CC_NAMESPACE}{urllib.parse.quote(name, safe='')}"


def templates_to_context(templates: list[dict]) -> dict:
    """Build a JSON-LD ``@context`` from templates.

    The ``cc`` prefix binds the Chaos Cypher namespace. Each template
    ``name`` becomes a type term mapped to its full (percent-encoded) IRI
    with ``"@type": "@id"``; each property ``name`` maps to its full IRI.
    Mapping the human term to a full IRI (rather than a ``cc:<name>``
    prefixed name) keeps the context valid when a name contains spaces or
    punctuation, while ``node_to_jsonld`` still uses the human ``name`` as
    the JSON-LD key / ``@type`` term.

    Args:
        templates: Template dicts (``name`` + ``properties`` list).

    Returns:
        ``{"@context": {...}}``.
    """
    context: dict[str, Any] = {"cc": _CC_NAMESPACE}
    for template in templates:
        name = template.get("name")
        if name:
            context[name] = {"@id": _cc_iri(name), "@type": "@id"}
        for prop in template.get("properties") or []:
            prop_name = prop.get("name")
            if prop_name:
                context[prop_name] = _cc_iri(prop_name)
    return {"@context": context}


def templates_to_shacl(templates: list[dict]) -> bytes:
    """Build a minimal SHACL shapes graph (Turtle) from node templates.

    One ``sh:NodeShape`` per node-type template, with ``sh:targetClass`` the
    type term and one ``sh:property [ sh:path <prop> ; sh:minCount 1 ]`` per
    required property. Known ``property_type`` values add an ``sh:datatype``;
    unknown ones omit it. Edge-type templates are skipped.

    Template / property ``name`` values may contain spaces or punctuation,
    which cannot appear in a ``cc:<name>`` Turtle prefixed name. Class and
    property IRIs are therefore emitted as full ``<...>`` angle-bracket IRIs
    with percent-encoded local names; only the ``xsd:`` datatype terms keep a
    prefix (their local names are always safe).

    Args:
        templates: Template dicts.

    Returns:
        UTF-8 Turtle bytes (parseable by rdflib).
    """
    lines: list[str] = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
    ]

    for template in templates:
        if template.get("template_type") != "node":
            continue
        name = template.get("name")
        if not name:
            continue

        class_iri = _cc_iri(name)
        shape_iri = _cc_iri(f"{name}Shape")

        property_blocks: list[str] = []
        for prop in template.get("properties") or []:
            if not prop.get("required"):
                continue
            prop_name = prop.get("name")
            if not prop_name:
                continue
            constraints = [f"sh:path <{_cc_iri(prop_name)}>", "sh:minCount 1"]
            datatype = _XSD_BY_PROPERTY_TYPE.get(prop.get("property_type"))
            if datatype is not None:
                constraints.append(f"sh:datatype {datatype}")
            property_blocks.append("sh:property [ " + " ; ".join(constraints) + " ]")

        lines.append(f"<{shape_iri}> a sh:NodeShape ;")
        if property_blocks:
            lines.append(f"    sh:targetClass <{class_iri}> ;")
            lines.append("    " + " ;\n    ".join(property_blocks) + " .")
        else:
            lines.append(f"    sh:targetClass <{class_iri}> .")
        lines.append("")

    return "\n".join(lines).encode("utf-8")


def templates_to_app_graph(templates: list[dict]) -> dict:
    """Build the editor-metadata named graph for templates.

    Each template becomes a member keyed by its minted template IRI,
    carrying the editor-only metadata (icon, color, full
    PropertyDefinitions) that does not belong in the portable knowledge
    graph but is needed to round-trip the Chaos Cypher editor.

    Args:
        templates: Template dicts.

    Returns:
        ``{"@graph": [...]}`` for the ``chaoscypher.templates`` named graph.
    """
    members: list[dict] = []
    for template in templates:
        member: dict[str, Any] = {
            "@id": ccx_identity.mint_iri("template", template["id"]),
            "name": template.get("name"),
            "template_type": template.get("template_type"),
            "description": template.get("description"),
            "icon": template.get("icon"),
            "color": template.get("color"),
            "properties": template.get("properties") or [],
        }
        members.append(member)
    return {"@graph": members}


# ---------------------------------------------------------------------------
# Task 2.8 — app named-graph wrapper
# ---------------------------------------------------------------------------


def app_named_graph(members: list[dict]) -> dict:
    """Wrap members in a ``{"@graph": [...]}`` object.

    Thin helper for the application named graphs
    (``chaoscypher.workflows`` / ``.lenses`` / ``.statistics``) that carry a
    flat member list with no node/edge assembly.

    Args:
        members: Pre-built JSON-LD member objects.

    Returns:
        ``{"@graph": list(members)}``.
    """
    return {"@graph": list(members)}
