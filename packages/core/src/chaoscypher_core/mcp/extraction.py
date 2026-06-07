# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP extraction orchestrator for Chaos Cypher.

Manages the MCP-driven extraction lifecycle: fetching tasks,
serving chunk text, accepting per-chunk extraction submissions,
tracking progress, and finalizing (parse, dedup, commit).
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from time import monotonic as _monotonic
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.operations.importing.confirmation_gate import (
    gate_decision,
    park_for_confirmation,
    proposal_from_detection,
)
from chaoscypher_core.services.sources.engine.extraction.domains import (
    get_domain_registry,
)
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    detect_extraction_domain,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    apply_properties_to_entities,
)
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_extraction_output,
)
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import (
    SYSTEM_PROMPT,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    split_into_sentences,
)
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_core.services.stage_progress import StageName


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
#  Instruction builders for MCP sub-agents
# ------------------------------------------------------------------ #


def _build_entity_instructions(
    node_templates_str: str,
    entity_exclusions: list[ExclusionRule],
    strict_types: bool,
    entity_examples: str,
) -> str:
    """Build pre-formatted entity extraction instructions for MCP sub-agents.

    Produces a complete, self-contained instruction block with entity types,
    rules, guidelines, and examples baked in.  The consuming sub-agent only
    needs to prepend numbered sentences from the chunk.

    Args:
        node_templates_str: Formatted node template list for the type field.
        entity_exclusions: Domain-specific categories to skip.
        strict_types: Whether to enforce strict type matching.
        entity_examples: Formatted domain-specific extraction examples.

    Returns:
        Complete entity extraction instruction text.

    """
    parts = [
        "Extract entities and their properties from the numbered sentences provided.",
        "",
        "OUTPUT FORMAT:",
        "- E|name|type|aliases|confidence|sent_ref|description",
        "- P|entity_index|key|value",
        "",
        "ENTITY TYPES:",
        node_templates_str,
    ]

    if strict_types:
        parts.append(
            "\nSTRICT: Use ONLY the entity types listed above. "
            "Do NOT invent new types. Skip entities that don't fit any listed type."
        )

    parts.extend(
        [
            "",
            "RULES:",
            "1. Output each entity's E| line followed immediately by its P| lines.",
            "2. Only extract facts explicitly stated in the sentences — do NOT infer.",
            "3. Every E| MUST include sent_ref (S# or S#-S#) pointing to supporting sentence(s).",
            "4. P| entity_index is 0-based from your output order.",
            "",
            "GUIDELINES:",
            "- Extract NAMED entities only — each MUST have a proper name or specific identifier",
            "- NAME must be a real name from the text, NOT a description or invented phrase",
            '  GOOD: "Prince Andrei", "Moscow", "Battle of Austerlitz"',
            '  BAD: "The emotional state of X", "Feelings of sadness"',
            "- DESCRIPTION: Rich, factual summary (2-3 sentences). Aim for 100+ characters.",
            "- ALIASES: Only alternate PROPER NAMES (semicolon-separated). Aim for 2-4 for major entities.",
            '  GOOD: "Andrei; Prince Andrew; Andrew Bolkonsky"',
            '  BAD: "Nieces; The soldiers; Friend"',
            "- PROPERTIES: Titles, roles, occupations, nationalities. Aim for 3-5 per major entity.",
            "- Confidence: 1.0 explicit, 0.7-0.9 implied, 0.5-0.6 uncertain",
            "- Do NOT extract pronouns, vague references, abstract concepts, emotions, or actions",
            "- Expect 5-15 entities per chunk. More than 20 means over-extraction.",
        ]
    )

    if entity_exclusions:
        parts.append("\nSKIP these categories:")
        parts.extend(f"- {rule.as_prompt_text()}" for rule in entity_exclusions)

    if entity_examples:
        parts.extend(["", f"DOMAIN EXAMPLES:\n{entity_examples}"])

    parts.extend(
        [
            "",
            "EXAMPLE (3 entities):",
            "E|Prince Andrei|Character|Andrei; Prince Andrew|0.9|S1-S2|"
            "Military officer and nobleman, eldest son of old Prince Bolkonsky.",
            "P|0|title|Prince",
            "P|0|occupation|Military Officer",
            "E|Napoleon|Character|Emperor Napoleon|1.0|S3|"
            "Emperor of France and military commander.",
            "P|1|title|Emperor",
            "E|Battle of Austerlitz|Event|Austerlitz|1.0|S4|"
            "Major battle of the Napoleonic Wars in December 1805.",
        ]
    )

    return "\n".join(parts)


def _build_relationship_instructions(
    edge_templates_str: str,
    relationship_examples: str,
) -> str:
    """Build pre-formatted relationship extraction instructions for MCP sub-agents.

    Produces a complete, self-contained instruction block with relationship
    types, rules, and examples.  The consuming sub-agent provides numbered
    sentences and a numbered entity list from their entity extraction pass.

    Args:
        edge_templates_str: Formatted edge template list for the type field.
        relationship_examples: Formatted domain-specific relationship examples.

    Returns:
        Complete relationship extraction instruction text.

    """
    parts = [
        "Extract relationships between the entities listed below, based on the numbered sentences.",
        "",
        "OUTPUT FORMAT:",
        "- R|source_index|target_index|type|confidence|sent_ref|justification",
        "",
        "RELATIONSHIP TYPES:",
        edge_templates_str,
        "",
        "RULES:",
        "1. R| indices MUST be valid (0 to N-1 where N = entity count). No self-relationships.",
        "2. Only extract facts explicitly stated — do NOT infer.",
        "3. Every R| MUST include sent_ref pointing to sentence(s) containing BOTH entities.",
        "4. ONLY use entities from the provided list. Skip relationships for missing entities.",
        "",
        "GUIDELINES:",
        "- Extract ALL relationships stated or strongly implied — do not skip obvious connections",
        "- Every entity should have at least one relationship. "
        "Aim for at least N relationships for N entities.",
        "- Prefer specific types (spouse_of, wrote, parent_of) over generic ones (related_to)",
        "- JUSTIFICATION: Full sentence (50+ characters) explaining "
        "the relationship with text evidence",
    ]

    if relationship_examples:
        parts.extend(["", f"DOMAIN EXAMPLES:\n{relationship_examples}"])

    parts.extend(
        [
            "",
            "EXAMPLE:",
            "R|0|2|participates_in|0.9|S4|Prince Andrei fought in the "
            "Battle of Austerlitz as part of the Russian forces",
            "R|1|2|commands|0.8|S4|Napoleon commanded the French forces to victory at Austerlitz",
        ]
    )

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  Line-shape validation
# ------------------------------------------------------------------ #

_ENTITY_FIELD_COUNT = 7  # E|name|type|aliases|confidence|sent_ref|description
_PROPERTY_FIELD_COUNT = 4  # P|entity_index|key|value
_RELATIONSHIP_FIELD_COUNT = 7  # R|src|tgt|type|confidence|sent_ref|justification


def _validate_extraction_lines(
    entities_text: str,
    relationships_text: str,
) -> list[dict[str, Any]]:
    """Validate pipe-separated E|/P|/R| lines; return list of per-line errors.

    Checks:
        - E| lines have 7 fields.
        - P| lines have 4 fields with a non-negative integer entity_index
          that points to an entity actually present in entities_text.
        - R| lines have 7 fields with non-negative integer source/target
          indices that point to entities present in entities_text.

    Args:
        entities_text: Raw E|/P| lines from the client.
        relationships_text: Raw R| lines from the client.

    Returns:
        List of ``{"line_number": int, "line_type": str, "error": str}``
        dicts. Empty list means the submission is well-formed.

    """
    errors: list[dict[str, Any]] = []
    entity_count = 0

    for idx, raw_line in enumerate(entities_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("E|"):
            fields = line.split("|")
            if len(fields) != _ENTITY_FIELD_COUNT:
                errors.append(
                    {
                        "line_number": idx,
                        "line_type": "E",
                        "error": (
                            f"expected {_ENTITY_FIELD_COUNT} pipe-separated fields, "
                            f"got {len(fields)}"
                        ),
                    }
                )
                continue
            entity_count += 1
        elif line.startswith("P|"):
            fields = line.split("|")
            if len(fields) != _PROPERTY_FIELD_COUNT:
                errors.append(
                    {
                        "line_number": idx,
                        "line_type": "P",
                        "error": (
                            f"expected {_PROPERTY_FIELD_COUNT} pipe-separated fields, "
                            f"got {len(fields)}"
                        ),
                    }
                )
                continue
            try:
                entity_index = int(fields[1])
            except ValueError:
                errors.append(
                    {
                        "line_number": idx,
                        "line_type": "P",
                        "error": f"entity_index must be integer, got '{fields[1]}'",
                    }
                )
                continue
            if entity_index < 0:
                errors.append(
                    {
                        "line_number": idx,
                        "line_type": "P",
                        "error": "entity_index must be non-negative",
                    }
                )
            elif entity_index >= entity_count:
                errors.append(
                    {
                        "line_number": idx,
                        "line_type": "P",
                        "error": (
                            f"entity_index {entity_index} out of range "
                            f"(have {entity_count} entities so far)"
                        ),
                    }
                )

    for idx, raw_line in enumerate(relationships_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or not line.startswith("R|"):
            continue
        fields = line.split("|")
        if len(fields) != _RELATIONSHIP_FIELD_COUNT:
            errors.append(
                {
                    "line_number": idx,
                    "line_type": "R",
                    "error": (
                        f"expected {_RELATIONSHIP_FIELD_COUNT} pipe-separated fields, "
                        f"got {len(fields)}"
                    ),
                }
            )
            continue
        try:
            src = int(fields[1])
        except ValueError:
            errors.append(
                {
                    "line_number": idx,
                    "line_type": "R",
                    "error": f"source must be integer, got '{fields[1]}'",
                }
            )
            src = None
        try:
            tgt = int(fields[2])
        except ValueError:
            errors.append(
                {
                    "line_number": idx,
                    "line_type": "R",
                    "error": f"target must be integer, got '{fields[2]}'",
                }
            )
            tgt = None
        if src is not None and (src < 0 or src >= entity_count):
            errors.append(
                {
                    "line_number": idx,
                    "line_type": "R",
                    "error": (f"source index {src} out of range (have {entity_count} entities)"),
                }
            )
        if tgt is not None and (tgt < 0 or tgt >= entity_count):
            errors.append(
                {
                    "line_number": idx,
                    "line_type": "R",
                    "error": (f"target index {tgt} out of range (have {entity_count} entities)"),
                }
            )

    return errors


class ExtractionOrchestrator:
    """Manages MCP-driven extraction lifecycle.

    Coordinates the external LLM extraction flow:
    1. ``get_tasks`` — provide extraction metadata and instructions
    2. ``get_chunks`` — serve chunk text for specific group indices
    3. ``submit_chunk`` — accept per-chunk extraction results
    4. ``get_progress`` — report submission progress
    5. ``finalize`` — parse, deduplicate, and commit to graph
    """

    def __init__(self, engine: Engine) -> None:
        """Initialize with a wired Engine instance.

        Args:
            engine: Engine with storage_adapter, graph_repository,
                extraction_service, and commit_service.

        """
        self.engine = engine
        # source_id -> deque[float] of monotonic-clock submission timestamps
        self._submission_timestamps: dict[str, deque[float]] = {}
        # source_id -> resolved FilteringConfig captured from the row's
        # filtering_mode at get_tasks time (Workstream 3, Task 3.5). Read by
        # finalize() so cross-chunk filtering and commit-side orphan-drop
        # honour the upload-time preset rather than engine defaults.
        self._filtering_configs: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _get_db(self) -> str:
        """Return the current database name from engine settings."""
        return self.engine.settings.current_database

    def _get_source(self, source_id: str) -> dict[str, Any]:
        """Load source dict or raise ValueError.

        Args:
            source_id: Source identifier.

        Returns:
            Source dict from storage.

        Raises:
            ValueError: If source not found.

        """
        source = self.engine.storage_adapter.get_source(source_id, self._get_db())
        if not source:
            msg = f"Source {source_id} not found"
            raise ValueError(msg)
        return source

    def _build_source_groups(self, source_id: str) -> list[dict[str, Any]]:
        """Build extraction groups for a source using the shared pipeline.

        Fetches chunks, applies domain-based content exclusion filtering,
        and builds token-budget groups via the shared orchestration functions.

        Args:
            source_id: Source identifier.

        Returns:
            List of group dicts from ``build_extraction_groups``.

        """
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            build_extraction_groups,
            filter_and_strip_chunks,
            resolve_content_exclusions,
        )

        db = self._get_db()

        # Fetch lightweight chunk data
        all_chunks = self.engine.storage_adapter.get_chunks_for_extraction(
            source_id=source_id,
            database_name=db,
        )

        if not all_chunks:
            return []

        # Resolve domain for content filtering
        source = self.engine.storage_adapter.get_source(source_id, db)
        domain_name = None
        if source:
            domain_name = source.get("forced_domain") or source.get("extraction_domain")

        domain_obj = None
        if domain_name:
            registry = get_domain_registry(database_name=db)
            domain_obj = registry.get_domain(domain_name)

        # Apply content exclusion filtering
        content_matchers = resolve_content_exclusions(domain_obj)
        if content_matchers:
            all_chunks, filter_stats = filter_and_strip_chunks(all_chunks, content_matchers)
            logger.info(
                "mcp_content_exclusion_applied",
                source_id=source_id,
                excluded_chunks=filter_stats.excluded_chunks,
                categories=filter_stats.categories_matched,
            )

        # Build groups with dynamic token-budget sizing — read sizes from
        # ``EngineSettings.chunking`` so MCP-driven extractions match the
        # Cortex/Neuron worker path. Workstream 3, Task 3.5.
        return build_extraction_groups(
            all_chunks,
            target_tokens=self.engine.settings.chunking.target_group_tokens,
            overlap=self.engine.settings.chunking.group_overlap,
        )

    def _get_group_indices(self, source_id: str) -> set[int]:
        """Get all distinct chunk group indices for a source.

        Args:
            source_id: Source identifier.

        Returns:
            Set of distinct group index values.

        """
        groups = self._build_source_groups(source_id)
        return {g["group_index"] for g in groups}

    def _count_chunk_groups(self, source_id: str) -> int:
        """Count distinct chunk groups for a source.

        Args:
            source_id: Source identifier.

        Returns:
            Number of distinct chunk groups.

        """
        return len(self._get_group_indices(source_id))

    def _get_expected_indices(self, source: dict[str, Any]) -> set[int]:
        """Derive expected chunk group indices from extraction_depth.

        For ``quick`` mode returns the deterministically-sampled subset
        that ``get_tasks`` enumerates; otherwise returns the full set of
        group indices for the source. (Pre-0030 this was cached on the
        source row as ``extraction_chunk_indices``; that column is gone
        and re-deriving produces an identical value. Tests that want to
        bypass the heavy ``_build_source_groups`` chain use the shortcut
        installed by ``tests/unit/mcp/conftest.install_chunk_indices_shortcut``.)

        Args:
            source: Source dict from storage.

        Returns:
            Set of expected chunk group indices.

        """
        source_id = source["id"]
        all_indices = self._get_group_indices(source_id)
        extraction_depth = source.get("extraction_depth", "full")

        if extraction_depth == "quick":
            # Read from EngineSettings.analysis so MCP matches the Cortex
            # ``import_service`` path — both surface ``analysis.quick_sample_size``
            # from settings.yaml. Workstream 3, Task 3.5.
            quick_sample_size = self.engine.settings.analysis.quick_sample_size
            total = len(all_indices)
            sample_size = min(quick_sample_size, total)
            step = max(1, total // sample_size)
            selected = sorted(all_indices)[::step][:sample_size]
            return set(selected)

        return all_indices

    def _build_file_info(self, source: dict[str, Any]) -> dict[str, Any]:
        """Build the file_info dict expected by commit_service.commit().

        Includes ``filtering_mode`` so the commit-side orphan-drop honours the
        row's filtering preset (Workstream 3, Task 3.5). Falls back to
        ``"balanced"`` when the row was uploaded before the column existed —
        the same default ``resolve_filtering_config`` uses.

        Args:
            source: Source dict from storage.

        Returns:
            Dict with ``filename`` and ``filtering_mode`` keys.

        """
        return {
            "filename": source.get("filename", "unknown"),
            "filtering_mode": source.get("filtering_mode") or "balanced",
        }

    def _build_edge_type_constraints(
        self, domain_name: str | None
    ) -> dict[str, dict[str, list[str]]] | None:
        """Build edge_type_constraints from a resolved domain.

        Mirrors the worker path's ``_apply_post_dedup_filters`` logic in
        ``operations/extraction/extraction_finalizer.py``: when a domain is
        in scope, surface its ``get_edge_type_constraints()`` so the
        cross-chunk type-constraint filter can run during finalize. Returns
        ``None`` when the domain is missing, unresolvable, or yields no
        constraints — matching how the worker path passes ``None`` to
        skip the filter.

        Args:
            domain_name: Detected or forced domain name from the source row.

        Returns:
            Edge-type-constraints mapping, or ``None`` when unavailable.

        """
        if not domain_name:
            return None
        try:
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                get_domain_registry,
            )

            registry = get_domain_registry(database_name=self._get_db())
            domain = registry.get_domain(domain_name)
        except Exception:  # registry lookup failures must not block finalize
            logger.debug("mcp_domain_lookup_failed", domain=domain_name)
            return None

        if domain is None or not hasattr(domain, "get_edge_type_constraints"):
            return None
        try:
            constraints: dict[str, dict[str, list[str]]] = domain.get_edge_type_constraints()
        except Exception:
            logger.debug("mcp_edge_type_constraints_unavailable", domain=domain_name)
            return None
        return constraints or None

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def get_tasks(self, source_id: str, force: bool = False) -> dict[str, Any]:
        """Get extraction planning metadata for an indexed source.

        Validates the source status, transitions to ``mcp_extracting``,
        counts chunk groups, builds extraction instructions, and returns
        planning metadata for the MCP client.

        Args:
            source_id: Source identifier.
            force: If True, allow re-extraction of committed sources.

        Returns:
            Dict with source_id, filename, status, total_chunks,
            system_prompt, entity_instructions, relationship_instructions,
            and existing_templates.

        Raises:
            ValueError: If source not found or in invalid status.

        """
        source = self._get_source(source_id)
        status = source.get("status", "")

        # Validate status before attempting CAS
        if status not in (SourceStatus.INDEXED, SourceStatus.COMMITTED):
            msg = (
                f"Source {source_id} has status '{status}', "
                f"expected 'indexed' (or 'committed' with force=True)"
            )
            raise ValueError(msg)
        if status == SourceStatus.COMMITTED and not force:
            msg = (
                f"Source {source_id} has status '{status}', "
                f"expected 'indexed' (or 'committed' with force=True)"
            )
            raise ValueError(msg)

        # --- Confirmation gate -------------------------------------------- #
        # Evaluate the gate from PERSISTED SourceRow state BEFORE the
        # INDEXED -> MCP_EXTRACTING CAS, so a parked source never claims the
        # extraction slot. A forced/stored domain, a prior confirmation
        # (extraction_confirmed_at), or a status already past INDEXED all
        # short-circuit to 'proceed' inside gate_decision.
        if gate_decision(source) == "park":
            # Detection is a fast heuristic — compute the proposal so the
            # client sees the recommended domain even while parked. ranking[0]
            # is the winner; low_confidence flags the generic-fallback case.
            registry = get_domain_registry(database_name=self._get_db())
            extraction_settings = self.engine.settings.extraction
            chunks = self.engine.storage_adapter.list_chunks(
                database_name=self._get_db(),
                source_id=source_id,
                limit=extraction_settings.domain_detection_sample_count,
                include_content=True,
            )
            sample_text = " ".join(c.get("content", "") for c in chunks)[
                : extraction_settings.domain_detection_sample_chars
            ]
            detection = detect_extraction_domain(
                registry=registry,
                forced_domain=None,
                sample_text=sample_text,
                filename=source.get("filename", ""),
            )
            proposal = proposal_from_detection(detection)
            park_for_confirmation(
                self.engine.storage_adapter,
                source_id,
                proposal,
            )
            logger.info(
                "mcp_source_parked_for_confirmation",
                source_id=source_id,
                detected_domain=detection["detected_domain"],
                confidence=detection["confidence"],
                low_confidence=detection.get("low_confidence", False),
            )
            return {
                "source_id": source_id,
                "filename": source.get("filename", ""),
                "status": SourceStatus.AWAITING_CONFIRMATION,
                "detected_domain": proposal["detected_domain"],
                "confidence": proposal["confidence"],
                "ranking": proposal["ranking"],
                "low_confidence": proposal["low_confidence"],
                "next_steps": (
                    f"Detection proposed domain "
                    f'"{detection["detected_domain"]}". Call confirm_extraction '
                    f'with file_id="{source_id}" (and an optional domain '
                    f"override) to start extraction, or add the document with "
                    f"auto_confirm=true to skip this gate."
                ),
            }
        # ------------------------------------------------------------------ #

        # Read depth BEFORE any transition (start_extraction would overwrite it).
        extraction_depth = source.get("extraction_depth", "full")

        # Resolve the source's persisted filtering_mode into a FilteringConfig
        # so subsequent get_chunks / submit_chunk / finalize calls can apply
        # the same cross-chunk filter behaviour Cortex does. Without this
        # resolution, MCP-driven extractions silently fell back to defaults
        # regardless of the upload-time filtering_mode setting. Workstream 3,
        # Task 3.5.
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
            resolve_filtering_config,
        )

        _row_filtering_mode = source.get("filtering_mode")
        # Only resolve when the row carries an explicit string mode. The
        # engine-level default is also "balanced" (the same default
        # ``resolve_filtering_config`` uses), so callers that didn't set a
        # filtering_mode at upload time get the canonical default without
        # needing to re-read engine settings here.
        if isinstance(_row_filtering_mode, str) and _row_filtering_mode:
            try:
                self._filtering_configs[source_id] = resolve_filtering_config(
                    mode=_row_filtering_mode
                )
            except ValueError:
                # Best-effort: a malformed legacy mode persisted on the row
                # falls back to the canonical default rather than poisoning
                # the get_tasks path.
                logger.warning(
                    "mcp_filtering_mode_invalid",
                    source_id=source_id,
                    filtering_mode=_row_filtering_mode,
                )
                self._filtering_configs[source_id] = resolve_filtering_config()

        # Compare-and-swap the status transition so two concurrent callers
        # can't both win the race.
        if force and status == SourceStatus.COMMITTED:
            logger.info(
                "force_re_extraction_requested",
                source_id=source_id,
                previous_status=status,
            )
            # Clear MCP extraction submissions first (these are separate from
            # graph artifacts — force_re_extract clears graph nodes/edges but
            # not the extraction submission table).
            self.engine.storage_adapter.delete_extraction_submissions(source_id, self._get_db())
            # Atomically reset commit_complete, extraction_complete,
            # extraction_results, and graph artifacts inside a single
            # adapter.transaction(). Leaves the source at INDEXED.
            force_re_extract(
                source_id=source_id,
                database_name=self._get_db(),
                storage_adapter=self.engine.storage_adapter,
                graph_repository=self.engine.graph_repository,
            )
            ok = self.engine.storage_adapter.transition_source_status(
                source_id,
                from_status=SourceStatus.INDEXED,
                to_status=SourceStatus.MCP_EXTRACTING,
                database_name=self._get_db(),
            )
        else:
            ok = self.engine.storage_adapter.transition_source_status(
                source_id,
                from_status=SourceStatus.INDEXED,
                to_status=SourceStatus.MCP_EXTRACTING,
                database_name=self._get_db(),
            )

        if not ok:
            msg = (
                f"Source {source_id} status changed or is already under "
                f"extraction by another client — refresh and retry."
            )
            raise ValueError(msg)

        # Record extraction start + mode metadata (status is already mcp_extracting).
        self.engine.storage_adapter.start_extraction(source_id, depth=extraction_depth)
        self.engine.storage_adapter.update_source(
            source_id,
            {
                "status": SourceStatus.MCP_EXTRACTING,
                "extraction_mode": "mcp",
            },
        )

        # Count chunk groups and apply depth strategy
        all_group_indices = self._get_group_indices(source_id)

        if extraction_depth == "quick":
            # Sample evenly-distributed groups (same as internal pipeline).
            # Reads from EngineSettings.analysis so MCP matches the Cortex
            # ``import_service`` path — Workstream 3, Task 3.5.
            quick_sample_size = self.engine.settings.analysis.quick_sample_size
            total = len(all_group_indices)
            sample_size = min(quick_sample_size, total)
            step = max(1, total // sample_size)
            selected = sorted(all_group_indices)[::step][:sample_size]
            active_group_indices = set(selected)
        else:
            active_group_indices = all_group_indices

        total_chunks = len(active_group_indices)

        # Record MCP extraction start in the universal stage-progress facility.
        # (Pre-0030 we also wrote ``extraction_chunk_indices`` to the source row
        # as a checkpoint; 0030 dropped that column. ``_get_expected_indices``
        # now re-derives the same set deterministically from extraction_depth.)
        await self.engine.storage_adapter.start_stage(
            parent_id=source_id,
            stage_name=StageName.MCP_EXTRACTION.value,
            total=total_chunks,
            started_at=datetime.now(UTC),
        )

        # Resolve domain: use forced > stored > auto-detect from chunk text
        source_domain = source.get("forced_domain") or source.get("extraction_domain")
        domain_obj = None

        if not source_domain:
            # Auto-detect domain from chunk content
            registry = get_domain_registry(database_name=self._get_db())
            extraction_settings = self.engine.settings.extraction
            chunks = self.engine.storage_adapter.list_chunks(
                database_name=self._get_db(),
                source_id=source_id,
                limit=extraction_settings.domain_detection_sample_count,
                include_content=True,
            )
            sample_text = " ".join(c.get("content", "") for c in chunks)[
                : extraction_settings.domain_detection_sample_chars
            ]

            detection = detect_extraction_domain(
                registry=registry,
                forced_domain=None,
                sample_text=sample_text,
                filename=source.get("filename", ""),
            )
            source_domain = detection["detected_domain"]
            domain_obj = detection["domain"]

            # Persist detected domain on the source record
            self.engine.storage_adapter.update_source(
                source_id,
                {
                    "extraction_domain": source_domain,
                    "extraction_domain_auto": True,
                },
            )
            logger.info(
                "mcp_domain_auto_detected",
                source_id=source_id,
                domain=source_domain,
                confidence=detection["confidence"],
            )
        else:
            # Load domain object for a forced or previously-stored domain
            registry = get_domain_registry(database_name=self._get_db())
            domain_obj = registry.get_domain(source_domain)

        # Format domain templates (falls back to generic if no domain)
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            format_extraction_templates,
        )

        formatted = format_extraction_templates(domain_obj)

        # Get domain-specific configuration
        entity_exclusions: list[ExclusionRule] = []
        strict_types = False
        if domain_obj:
            entity_exclusions = domain_obj.get_entity_exclusions()
            strict_types = domain_obj.get_strict_entity_types()

        # Build pre-formatted instructions for MCP sub-agents
        entity_instructions = _build_entity_instructions(
            node_templates_str=formatted["node_templates"],
            entity_exclusions=entity_exclusions,
            strict_types=strict_types,
            entity_examples=formatted.get("entity_examples", ""),
        )
        relationship_instructions = _build_relationship_instructions(
            edge_templates_str=formatted["edge_templates"],
            relationship_examples=formatted.get("relationship_examples", ""),
        )

        # List existing graph templates for type reuse hints
        node_templates = self.engine.graph_repository.list_templates(template_type="node")
        edge_templates = self.engine.graph_repository.list_templates(template_type="edge")
        existing_templates = {
            "node_templates": [
                {"name": t.name, "description": t.description} for t in node_templates
            ],
            "edge_templates": [
                {"name": t.name, "description": t.description} for t in edge_templates
            ],
        }

        logger.info(
            "mcp_extraction_tasks_prepared",
            source_id=source_id,
            total_chunks=total_chunks,
            domain=source_domain,
            node_template_count=len(node_templates),
            edge_template_count=len(edge_templates),
        )

        return {
            "source_id": source_id,
            "filename": source.get("filename", ""),
            "status": SourceStatus.MCP_EXTRACTING,
            "total_chunks": total_chunks,
            "chunk_indices": sorted(active_group_indices),
            "extraction_depth": extraction_depth,
            "domain": source_domain,
            "system_prompt": SYSTEM_PROMPT,
            "entity_instructions": entity_instructions,
            "relationship_instructions": relationship_instructions,
            "existing_templates": existing_templates,
        }

    async def get_chunks(self, source_id: str, chunk_indices: list[int]) -> dict[str, Any]:
        """Fetch chunk text for specific group indices.

        Builds extraction groups via the shared content-filtering and
        token-budget pipeline, then returns the requested groups with
        sentence splits and token estimates.

        Args:
            source_id: Source identifier.
            chunk_indices: List of group indices to fetch.

        Returns:
            Dict with source_id and chunks list, each containing
            index, text, sentences, and token_estimate.

        """
        # Build groups via shared pipeline (fetch, filter, pack)
        all_groups = self._build_source_groups(source_id)

        # Index groups by group_index for fast lookup
        groups_by_index: dict[int, dict[str, Any]] = {g["group_index"]: g for g in all_groups}

        # Build result for each requested index
        requested_set = set(chunk_indices)
        result_chunks: list[dict[str, Any]] = []
        for idx in sorted(requested_set):
            group = groups_by_index.get(idx)
            if not group:
                continue

            combined_text = group["combined_content"]

            # Split into sentences
            sentences = split_into_sentences(combined_text)

            # Estimate tokens (~4 chars per token)
            token_estimate = len(combined_text) // 4

            result_chunks.append(
                {
                    "index": idx,
                    "text": combined_text,
                    "sentences": sentences,
                    "token_estimate": token_estimate,
                }
            )

        logger.info(
            "mcp_chunks_fetched",
            source_id=source_id,
            requested=len(chunk_indices),
            returned=len(result_chunks),
        )

        return {
            "source_id": source_id,
            "chunks": result_chunks,
        }

    def _check_rate_limit(self, source_id: str) -> tuple[bool, int]:
        """Sliding-window rate limiter keyed by source_id.

        Evicts timestamps older than 60s, then allows or rejects based
        on the configured ``extraction_rate_limit_per_minute`` setting.

        Args:
            source_id: Source identifier used as the rate-limit bucket key.

        Returns:
            ``(allowed, limit)`` where ``allowed`` is False when the
            bucket is full and ``limit`` is the configured per-minute cap.

        """
        limit = self.engine.settings.mcp.extraction_rate_limit_per_minute
        window_seconds = 60.0
        now = _monotonic()
        bucket = self._submission_timestamps.setdefault(source_id, deque())
        # Drop expired timestamps.
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False, limit
        bucket.append(now)
        return True, limit

    async def submit_chunk(
        self,
        source_id: str,
        chunk_group_index: int,
        entities_text: str,
        relationships_text: str,
    ) -> dict[str, Any]:
        """Submit extraction results for one chunk group.

        Counts E| and R| lines, stores the submission via the storage
        adapter, and updates source progress fields.

        Args:
            source_id: Source identifier.
            chunk_group_index: Group index for this chunk.
            entities_text: Raw E|/P| lines from the MCP client.
            relationships_text: Raw R| lines from the MCP client.

        Returns:
            Dict with success, chunk_group_index, chunks_submitted,
            chunks_total, and ready_to_finalize flag.

        """
        # Enforce payload size cap
        max_bytes = self.engine.settings.mcp.max_extraction_payload_bytes
        total_bytes = len(entities_text.encode("utf-8")) + len(relationships_text.encode("utf-8"))
        if total_bytes > max_bytes:
            logger.warning(
                "mcp_submission_too_large",
                source_id=source_id,
                chunk_group_index=chunk_group_index,
                bytes=total_bytes,
                limit=max_bytes,
            )
            return {
                "success": False,
                "error_code": "PAYLOAD_TOO_LARGE",
                "error": (f"Submission exceeds max {max_bytes} bytes (got {total_bytes})."),
            }

        # Enforce per-source rate limit
        allowed, limit = self._check_rate_limit(source_id)
        if not allowed:
            logger.warning(
                "mcp_submission_rate_limited",
                source_id=source_id,
                limit_per_minute=limit,
            )
            return {
                "success": False,
                "error_code": "RATE_LIMIT_EXCEEDED",
                "error": (
                    f"Too many submissions for source '{source_id}': limit is {limit}/minute."
                ),
            }

        # Validate chunk_group_index against expected set (handles quick-mode subsets)
        source = self._get_source(source_id)
        expected = self._get_expected_indices(source)
        if chunk_group_index not in expected:
            valid = sorted(expected) if expected else []
            logger.warning(
                "mcp_invalid_chunk_index",
                source_id=source_id,
                chunk_group_index=chunk_group_index,
                valid_indices=valid,
            )
            return {
                "success": False,
                "error_code": "INVALID_CHUNK_INDEX",
                "error": (
                    f"chunk_group_index={chunk_group_index} not in expected indices {valid}."
                ),
            }

        # Validate line shapes before persisting
        shape_errors = _validate_extraction_lines(entities_text, relationships_text)
        if shape_errors:
            logger.warning(
                "mcp_submission_invalid_shape",
                source_id=source_id,
                chunk_group_index=chunk_group_index,
                error_count=len(shape_errors),
            )
            return {
                "success": False,
                "error_code": "INVALID_LINE_SHAPE",
                "error": f"Malformed submission: {len(shape_errors)} error(s).",
                "errors": shape_errors,
            }

        db = self._get_db()

        # Count lines (simple string counting)
        entity_count = sum(
            1 for line in entities_text.splitlines() if line.strip().startswith("E|")
        )
        relationship_count = sum(
            1 for line in relationships_text.splitlines() if line.strip().startswith("R|")
        )

        # Build submission data
        from chaoscypher_core.utils import generate_id

        submission_data = {
            "id": generate_id(),
            "source_id": source_id,
            "chunk_group_index": chunk_group_index,
            "entities_text": entities_text,
            "relationships_text": relationships_text,
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "submitted_at": datetime.now(UTC),
        }

        # Store via storage adapter (upsert)
        self.engine.storage_adapter.create_extraction_submission(submission_data, db)

        # Count total submissions for this source
        submitted_count = self.engine.storage_adapter.count_extraction_submissions(source_id, db)

        # Get total chunk count from stage_progress (set by start_stage in get_tasks)
        source = self._get_source(source_id)
        chunks_total = (
            (source.get("stage_progress") or {})
            .get(StageName.MCP_EXTRACTION.value, {})
            .get("total", 0)
        )

        # Compute running entity/relationship preview counts
        subs = self.engine.storage_adapter.list_extraction_submissions(source_id, db)
        total_entities = sum(s.get("entity_count", 0) for s in subs)
        total_relationships = sum(s.get("relationship_count", 0) for s in subs)

        # Tick the stage-progress facility with the running submitted count
        await self.engine.storage_adapter.tick_stage(
            parent_id=source_id,
            stage_name=StageName.MCP_EXTRACTION.value,
            processed=submitted_count,
            avg_ms=None,  # MCP doesn't measure per-chunk wall-clock duration
            last_activity=datetime.now(UTC),
        )

        # Store running entity/relationship preview counts in stage extras
        await self.engine.storage_adapter.update_stage_extras(
            parent_id=source_id,
            stage_name=StageName.MCP_EXTRACTION.value,
            extras={
                "entities_preview": total_entities,
                "relationships_preview": total_relationships,
            },
            last_activity=datetime.now(UTC),
        )

        ready = submitted_count >= chunks_total > 0

        logger.info(
            "mcp_chunk_submitted",
            source_id=source_id,
            chunk_group_index=chunk_group_index,
            entity_count=entity_count,
            relationship_count=relationship_count,
            submitted=submitted_count,
            total=chunks_total,
        )

        return {
            "success": True,
            "chunk_group_index": chunk_group_index,
            "chunks_submitted": submitted_count,
            "chunks_total": chunks_total,
            "ready_to_finalize": ready,
        }

    async def get_progress(self, source_id: str) -> dict[str, Any]:
        """Check extraction submission progress.

        Uses stored expected chunk indices (set during get_tasks) to
        correctly handle quick-mode extractions where only a subset of
        chunk groups are expected.

        Args:
            source_id: Source identifier.

        Returns:
            Dict with source_id, total_chunks, submitted_indices,
            missing_indices, and ready_to_finalize flag.

        """
        db = self._get_db()
        source = self._get_source(source_id)

        # Use expected indices (handles quick-mode subset)
        expected_set = self._get_expected_indices(source)
        total_chunks = len(expected_set)

        # List submitted chunk indices
        submissions = self.engine.storage_adapter.list_extraction_submissions(source_id, db)
        submitted_indices = sorted(s["chunk_group_index"] for s in submissions)
        submitted_set = set(submitted_indices)

        # Compute missing from expected set
        missing_indices = sorted(expected_set - submitted_set)

        return {
            "source_id": source_id,
            "total_chunks": total_chunks,
            "submitted_indices": submitted_indices,
            "missing_indices": missing_indices,
            "ready_to_finalize": len(missing_indices) == 0 and total_chunks > 0,
            "status": source.get("status", ""),
        }

    async def finalize(self, source_id: str, model: str | None = None) -> dict[str, Any]:
        """Finalize extraction: parse, remap, dedup, commit, score.

        Loads all submissions, parses E|/P|/R| lines, applies properties,
        annotates chunk indices for citation tracking, remaps relationship
        indices across chunks, then delegates to ExtractionService for
        deduplication and to CommitService for graph writing. Finally
        caches quality scores (same as the internal pipeline).

        Args:
            source_id: Source identifier.
            model: Optional model name used for extraction (stored as
                raw model name in ``llm_model``).

        Returns:
            Dict with success, nodes_created, edges_created,
            templates_created, and final status.

        Raises:
            ValueError: If submissions are incomplete.

        """
        db = self._get_db()

        # Atomic status transition — only one process wins
        if not self.engine.storage_adapter.transition_source_status(
            source_id,
            from_status=SourceStatus.MCP_EXTRACTING,
            to_status=SourceStatus.COMMITTING,
            database_name=db,
        ):
            source = self._get_source(source_id)
            current_status = source.get("status", "unknown")
            msg = (
                f"Cannot finalize source {source_id}: status is '{current_status}', "
                f"expected 'mcp_extracting'. Another process may have already started finalization."
            )
            raise ValueError(msg)

        # Load source
        source = self._get_source(source_id)
        source_domain = source.get("extraction_domain")

        # Load all submissions ordered by chunk_group_index
        submissions = self.engine.storage_adapter.list_extraction_submissions(source_id, db)

        # Verify completeness using expected indices (handles quick-mode subset)
        expected_set = self._get_expected_indices(source)
        total_chunks = len(expected_set)
        submitted_set = {s["chunk_group_index"] for s in submissions}
        missing = sorted(expected_set - submitted_set)

        if missing:
            msg = (
                f"Incomplete: {len(submissions)}/{total_chunks} chunks submitted. "
                f"Missing indices: {missing}"
            )
            raise ValueError(msg)

        logger.info(
            "mcp_finalization_started",
            source_id=source_id,
            submission_count=len(submissions),
        )

        # Resolve the FilteringConfig that should govern parsing and
        # cross-chunk filters. Prefer the cached entry populated by
        # ``get_tasks`` (same process, same lifecycle); fall back to
        # re-resolving from the row when the finalize call lands in a
        # different process than ``get_tasks`` — the cache is per-instance,
        # so a fresh worker wouldn't see it. Workstream 3, Task 3.5; the
        # ``minimum_alias_length`` thread closes a W4 review-gap where
        # the slider was silently ignored on the MCP path.
        cached_filtering_config = self._filtering_configs.get(source_id)
        if cached_filtering_config is None:
            from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
                resolve_filtering_config,
            )

            row_filtering_mode = source.get("filtering_mode") or "balanced"
            try:
                cached_filtering_config = resolve_filtering_config(mode=str(row_filtering_mode))
            except ValueError:
                # Malformed legacy mode — fall back to the canonical default
                # rather than failing the commit on a value mismatch.
                logger.warning(
                    "mcp_finalize_filtering_mode_invalid",
                    source_id=source_id,
                    filtering_mode=row_filtering_mode,
                )
                cached_filtering_config = resolve_filtering_config()

        minimum_alias_length = (
            int(cached_filtering_config.minimum_alias_length)
            if cached_filtering_config is not None
            and getattr(cached_filtering_config, "minimum_alias_length", None) is not None
            else 2
        )

        # Parse and aggregate all submissions
        all_entities: list[dict[str, Any]] = []
        all_relationships: list[dict[str, Any]] = []

        for sub in submissions:
            # Parse via line parser
            entities, relationships, properties = parse_extraction_output(
                entities_str=sub.get("entities_text", ""),
                relationships_str=sub.get("relationships_text", ""),
                minimum_alias_length=minimum_alias_length,
            )

            # Apply properties to entities (modifies in-place)
            apply_properties_to_entities(entities, properties)

            chunk_idx = sub["chunk_group_index"]

            # Annotate chunk_index for citation tracking
            for entity in entities:
                entity["chunk_index"] = chunk_idx
            for rel in relationships:
                rel["chunk_index"] = chunk_idx

            # Remap relationship indices: offset by current entity count
            entity_offset = len(all_entities)
            for rel in relationships:
                source_val = rel.get("source")
                target_val = rel.get("target")
                if isinstance(source_val, int):
                    rel["source"] = source_val + entity_offset
                if isinstance(target_val, int):
                    rel["target"] = target_val + entity_offset

            all_entities.extend(entities)
            all_relationships.extend(relationships)

        logger.info(
            "mcp_submissions_parsed",
            source_id=source_id,
            total_entities=len(all_entities),
            total_relationships=len(all_relationships),
        )

        # Build file_info for commit service
        file_info = self._build_file_info(source)

        # Build edge_type_constraints from the detected/forced domain so the
        # cross-chunk type-constraint filter can run when the resolved
        # FilteringConfig has ``strict_edge_type_constraints`` set (worker
        # path parity — see ``_apply_post_dedup_filters``). Use the effective
        # domain (forced overrides detected) so a user-pinned domain still
        # drives constraint resolution.
        effective_domain_for_constraints = source.get("forced_domain") or source_domain
        edge_type_constraints = self._build_edge_type_constraints(effective_domain_for_constraints)

        # Finalize via extraction service (dedup, normalize, embed, suggest)
        result = await self.engine.extraction_service.finalize_distributed_extraction(
            raw_entities=all_entities,
            raw_relationships=all_relationships,
            generate_embeddings=True,
            file_info=file_info,
            detected_domain=source_domain,
            edge_type_constraints=edge_type_constraints,
            filtering_config=cached_filtering_config,
        )

        # Persist the generated entity embeddings so the commit phase can
        # attach real vectors to the graph nodes — parity with the worker
        # finalizer's ``_store_entity_embeddings`` call. Previously the MCP
        # manual-extraction path dropped the embeddings here, committing every
        # node with a null embedding; ``vec_search_nodes`` stayed empty so node
        # vector search and GraphRAG seeding silently degraded to keyword-only.
        # ``EntityCommitHandler._load_embeddings`` reads these back by
        # ``entity_id`` during the commit below.
        from chaoscypher_core.operations.extraction.extraction_finalizer import (
            _store_entity_embeddings,
        )

        _store_entity_embeddings(
            self.engine.storage_adapter,
            result,
            result.get("entities", []),
            source_id,
            db,
        )

        # Drop the embeddings payload from the in-memory result now that it is
        # persisted: ``complete_extraction`` below writes the entity rows and
        # the vectors have no place there (they live in source_entity_embeddings).
        result.pop("embeddings", None)

        # Store extraction results on source (same as internal pipeline)
        # This enables quality scoring, UI display, and re-extraction.
        # Per-source entity / relationship rows now live in dedicated
        # tables (migration 0042); ``complete_extraction`` writes them.
        forced_domain = source.get("forced_domain")
        result_metadata = result.get("metadata") if isinstance(result, dict) else None
        filtering_log = (
            result_metadata.get("filtering_log") if isinstance(result_metadata, dict) else None
        )
        # Stamp the domain fingerprint onto the source row so re-extraction can
        # detect when the domain definition has drifted (worker-path parity).
        from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
            resolve_domain_fingerprint,
        )

        _dom_version, _dom_hash = resolve_domain_fingerprint(source_domain, db)
        self.engine.storage_adapter.complete_extraction(
            source_id,
            entities=result.get("entities", []),
            relationships=result.get("relationships", []),
            detected_domain=source_domain,
            forced_domain=forced_domain,
            cross_chunk_filtering_log=filtering_log,
            domain_version=_dom_version,
            domain_content_hash=_dom_hash,
        )

        # Mark MCP extraction stage complete in the universal facility
        await self.engine.storage_adapter.complete_stage(
            parent_id=source_id,
            stage_name=StageName.MCP_EXTRACTION.value,
            completed_at=datetime.now(UTC),
        )

        # Commit to graph
        commit_result = await self.engine.commit_service.commit(
            file_id=source_id,
            commit_data=result,
            file_info=file_info,
        )

        # Cache quality scores (same as internal pipeline)
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            cache_quality_scores,
        )

        entities = result.get("entities", [])
        relationships = result.get("relationships", [])
        chunk_count = source.get("chunk_count", 0)

        cache_quality_scores(
            adapter=self.engine.storage_adapter,
            source_id=source_id,
            entities=entities,
            relationships=relationships,
            domain_name=source_domain,
            database_name=db,
            chunk_count=chunk_count,
        )

        # Store model provenance (extraction_mode already set to "mcp" by get_tasks)
        if model:
            self.engine.storage_adapter.update_file(
                source_id, database_name=db, updates={"llm_model": model}
            )

        # Clean up submissions
        deleted = self.engine.storage_adapter.delete_extraction_submissions(source_id, db)

        # Evict the per-source FilteringConfig cache entry now that finalize
        # has consumed it. Without this the dict accumulates one entry per
        # finalized source for the lifetime of the orchestrator (and a
        # long-lived MCP server process), which is a slow leak we don't
        # need — the cache only exists to bridge get_tasks → finalize.
        self._filtering_configs.pop(source_id, None)

        nodes_created = len(commit_result.get("created_nodes", []))
        edges_created = len(commit_result.get("created_edges", []))
        templates_created = len(commit_result.get("created_templates", []))

        # Re-read the source row so we can surface the quality scores
        # that ``cache_quality_scores`` just wrote. Reading from the same
        # adapter keeps the response self-consistent — the row reflects
        # the exact state the commit landed in. Failures here are
        # non-fatal: the commit already succeeded, so we degrade
        # gracefully to nulls rather than rolling back the whole call.
        quality_grade: float | None = None
        quality_label: str | None = None
        quality_breakdown: dict[str, Any] | None = None
        try:
            final_row = self.engine.storage_adapter.get_source(source_id, db)
            if final_row is not None:
                quality_grade = final_row.get("cached_quality_grade")
                quality_label = final_row.get("cached_quality_label")
                # Drill-down so MCP clients can show why the grade is what
                # it is without a separate round trip. Mirrors the
                # ``QualityMetrics`` shape the web UI's Data Quality tab
                # surfaces.
                quality_breakdown = {
                    "richness": final_row.get("cached_richness_score"),
                    "avg_entity_quality": final_row.get("cached_avg_entity_quality"),
                    "avg_relationship_quality": final_row.get("cached_avg_relationship_quality"),
                    "topology_score": final_row.get("cached_topology_score"),
                    "density_score": final_row.get("cached_density_score"),
                    "structural_penalty": final_row.get("cached_structural_penalty"),
                    "pollution_penalty": final_row.get("cached_pollution_penalty"),
                    "hub_skew": final_row.get("cached_hub_skew"),
                    "reciprocal_rate": final_row.get("cached_reciprocal_rate"),
                    "coverage_score": final_row.get("cached_coverage_score"),
                    "low_quality_entity_count": final_row.get("cached_low_quality_entity_count"),
                    "low_quality_relationship_count": final_row.get(
                        "cached_low_quality_relationship_count"
                    ),
                    "scores_version": final_row.get("cached_scores_version"),
                }
        except Exception:
            logger.warning(
                "mcp_finalize_quality_readback_failed",
                source_id=source_id,
                exc_info=True,
            )

        logger.info(
            "mcp_finalization_completed",
            source_id=source_id,
            nodes_created=nodes_created,
            edges_created=edges_created,
            templates_created=templates_created,
            submissions_deleted=deleted,
            quality_grade=quality_grade,
            quality_label=quality_label,
        )

        return {
            "success": True,
            "source_id": source_id,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
            "templates_created": templates_created,
            "status": "committed",
            "quality_grade": quality_grade,
            "quality_label": quality_label,
            "quality_breakdown": quality_breakdown,
        }
