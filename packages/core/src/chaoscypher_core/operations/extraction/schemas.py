# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic schemas for ChunkExtractionTask JSON columns.

These models lock down the canonical shape of ``raw_entities`` and
``raw_relationships`` as written by ``parse_extraction_output`` (the
line-format parser used by ``AIEntityExtractor.extract_single_chunk``)
and decorated by ``chunk_extraction_service`` before persistence.

They are validated **on write** (before ``adapter.complete_chunk_task_with_output``)
so a malformed extraction result never lands in the DB, and **on read**
(by the finalizer's aggregation step) so legacy or drifted JSON in the
DB raises ``DataIntegrityError`` rather than silently propagating to the
graph commit phase.

``model_config = ConfigDict(extra="allow")`` is intentional: this is a
tightening of existing behaviour, not a redesign. Future extraction
features may attach additional keys (e.g. domain-specific annotations)
and we do not want this validator to be the bottleneck for that.
Required fields, however, are enforced — those are the load-bearing
contract between the parser and the aggregator.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chaoscypher_core.exceptions import DataIntegrityError


class RawEntity(BaseModel):
    """Canonical shape for an entity emitted by an LLM chunk extraction.

    Source: ``parse_entity_line`` in
    ``services/sources/engine/extraction/utils/line_parser.py``. Properties
    (``properties``) are attached afterwards by ``apply_properties_to_entities``.
    The ``chunk_index`` annotation is added by ``chunk_extraction_service``
    just before persistence so each entity carries its parent chunk's
    ordinal position.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(..., description="Entity name as parsed from the E| line.")
    type: str = Field(..., description="Entity type (template name or 'UNKNOWN').")
    description: str = Field(default="", description="Free-text description.")
    aliases: list[str] = Field(default_factory=list, description="Proper-name aliases.")
    confidence: float = Field(..., description="LLM-reported confidence in [0.0, 1.0].")
    sent_ref: str = Field(..., description="Sentence reference like 'S3' or 'S2-S5'.")
    # Optional (attached conditionally by the parser / property merger).
    descriptors: list[str] | None = Field(default=None)
    rejected_aliases: list[str] | None = Field(default=None)
    properties: dict[str, Any] | None = Field(default=None)
    # Added by chunk_extraction_service before persistence.
    chunk_index: int | None = Field(default=None, ge=0)


class RawRelationship(BaseModel):
    """Canonical shape for a relationship emitted by an LLM chunk extraction.

    Source: ``parse_relationship_line``. ``source`` and ``target`` are
    **chunk-local 0-based integer indices** into the chunk's entity list;
    they are remapped to global indices by ``aggregate_chunk_results``.
    """

    model_config = ConfigDict(extra="allow")

    source: int = Field(..., ge=0, description="Chunk-local 0-based source entity index.")
    target: int = Field(..., ge=0, description="Chunk-local 0-based target entity index.")
    type: str = Field(..., description="Relationship type (or fallback 'related_to').")
    confidence: float = Field(..., description="LLM-reported confidence in [0.0, 1.0].")
    justification: str = Field(default="", description="Free-text justification.")
    sent_ref: str = Field(..., description="Sentence reference like 'S3' or 'S2-S5'.")
    # Added by chunk_extraction_service before persistence.
    chunk_index: int | None = Field(default=None, ge=0)


def _truncate(payload: Any, *, limit: int = 500) -> str:
    """Render *payload* as a short string suitable for log lines."""
    text = repr(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def validate_raw_entities(
    items: list[dict[str, Any]] | None,
    *,
    chunk_task_id: str | None,
    stage: str,
    logger: Any,
) -> list[dict[str, Any]]:
    """Validate every entity dict against ``RawEntity``.

    Args:
        items: Raw entity dicts (typically the contents of
            ``ChunkExtractionTask.raw_entities``). ``None`` is treated as
            an empty list.
        chunk_task_id: The owning chunk-task id, included in the log
            event and the raised exception's details.
        stage: ``"write"`` or ``"read"`` — included verbatim in the log
            event so dashboards can distinguish a writer-side failure
            (extraction handler bug) from a reader-side failure (DB drift).
        logger: ``structlog`` logger to fire ``chunk_task_schema_drift``
            on validation failure.

    Returns:
        The original list (unchanged) on success.

    Raises:
        DataIntegrityError: First validation failure aborts; the bad
            payload is logged at warning level under
            ``chunk_task_schema_drift`` and surfaced in ``details``.
    """
    if not items:
        return items or []
    for index, item in enumerate(items):
        try:
            RawEntity.model_validate(item)
        except Exception as exc:  # pydantic.ValidationError
            logger.warning(
                "chunk_task_schema_drift",
                chunk_task_id=chunk_task_id,
                stage=stage,
                kind="entity",
                index=index,
                payload=_truncate(item),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            message = (
                f"Entity schema validation failed at chunk_task={chunk_task_id} "
                f"raw_entities[{index}] (stage={stage}): {exc}"
            )
            raise DataIntegrityError(
                message,
                details={
                    "chunk_task_id": chunk_task_id,
                    "stage": stage,
                    "kind": "entity",
                    "index": index,
                },
            ) from exc
    return items


def validate_raw_relationships(
    items: list[dict[str, Any]] | None,
    *,
    chunk_task_id: str | None,
    stage: str,
    logger: Any,
) -> list[dict[str, Any]]:
    """Validate every relationship dict against ``RawRelationship``.

    See ``validate_raw_entities`` for parameter semantics.
    """
    if not items:
        return items or []
    for index, item in enumerate(items):
        try:
            RawRelationship.model_validate(item)
        except Exception as exc:  # pydantic.ValidationError
            logger.warning(
                "chunk_task_schema_drift",
                chunk_task_id=chunk_task_id,
                stage=stage,
                kind="relationship",
                index=index,
                payload=_truncate(item),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            message = (
                f"Relationship schema validation failed at chunk_task={chunk_task_id} "
                f"raw_relationships[{index}] (stage={stage}): {exc}"
            )
            raise DataIntegrityError(
                message,
                details={
                    "chunk_task_id": chunk_task_id,
                    "stage": stage,
                    "kind": "relationship",
                    "index": index,
                },
            ) from exc
    return items


__all__ = [
    "RawEntity",
    "RawRelationship",
    "validate_raw_entities",
    "validate_raw_relationships",
]
