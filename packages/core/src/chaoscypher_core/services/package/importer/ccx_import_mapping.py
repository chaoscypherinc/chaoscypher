# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure CCX 3.0 -> Chaos Cypher domain mapping (no I/O, no backend).

This module is the inverse of ``services.export.ccx_mapping``: it consumes the
JSON-LD objects and ``sources.jsonl`` records that a CCX 3.0 package carries and
turns them into the plain kwargs/dicts the importer (Task 4.3) feeds into the
storage layer via ``NodeCreate`` / ``EdgeCreate`` and the source/chunk repos.

It is deliberately dependency-free (stdlib + the sibling ``ccx_identity``
helpers) so it stays import-linter clean (CC010/CC012): no adapters, no
SQLModel, no ``ccx`` package needed. All functions are pure — the importer owns
all I/O and template/IRI resolution.

Identity contract
-----------------
Each function returns the entity's CCX ``@id`` (its ``ccx_iri``) unchanged so
the importer can upsert by it. A foreign IRI (one we did not mint) is kept
verbatim as a cross-package merge key; for IRIs we minted, the recovered local
id is surfaced too (via :func:`ccx_identity.local_id_from_iri`) for callers
that want to reuse the original local id.

Template resolution (the ``@type`` term -> a ``template_id``) is intentionally
NOT done here — it needs the template registry, which is a Phase-4.3 concern.
:func:`jsonld_entity_to_node` therefore returns the raw ``@type`` term in
``type_term`` so the importer can map it to a ``template_id`` after it has
imported the ``chaoscypher.templates`` graph.
"""

from __future__ import annotations

import hashlib
from typing import Any

from chaoscypher_core.services.export import ccx_identity


# Reserved JSON-LD keys that are never node properties or simple-edge
# predicates. Mirrors ``ccx_mapping.RESERVED_KEYS`` plus ``@context`` so the
# inverse mapping never treats framing/structural keys as domain data.
_RESERVED_KEYS = {"@id", "@type", "@context", "name", "source"}

# The relationship-resource terms the exporter writes — stripped before the
# remaining terms are collected as edge ``properties``.
_RELATIONSHIP_KEYS = {
    "@id",
    "@type",
    "ccx:subject",
    "ccx:predicate",
    "ccx:object",
    "ccx:relationshipTemplate",
}

# The fallback type term the exporter emits when a node has no template/entity
# type. The importer should treat this as "no concrete type", not a real class.
_GENERIC_TYPE_TERM = "ccx:Entity"


def _is_object_ref(value: Any) -> bool:
    """True when ``value`` is a JSON-LD object reference ``{"@id": <iri>}``."""
    return isinstance(value, dict) and isinstance(value.get("@id"), str)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def jsonld_entity_to_node(obj: dict) -> tuple[str, dict]:
    """Map a JSON-LD node object to ``(ccx_iri, node_create_kwargs)``.

    Inverse of :func:`ccx_mapping.node_to_jsonld`. The returned kwargs dict is
    NOT a ready ``NodeCreate`` — it omits ``template_id`` (a Phase-4.3 concern
    that needs the template registry). Instead it carries everything the
    importer needs to finish the mapping:

    * ``label`` — from the JSON-LD ``name`` term (``NodeCreate.label``).
    * ``entity_type`` — the ``@type`` term, except the generic ``ccx:Entity``
      sentinel maps to ``None`` (``NodeCreate.entity_type``).
    * ``type_term`` — the raw ``@type`` term verbatim, so the importer can
      resolve it to a ``template_id`` via the imported template registry.
    * ``properties`` — every remaining term that is neither reserved nor an
      object reference (object refs are simple edges, handled by
      :func:`plain_triples_to_edges`).
    * ``local_id`` — the recovered local id when ``@id`` is one we minted,
      else ``None`` (a foreign IRI kept only as a merge key).

    Args:
        obj: A JSON-LD node object from the ``knowledge`` default graph.

    Returns:
        ``(ccx_iri, kwargs)`` where ``ccx_iri`` is the object's ``@id``.
    """
    ccx_iri = obj["@id"]
    type_term = obj.get("@type")
    entity_type = None if type_term in (None, _GENERIC_TYPE_TERM) else type_term

    properties: dict[str, Any] = {}
    for key, value in obj.items():
        if key in _RESERVED_KEYS:
            continue
        # Object references (single or list) are simple edges, not properties.
        if _is_object_ref(value):
            continue
        if isinstance(value, list) and any(_is_object_ref(item) for item in value):
            continue
        properties[key] = value

    kwargs: dict[str, Any] = {
        "label": obj.get("name"),
        "entity_type": entity_type,
        "type_term": type_term,
        "properties": properties,
        "local_id": ccx_identity.local_id_from_iri(ccx_iri),
    }
    return ccx_iri, kwargs


# ---------------------------------------------------------------------------
# Edges — reified ccx:Relationship resources
# ---------------------------------------------------------------------------


def relationship_to_edge(resource: dict) -> dict:
    """Map a ``ccx:Relationship`` resource to an edge dict.

    Inverse of :func:`ccx_mapping._relationship_resource`. Object references
    are unwrapped to bare IRIs; the relationship template ref (if any) becomes
    ``template_iri``; every non-reserved term becomes an edge ``property``.

    Args:
        resource: A ``{"@type": "ccx:Relationship", ...}`` JSON-LD resource.

    Returns:
        ``{ccx_iri, subject_iri, object_iri, predicate, template_iri, properties}``
        where ``template_iri`` is ``None`` when the resource carried none.
    """
    subject = resource.get("ccx:subject") or {}
    obj = resource.get("ccx:object") or {}
    template_ref = resource.get("ccx:relationshipTemplate")
    template_iri = template_ref.get("@id") if isinstance(template_ref, dict) else None

    properties = {key: value for key, value in resource.items() if key not in _RELATIONSHIP_KEYS}

    return {
        "ccx_iri": resource["@id"],
        "subject_iri": subject.get("@id"),
        "object_iri": obj.get("@id"),
        "predicate": resource.get("ccx:predicate"),
        "template_iri": template_iri,
        "properties": properties,
    }


# ---------------------------------------------------------------------------
# Edges — plain triples attached to a node object
# ---------------------------------------------------------------------------


def _deterministic_triple_iri(subject_iri: str, predicate: str, object_iri: str) -> str:
    """Stable ``rel`` IRI for a plain triple, for idempotent upsert.

    The IRI is a pure function of ``(subject, predicate, object)`` so importing
    the same package twice (a triple has no own ``@id`` in JSON-LD) upserts the
    same edge row rather than duplicating it.
    """
    # sha1 here is a non-cryptographic stable id (idempotency key), not a
    # security control — collision resistance is not required.
    digest = hashlib.sha1(f"{subject_iri}|{predicate}|{object_iri}".encode()).hexdigest()
    return ccx_identity.mint_iri("rel", digest)


def plain_triples_to_edges(obj: dict) -> list[dict]:
    """Extract simple-edge triples from a node object.

    Inverse of :func:`ccx_mapping._attach_predicate`. For each non-reserved
    predicate whose value is an object reference ``{"@id": ...}`` (or a list
    containing object references), yield an edge dict. Literal-valued terms and
    reserved keys are skipped (those are node properties / structural keys).

    Each edge gets a DETERMINISTIC ``ccx_iri`` derived from
    ``(subject, predicate, object)`` so re-import is idempotent.

    Args:
        obj: A JSON-LD node object (the subject of the triples).

    Returns:
        A list of ``{ccx_iri, subject_iri, predicate, object_iri}`` dicts; one
        per object reference, in document order.
    """
    subject_iri = obj["@id"]
    edges: list[dict] = []
    for predicate, value in obj.items():
        if predicate in _RESERVED_KEYS:
            continue
        refs: list[dict] = []
        if _is_object_ref(value):
            refs = [value]
        elif isinstance(value, list):
            refs = [item for item in value if _is_object_ref(item)]
        for ref in refs:
            object_iri = ref["@id"]
            edges.append(
                {
                    "ccx_iri": _deterministic_triple_iri(subject_iri, predicate, object_iri),
                    "subject_iri": subject_iri,
                    "predicate": predicate,
                    "object_iri": object_iri,
                }
            )
    return edges


# ---------------------------------------------------------------------------
# Sources / chunks
# ---------------------------------------------------------------------------


def ccx_chunk_to_chunk(rec: dict, full_text: str | None) -> dict:
    """Map a ``ccx:Chunk`` record to a chunk dict for the importer.

    Inverse of the chunk branch of :func:`ccx_mapping.source_records`. A chunk
    carries EITHER an offset ``selector`` into the source's full text OR inline
    ``content`` — never both:

    * ``selector`` present: ``content`` is resolved as ``full_text[start:end]``
      when ``full_text`` is available, else an empty string (the importer has
      the offsets but no text asset to slice). ``char_start`` / ``char_end``
      carry the selector offsets.
    * inline ``content``: passed through verbatim; offsets are ``None``.

    Args:
        rec: A ``{"@type": "ccx:Chunk", ...}`` record.
        full_text: The source's full text (from the ``text`` asset), or
            ``None`` when the package only carried inline chunk content.

    Returns:
        ``{content, char_start, char_end, source_iri, ccx_iri, citations}``.
        ``citations`` is the raw ``ccx:citation`` list (empty when absent).
    """
    source_ref = rec.get("source") or {}
    selector = rec.get("selector")

    if isinstance(selector, dict):
        char_start = selector.get("start")
        char_end = selector.get("end")
        if full_text is not None and char_start is not None and char_end is not None:
            content = full_text[char_start:char_end]
        else:
            content = ""
    else:
        char_start = None
        char_end = None
        content = rec.get("content", "")

    return {
        "ccx_iri": rec.get("@id"),
        "source_iri": source_ref.get("@id"),
        "content": content,
        "char_start": char_start,
        "char_end": char_end,
        "citations": rec.get("ccx:citation") or [],
    }
