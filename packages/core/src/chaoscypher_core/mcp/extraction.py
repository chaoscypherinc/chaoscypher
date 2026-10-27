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
from chaoscypher_core.services.graph.management.release import refresh_after_release
from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
    source_domain_name,
    source_packs,
)
from chaoscypher_core.services.sources.engine.extraction.domains import (
    get_domain_registry,
    source_domain_sample,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    detected_packs_of,
    first_domain_name,
    forced_packs_of,
    labels_of,
    proposal_packs,
    stored_packs,
)
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    ExtractionPlan,
    build_extraction_plan,
)
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    detect_extraction_domain,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    PROMPT_CHUNK_TEXT_PLACEHOLDER,
    PROMPT_PASS1_ENTITIES_PLACEHOLDER,
    AIEntityExtractor,
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
    format_numbered_sentences,
    split_into_sentences,
)
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_core.services.stage_progress import StageName


if TYPE_CHECKING:
    from collections.abc import Sequence

    from chaoscypher_core.bootstrap import Engine

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------ #
#  Instruction rendering for MCP sub-agents
# ------------------------------------------------------------------ #


def _render_extraction_instructions(settings: Any, plan: ExtractionPlan) -> tuple[str, str]:
    """Render the pass-1 and pass-2 prompts an MCP sub-agent extracts under.

    The prompt inputs are the source's :class:`ExtractionPlan` chunk kwargs
    (:func:`build_extraction_plan`, the one plan the queue, the CLI and the
    standalone extractor run with), so an MCP client is prompted with what a
    local model is prompted with. Both prompts come from
    :class:`AIEntityExtractor`, the renderer local extraction sends, with the
    chunk's numbered sentences and the pass-1 entity list left as
    :data:`PROMPT_CHUNK_TEXT_PLACEHOLDER` and
    :data:`PROMPT_PASS1_ENTITIES_PLACEHOLDER` (the templates extraction
    metrics record, but with pass 1's KNOWN ENTITIES slot empty: this path
    serves no block). Substituting a chunk's ``numbered_sentences`` (from
    ``get_chunks``) for the chunk placeholder yields the exact pass-1 prompt
    a local model receives for that chunk only when the local model is sent
    no block: for the source's first group, or with
    ``extraction.known_entities_limit`` 0. From the second group on a local
    model's pass 1 carries the KNOWN ENTITIES block of the earlier groups,
    and its records pass the list's alias and name guards; a sub-agent's do
    neither, since the groups are extracted in any order here.

    Args:
        settings: ``EngineSettings`` the extractor is built with.
        plan: The source's extraction plan.

    Returns:
        ``(entity_instructions, relationship_instructions)``.

    """
    return AIEntityExtractor(settings=settings).render_plan_templates(
        plan.chunk_kwargs, known_entities=""
    )


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
        # source_id -> the source's ExtractionPlan (build_extraction_plan, the
        # plan every extraction path runs with) decided at get_tasks time.
        # Read by finalize() so the dedup threshold, cross-chunk filters and
        # commit-side orphan-drop run with the domain's limits and the row's
        # preset; rebuilt from the row when finalize lands in another process.
        self._plans: dict[str, ExtractionPlan] = {}
        # source_id -> the source's derived chunk-group index set, memoized for
        # the same get_tasks -> finalize span as _plans above.
        # Pre-0030 this value lived on the source row as
        # ``extraction_chunk_indices``; re-deriving it means a full-source
        # chunk read, a content-exclusion pass over every chunk and a re-pack,
        # and ``submit_chunk`` probes it once per chunk group purely to test one
        # integer. Refreshed at get_tasks, evicted at finalize.
        self._group_indices: dict[str, set[int]] = {}
        # Sources whose finalize has reached the commit phase (entity rows
        # written, graph commit in flight). A failure before this point rolls
        # the status back to mcp_extracting so a retry can win the CAS again;
        # a failure after it is left at committing for the recovery path.
        self._commit_phase: set[str] = set()

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
            domain_name = source_domain_name(source)

        domain_obj = None
        if domain_name:
            from chaoscypher_core.services.sources.engine.extraction.domains.stack import (
                DomainStack,
            )

            # The floor plus every pack the source names: every member's exclusions.
            domain_obj = DomainStack.from_stored(
                get_domain_registry(database_name=db), source_packs(source)
            )

        # Apply content exclusion filtering — honouring the row's
        # ``content_filtering`` toggle the same way the pipeline's
        # ``_apply_content_filtering`` does (``confirm_extraction`` persists
        # it; without this check the MCP path ignored the override).
        content_filtering_enabled = bool(source.get("content_filtering", True)) if source else True
        content_matchers = (
            resolve_content_exclusions(domain_obj) if content_filtering_enabled else []
        )
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

        Memoized per source for the protocol span (see ``_group_indices``):
        the value is stable across it — ``get_tasks`` already freezes the
        matching ``total`` into stage progress, which ``submit_chunk`` reads
        back — and rebuilt on a miss, so a ``finalize`` landing in a different
        process than its ``get_tasks`` still resolves.

        Args:
            source_id: Source identifier.

        Returns:
            Set of distinct group index values.

        """
        cached = self._group_indices.get(source_id)
        if cached is not None:
            return set(cached)
        groups = self._build_source_groups(source_id)
        indices = {g["group_index"] for g in groups}
        self._group_indices[source_id] = set(indices)
        return indices

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
            return self._sample_quick_indices(all_indices)

        return all_indices

    def _build_file_info(self, source: dict[str, Any], plan: ExtractionPlan) -> dict[str, Any]:
        """Build the file_info dict expected by commit_service.commit().

        Includes the plan's ``filtering_mode`` so the commit-side orphan-drop
        honours the preset the extraction ran with.

        Args:
            source: Source dict from storage.
            plan: The source's extraction plan.

        Returns:
            Dict with ``filename`` and ``filtering_mode`` keys.

        """
        return {
            "filename": source.get("filename", "unknown"),
            "filtering_mode": plan.filtering_mode,
        }

    def _plan_for(self, source: dict[str, Any], packs: Sequence[str]) -> ExtractionPlan:
        """The source's extraction plan: :func:`build_extraction_plan` over its stack and row.

        The stack is the floor plus the source's packs
        (:meth:`DomainStack.from_stored`). A preset the row carries that
        is no longer a valid mode (a legacy alias) falls back to the engine's
        default preset, still through the plan, rather than failing the MCP
        call.

        Args:
            source: Source dict from storage.
            packs: The row's forced or detected packs (``source_packs``);
                ``()`` is the floor alone.

        Returns:
            The plan the queue, the CLI and the standalone extractor would run with.

        """
        settings = self.engine.settings
        stack = DomainStack.from_stored(get_domain_registry(database_name=self._get_db()), packs)
        try:
            return build_extraction_plan(stack, settings, source_row=source)
        except ValueError:
            logger.warning(
                "mcp_filtering_mode_invalid",
                source_id=source.get("id"),
                filtering_mode=source.get("filtering_mode"),
            )
            return build_extraction_plan(
                stack,
                settings,
                filtering_mode_override=str(settings.extraction.extraction_filtering_mode),
                source_row=source,
            )

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def _sample_quick_indices(self, all_indices: set[int]) -> set[int]:
        """Evenly sample group indices for ``quick`` depth (same as the pipeline).

        Reads ``EngineSettings.analysis.quick_sample_size`` so MCP matches the
        Cortex ``import_service`` path. An empty index set returns empty — the
        old inline copies divided by ``min(sample_size, 0)``.
        """
        total = len(all_indices)
        if total == 0:
            return set()
        sample_size = min(self.engine.settings.analysis.quick_sample_size, total)
        step = max(1, total // sample_size)
        return set(sorted(all_indices)[::step][:sample_size])

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
            prompt_placeholders and existing_templates. The two instruction
            strings are the product's prompt templates; ``prompt_placeholders``
            names the markers a client replaces with a chunk's
            ``numbered_sentences`` and its pass-1 entity list.

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
            sample_text = source_domain_sample(
                self.engine.storage_adapter, source_id, self._get_db()
            )
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
            proposed = proposal_packs(proposal)
            return {
                "source_id": source_id,
                "filename": source.get("filename", ""),
                "status": SourceStatus.AWAITING_CONFIRMATION,
                "packs": proposed,
                "detected_domain": proposal["detected_domain"],
                "confidence": proposal["confidence"],
                "ranking": proposal["ranking"],
                "low_confidence": proposal["low_confidence"],
                "next_steps": (
                    f"Detection proposed the packs {proposed} (on the floor every "
                    f"source has; [] is the floor alone). Call confirm_extraction "
                    f'with file_id="{source_id}" (and an optional packs '
                    f"override) to start extraction, or add the document with "
                    f"auto_confirm=true to skip this gate."
                ),
            }
        # ------------------------------------------------------------------ #

        # Read depth BEFORE any transition (start_extraction would overwrite it).
        extraction_depth = source.get("extraction_depth", "full")

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
            released = force_re_extract(
                source_id=source_id,
                database_name=self._get_db(),
                storage_adapter=self.engine.storage_adapter,
                settings=self.engine.settings,
            )
            # Now: the reset commit counters make the next commit skip its cleanup.
            await refresh_after_release(
                self.engine.storage_adapter,
                self._get_db(),
                released,
                embedder=self.engine.embedding_provider,
                search=self.engine.search_repository,
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

        # Count chunk groups and apply the depth strategy. Re-derive on every
        # get_tasks: a force=True re-extract may follow a re-chunk, so the
        # previous span's memo must not be reused. A source with no groups
        # releases the claim and is rejected with the pipeline's message
        # (``import_service``): previously the quick path divided by zero
        # here and full depth handed out zero tasks, both leaving the source
        # at mcp_extracting until the staleness window.
        self._group_indices.pop(source_id, None)
        all_group_indices = self._get_group_indices(source_id)
        if not all_group_indices:
            self.engine.storage_adapter.transition_source_status(
                source_id,
                from_status=SourceStatus.MCP_EXTRACTING,
                to_status=SourceStatus.INDEXED,
                database_name=self._get_db(),
            )
            msg = f"No extraction groups could be built for source {source_id}: it has no chunks."
            raise ValueError(msg)

        if extraction_depth == "quick":
            active_group_indices = self._sample_quick_indices(all_group_indices)
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

        # Resolve the packs: forced > stored > auto-detect from chunk text
        packs = source_packs(source)

        if source_domain_name(source) is None:
            # The row names none: detect from chunk content
            registry = get_domain_registry(database_name=self._get_db())
            sample_text = source_domain_sample(
                self.engine.storage_adapter, source_id, self._get_db()
            )

            detection = detect_extraction_domain(
                registry=registry,
                forced_domain=None,
                sample_text=sample_text,
                filename=source.get("filename", ""),
            )
            packs = detected_packs_of(detection)

            # Persist the detected packs on the source record (the label rides along)
            self.engine.storage_adapter.update_source(
                source_id,
                {
                    "extraction_packs": list(packs),
                    "extraction_domain_auto": True,
                },
            )
            logger.info(
                "mcp_domain_auto_detected",
                source_id=source_id,
                packs=list(packs),
                confidence=detection["confidence"],
            )
        source_domain = first_domain_name(packs)

        # The one extraction plan (shared with the queue, the CLI and the
        # standalone extractor): prompt inputs, the stack's extraction limits
        # and the row's preset. Cached so finalize runs with the same plan.
        plan = self._plan_for(source, packs)
        self._plans[source_id] = plan

        # Render both prompts through the product's extractor so an MCP
        # client extracts under the same bytes a local model does.
        entity_instructions, relationship_instructions = _render_extraction_instructions(
            self.engine.settings, plan
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
            packs=list(packs),
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
            "packs": list(packs),
            "system_prompt": SYSTEM_PROMPT,
            "entity_instructions": entity_instructions,
            "relationship_instructions": relationship_instructions,
            "prompt_placeholders": {
                "chunk_text": PROMPT_CHUNK_TEXT_PLACEHOLDER,
                "pass1_entities": PROMPT_PASS1_ENTITIES_PLACEHOLDER,
            },
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
            index, text, sentences, numbered_sentences (the ``S1: ...``
            block the prompts number) and token_estimate.

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
                    "numbered_sentences": format_numbered_sentences(sentences),
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

        # Only a source under MCP extraction takes submissions. Rows written
        # against any other status (committed, or indexed after a UI reset)
        # survive a non-force get_tasks and would be committed as if current.
        source = self._get_source(source_id)
        current_status = source.get("status", "")
        if current_status != SourceStatus.MCP_EXTRACTING:
            logger.warning(
                "mcp_submission_wrong_status",
                source_id=source_id,
                status=current_status,
            )
            return {
                "success": False,
                "error_code": "INVALID_STATUS",
                "error": (
                    f"Source '{source_id}' is '{current_status}', expected 'mcp_extracting' — "
                    "call get_extraction_tasks first."
                ),
            }

        # Validate chunk_group_index against expected set (handles quick-mode subsets)
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

        # Any failure before the commit phase rolls the CAS back so a retry
        # can claim the source again (submissions are kept). Without this an
        # embedding or dedup error stranded the source at 'committing', where
        # the worker reconciler — seeing no commit payload and no entity rows
        # — dispatched an empty import_commit and zero-graph-committed it.
        # Failures after the commit phase stay at 'committing': the entity
        # rows are written and recovery can rebuild the commit from them.
        try:
            return await self._finalize_claimed(source_id, model, db)
        except Exception:
            if source_id not in self._commit_phase:
                self.engine.storage_adapter.transition_source_status(
                    source_id,
                    from_status=SourceStatus.COMMITTING,
                    to_status=SourceStatus.MCP_EXTRACTING,
                    database_name=db,
                )
                logger.warning("mcp_finalize_rolled_back", source_id=source_id)
            raise
        finally:
            self._commit_phase.discard(source_id)

    async def _finalize_claimed(self, source_id: str, model: str | None, db: str) -> dict[str, Any]:
        """The body of :meth:`finalize`, run after the COMMITTING claim succeeds."""
        # Load source. The effective domain is the forced one, else the
        # detected one, as on the queue and CLI paths (``get_tasks`` persists
        # ``extraction_domain`` only when it auto-detected).
        source = self._get_source(source_id)
        forced_packs = forced_packs_of(source)
        detected_packs = stored_packs(
            source, list_key="extraction_packs", name_key="extraction_domain"
        )
        # The one-name strings the finalize threads are the packs' labels.
        forced_domain = labels_of(forced_packs)
        source_domain = labels_of(detected_packs)
        effective_domain = forced_domain or source_domain

        # Load all submissions ordered by chunk_group_index
        submissions = self.engine.storage_adapter.list_extraction_submissions(source_id, db)

        # Verify completeness using expected indices (handles quick-mode subset)
        expected_set = self._get_expected_indices(source)
        total_chunks = len(expected_set)
        submitted_set = {s["chunk_group_index"] for s in submissions}
        missing = sorted(expected_set - submitted_set)

        if missing:
            # ``finalize`` rolls the CAS back on the way out (pre-commit
            # failure) so a retry can win MCP_EXTRACTING -> COMMITTING again.
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

        # The source's extraction plan governs parsing, deduplication and the
        # cross-chunk filters, as on every other path. Prefer the plan cached
        # by ``get_tasks`` (same process, same lifecycle); rebuild it from the
        # row when finalize lands in a different process (the cache is
        # per-instance).
        plan = self._plans.get(source_id) or self._plan_for(source, source_packs(source))
        filtering_config = plan.filtering_config
        minimum_alias_length = int(filtering_config.minimum_alias_length)

        # Parse every submission, then aggregate through the shared Core
        # aggregator, which also resolves each item's citation evidence: a
        # submission's sent_refs number the sentences of its group's text
        # (``fetch_chunks`` splits ``combined_content``), rebuilt here
        # deterministically from the same pipeline.
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            aggregate_chunk_results,
        )

        groups_by_index = {g["group_index"]: g for g in self._build_source_groups(source_id)}
        group_results: list[dict[str, Any]] = []

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

            # Tag the extraction-group position (citations read the evidence
            # resolved at aggregation, never this index).
            for entity in entities:
                entity["chunk_index"] = chunk_idx
            for rel in relationships:
                rel["chunk_index"] = chunk_idx

            group = groups_by_index.get(chunk_idx) or {}
            group_results.append(
                {
                    "chunk_index": chunk_idx,
                    "raw_entities": entities,
                    "raw_relationships": relationships,
                    "input_text": group.get("combined_content", ""),
                    "small_chunk_ids": group.get("small_chunk_ids") or [],
                }
            )

        aggregated = aggregate_chunk_results(
            group_results,
            chunks_by_id={
                c["id"]: c
                for c in self.engine.storage_adapter.get_chunks_for_extraction(
                    source_id=source_id, database_name=db
                )
                or []
                if c.get("id")
            },
        )
        all_entities: list[dict[str, Any]] = aggregated["entities"]
        all_relationships: list[dict[str, Any]] = aggregated["relationships"]

        logger.info(
            "mcp_submissions_parsed",
            source_id=source_id,
            total_entities=len(all_entities),
            total_relationships=len(all_relationships),
        )

        # Build file_info for commit service
        file_info = self._build_file_info(source, plan)

        # Finalize via extraction service (dedup, normalize, embed, suggest)
        result = await self.engine.extraction_service.finalize_distributed_extraction(
            raw_entities=all_entities,
            raw_relationships=all_relationships,
            # no entity vectors with vector search off (the engine has no provider)
            generate_embeddings=self.engine.embedding_service is not None,
            file_info=file_info,
            detected_domain=source_domain,
            forced_domain=forced_domain,
            edge_type_constraints=plan.edge_type_constraints,
            filtering_config=filtering_config,
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
        result_metadata = result.get("metadata") if isinstance(result, dict) else None
        filtering_log = (
            result_metadata.get("filtering_log") if isinstance(result_metadata, dict) else None
        )
        # Stamp the domain fingerprint onto the source row so re-extraction can
        # detect when the domain definition has drifted (worker-path parity).
        from chaoscypher_core.services.sources.engine.extraction.domains.fingerprint import (
            resolve_domain_fingerprint,
        )

        _dom_version, _dom_hash = resolve_domain_fingerprint(effective_domain, db)
        self._commit_phase.add(source_id)
        self.engine.storage_adapter.complete_extraction(
            source_id,
            entities=result.get("entities", []),
            relationships=result.get("relationships", []),
            detected_domain=source_domain,
            forced_domain=forced_domain,
            forced_packs=forced_packs,
            detected_packs=detected_packs,
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
            domain_name=effective_domain,
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

        # Evict the per-source plan cache entry now that finalize
        # has consumed it. Without this the dict accumulates one entry per
        # finalized source for the lifetime of the orchestrator (and a
        # long-lived MCP server process), which is a slow leak we don't
        # need — the cache only exists to bridge get_tasks → finalize.
        self._plans.pop(source_id, None)
        # Same bound for the group-index memo, for the same reason.
        self._group_indices.pop(source_id, None)
        # Same lifetime bound for the rate-limit bucket: submissions are done
        # once finalize commits, so the per-source timestamp deque is dead
        # weight from here on.
        self._submission_timestamps.pop(source_id, None)

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
