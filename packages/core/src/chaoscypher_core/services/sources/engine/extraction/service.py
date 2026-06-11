# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Business logic for Extraction feature.

Service orchestrates entity extraction and template matching by delegating
to focused sub-modules: preprocessor (normalization), extractor (AI extraction,
deduplication, embeddings), and template_matcher (edge suggestions).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    apply_cross_chunk_relationship_filters,
    generate_embeddings,
    run_deduplication,
)
from chaoscypher_core.services.sources.engine.extraction.preprocessor import (
    normalize_entities,
)
from chaoscypher_core.services.sources.engine.extraction.template_matcher import (
    suggest_edge_templates,
)
from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
    apply_domain_type_aliases,
    apply_structural_and_normalization,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class ExtractionService:
    """Business logic for entity extraction.

    This service is independent of the import workflow and can be used
    to extract entities from any text content.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        llm_provider: Any,
        settings: EngineSettings,
        *,
        embedding_service: Any,
    ) -> None:
        """Initialize extraction service.

        Args:
            graph_repository: GraphRepository for templates.
            llm_provider: LLMProvider for AI operations (queue-free).
            settings: Settings instance (EngineSettings or backend Settings).
            embedding_service: Embedding provider implementing
                EmbeddingProviderProtocol. Required keyword-only.
                Pass ``None`` explicitly to disable semantic deduplication
                (e.g. in unit tests that don't exercise dedup); otherwise
                pass the engine-managed instance. The default
                ``entity_deduplication_mode`` is "semantic", which silently
                degrades to exact-name dedup when this is None - making
                the kwarg required prevents callers from accidentally
                running degraded dedup.

        """
        self.graph_repository = graph_repository
        self.llm_provider = llm_provider
        self.settings = settings
        self.embedding_service = embedding_service

    @classmethod
    def from_engine(cls, engine: Any) -> ExtractionService:
        """Create an ExtractionService from an Engine instance.

        Convenience factory that wires dependencies from the engine's
        pre-configured services.

        Args:
            engine: Engine instance with llm_provider and graph_repository.

        Returns:
            Configured ExtractionService.

        """
        return cls(
            graph_repository=engine.graph_repository,
            llm_provider=engine.llm_provider,
            settings=engine.settings,
            embedding_service=engine.embedding_service,
        )

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def finalize_distributed_extraction(
        self,
        raw_entities: list[dict[str, Any]],
        raw_relationships: list[dict[str, Any]],
        generate_embeddings: bool = True,
        file_info: dict[str, Any] | None = None,
        detected_domain: str | None = None,
        forced_domain: str | None = None,
        *,
        edge_type_constraints: dict[str, dict[str, list[str]]] | None = None,
        filtering_config: FilteringConfig | None = None,
    ) -> dict[str, Any]:
        """Finalize extraction from pre-extracted chunk results.

        Used by distributed extraction (Cortex workers) where chunk extraction
        happens in parallel via queue, then finalization aggregates results.

        This performs all post-extraction steps:
        - Entity deduplication (exact or semantic)
        - Cross-chunk relationship filtering (type-constraint validation,
          relationship-limit enforcement) when ``edge_type_constraints`` or
          ``filtering_config`` is provided
        - Relationship index remapping
        - Template matching
        - Node and edge template suggestions
        - Embedding generation

        Args:
            raw_entities: Aggregated entities from all chunks
            raw_relationships: Aggregated relationships (indices relative to raw_entities)
            generate_embeddings: Whether to generate entity embeddings
            file_info: Optional file metadata for template suggestions
            detected_domain: Domain detected during extraction (for edge templates)
            forced_domain: User-forced domain override
            edge_type_constraints: Domain edge-type constraints used by the
                cross-chunk type-constraint filter. When ``None`` (default),
                the filter is skipped — appropriate for callers that already
                ran cross-chunk filtering upstream (e.g. ``extract_entities_from_groups``)
                or that don't have a domain in scope.
            filtering_config: Resolved FilteringConfig for cross-chunk filters.
                When ``None`` (default), cross-chunk filtering is skipped. The
                CLI extraction path threads this in so its pipeline matches
                the Cortex/Neuron worker path; the standalone ``Engine``-level
                callers run filters upstream and pass ``None`` here.

        Returns:
            Dictionary with:
                - entities: Deduplicated, template-matched entities
                - relationships: Remapped relationships
                - matched_templates: List of templates used
                - suggested_templates: Node template suggestions
                - suggested_edge_templates: Edge template suggestions
                - metadata: Processing metadata
                - embeddings: Optional embeddings data

        """
        try:
            logger.info(
                "distributed_extraction_finalization_started",
                raw_entity_count=len(raw_entities),
                raw_relationship_count=len(raw_relationships),
                generate_embeddings=generate_embeddings,
                detected_domain=detected_domain,
            )

            effective_domain = forced_domain or detected_domain

            # Resolve the FilteringConfig that gates the structural filter
            # (and, downstream, the cross-chunk filters). Callers that
            # already threaded a ``filtering_config`` win; otherwise we
            # fall back to the engine default mode so ``minimal`` and
            # ``unfiltered`` users on Cortex/Neuron actually skip
            # structural filtering rather than always stripping chapters.
            from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
                resolve_filtering_config,
            )

            if filtering_config is not None:
                resolved_filtering_config = filtering_config
            else:
                _default_mode = getattr(
                    getattr(self.settings, "extraction", None),
                    "extraction_filtering_mode",
                    "balanced",
                )
                try:
                    resolved_filtering_config = resolve_filtering_config(mode=str(_default_mode))
                except ValueError:
                    resolved_filtering_config = resolve_filtering_config()

            # Step 0: Apply domain type_aliases BEFORE dedup so name variants
            # split across alias types merge into one canonical entity (see
            # ``apply_domain_type_aliases`` for the rewrite semantics).
            # Resolves the domain once here and threads it into the
            # structural-and-normalization step further down to avoid
            # double-resolution.
            resolved_domain_obj = self._resolve_domain(effective_domain, forced=bool(forced_domain))
            apply_domain_type_aliases(raw_entities, resolved_domain_obj)

            # Step 1: Deduplicate, remap, resolve names
            (
                deduplicated,
                remapped,
                cached_embeddings,
                _dedup_filtering_log,
            ) = await run_deduplication(
                entities=raw_entities,
                relationships=raw_relationships,
                detected_domain=effective_domain,
                settings=self.settings,
                embedding_service=self.embedding_service,
                domain_resolver=self,
                filtering_config=resolved_filtering_config,
            )

            # Step 1b: Cross-chunk relationship filtering (Phase 6 reorder).
            # Type-constraint validation and relationship-limit enforcement
            # run AFTER dedup so canonical entities carry their consolidated
            # edges through the filter -- not chunk-local fragments. Mirrors
            # the standalone ``extract_entities_from_groups`` and Cortex/Neuron
            # ``_apply_post_dedup_filters`` paths so all three pipelines
            # agree on filter ordering. See ``apply_cross_chunk_relationship_filters``.
            if edge_type_constraints is not None or filtering_config is not None:
                deduplicated, remapped = apply_cross_chunk_relationship_filters(
                    entities=deduplicated,
                    relationships=remapped,
                    edge_type_constraints=edge_type_constraints,
                    filtering_config=resolved_filtering_config,
                )

            # Step 1c: Filter structural entities + apply domain-specific
            # type normalization, in lockstep with the worker path
            # (``_finalize_extraction_inner``) and the standalone helper
            # (``extract_entities_from_groups``). Production parity
            # (Workstream 3, Tasks 3.1+3.2): the structural filter strips
            # chapter / section / part markers; type normalization renames
            # generic ``Item``/``Concept``/``Unknown`` to their domain
            # target (e.g. ``Class`` for technical) when the description
            # matches a domain rule. Both gated through the shared helper
            # so ``minimal`` / ``unfiltered`` modes skip the structural
            # filter and custom-domain ``generic_types`` (e.g. ``Notion``,
            # ``Idea``) drive the normalization.
            # ``resolved_domain_obj`` was already resolved at Step 0 above.
            normalization_rules = self._get_domain_normalization_rules(effective_domain)
            deduplicated, remapped, structural_filtered = apply_structural_and_normalization(
                deduplicated,
                remapped,
                domain=resolved_domain_obj,
                filtering_config=resolved_filtering_config,
                normalization_rules=normalization_rules,
            )
            if structural_filtered > 0:
                logger.info(
                    "structural_entities_filtered_in_finalizer",
                    removed=structural_filtered,
                    remaining=len(deduplicated),
                )

            # Step 2: Build extraction results (normalize, suggest, embed)
            results = await self._build_extraction_results(
                deduplicated,
                remapped,
                generate_embeddings=generate_embeddings,
                cached_embeddings=cached_embeddings,
                detected_domain=detected_domain,
                forced_domain=forced_domain,
                extraction_depth="distributed",
            )

            logger.info(
                "distributed_extraction_finalization_completed",
                total_entities=results["metadata"]["total_entities"],
                total_relationships=results["metadata"]["total_relationships"],
                node_suggestions=len(results["suggested_templates"]),
                edge_suggestions=len(results["suggested_edge_templates"]),
                embeddings_generated=results["metadata"]["embeddings_generated"],
            )
            return results

        except Exception as e:
            logger.exception(
                "distributed_extraction_finalization_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    async def extract(
        self,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        *,
        domain: str | None = None,
        generate_embeddings: bool = True,
        edge_type_constraints: dict[str, dict[str, list[str]]] | None = None,
        filtering_config: FilteringConfig | None = None,
    ) -> dict[str, Any]:
        """Extract, deduplicate, and normalize entities and relationships.

        Clean alias for finalize_distributed_extraction with simplified
        parameter names.

        Args:
            entities: Raw entities from chunk-level extraction.
            relationships: Raw relationships (indices relative to entities).
            domain: Detected or forced domain name (e.g., 'literary', 'scientific').
            generate_embeddings: Generate entity embeddings. Defaults to True.
            edge_type_constraints: See ``finalize_distributed_extraction``.
            filtering_config: See ``finalize_distributed_extraction``.

        Returns:
            Dict with 'entities', 'relationships', 'suggested_templates',
            'suggested_edge_templates', 'metadata', and 'embeddings' keys.

        """
        return await self.finalize_distributed_extraction(
            raw_entities=entities,
            raw_relationships=relationships,
            generate_embeddings=generate_embeddings,
            detected_domain=domain,
            edge_type_constraints=edge_type_constraints,
            filtering_config=filtering_config,
        )

    async def build_extraction_results(
        self,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        *,
        generate_embeddings: bool,
        cached_embeddings: list[Any],
        detected_domain: str | None,
        forced_domain: str | None = None,
        extraction_depth: str = "full",
    ) -> dict[str, Any]:
        """Public entry point for building extraction results.

        Normalizes entities, generates template suggestions and embeddings,
        and builds the final results dict.

        Args:
            entities: Deduplicated entities.
            relationships: Remapped relationships.
            generate_embeddings: Whether to generate entity embeddings.
            cached_embeddings: Cached embeddings from semantic dedup.
            detected_domain: Auto-detected domain.
            forced_domain: User-forced domain override.
            extraction_depth: Depth for metadata.

        Returns:
            Complete extraction results dictionary.

        """
        return await self._build_extraction_results(
            entities,
            relationships,
            generate_embeddings=generate_embeddings,
            cached_embeddings=cached_embeddings,
            detected_domain=detected_domain,
            forced_domain=forced_domain,
            extraction_depth=extraction_depth,
        )

    def get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Public entry point for getting domain title words.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'historical')

        Returns:
            Frozenset of lowercase title words, or None if unavailable.

        """
        return self._get_domain_title_words(domain_name)

    def get_domain_type_compatibility(self, domain_name: str | None) -> dict[str, list[str]] | None:
        """Public entry point for getting domain type compatibility groups.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'technical')

        Returns:
            Dictionary of compatibility groups, or None if unavailable.

        """
        return self._get_domain_type_compatibility(domain_name)

    def get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Public entry point for getting domain inverse relationship mappings.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'historical')

        Returns:
            Mapping of edge type to inverse edge type, or empty dict.

        """
        return self._get_domain_inverse_relationships(domain_name)

    def get_domain_normalization_rules(self, domain_name: str | None) -> dict[str, list[str]]:
        """Public entry point for getting domain type-normalization rules.

        Used by the production finalizer to re-type generic entities
        (``Item`` -> ``Class``) the same way ``extract_entities_from_groups``
        does.

        Args:
            domain_name: Name of the domain (e.g., 'technical', 'literary').

        Returns:
            Mapping of target type to keyword list, or empty dict.

        """
        return self._get_domain_normalization_rules(domain_name)

    def get_domain_symmetric_relationships(self, domain_name: str | None) -> list[str]:
        """Public entry point for getting domain symmetric relationship types.

        Symmetric relationships are bidirectional — (A, B) and (B, A) are
        semantically identical and collapsed during deduplication.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'historical')

        Returns:
            List of symmetric relationship type names, or empty list.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return []
        try:
            return cast("list[str]", domain.get_symmetric_relationships())
        except Exception:
            logger.debug("domain_symmetric_relationships_unavailable", domain=domain_name)
        return []

    # ------------------------------------------------------------------ #
    #  Shared finalization pipeline
    # ------------------------------------------------------------------ #

    async def _build_extraction_results(
        self,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        *,
        generate_embeddings: bool,
        cached_embeddings: list[Any],
        detected_domain: str | None,
        forced_domain: str | None = None,
        extraction_depth: str = "full",
    ) -> dict[str, Any]:
        """Normalize entities, generate suggestions/embeddings, build result dict.

        Shared finalization logic used by ``finalize_distributed_extraction``.

        Args:
            entities: Deduplicated entities.
            relationships: Remapped relationships.
            generate_embeddings: Whether to generate entity embeddings.
            cached_embeddings: Cached embeddings from semantic dedup.
            detected_domain: Auto-detected domain.
            forced_domain: User-forced domain override.
            extraction_depth: Depth for metadata ("full", "quick", "distributed").

        Returns:
            Complete extraction results dictionary.

        """
        import asyncio

        # Step 1: Normalize entities for consistent structure
        normalized = normalize_entities(entities)

        # Steps 2-4: Run template suggestions (sync) concurrently with embeddings (async)
        effective_domain = forced_domain or detected_domain
        domain_node_templates = None
        if effective_domain:
            domain_node_templates = self._get_domain_node_templates(effective_domain)

        loop = asyncio.get_event_loop()

        # Wrap sync template suggestion calls for concurrent execution
        async def _suggest_templates() -> tuple[list, list, dict]:
            """Off-thread node/edge template suggestions plus inverse-rel lookup."""
            node_suggestions = await loop.run_in_executor(
                None,
                lambda: TemplateExtractor.generate_suggestions_from_entities(
                    entities=normalized,
                    domain_templates=domain_node_templates,
                ),
            )
            edge_suggestions = await loop.run_in_executor(
                None,
                lambda: suggest_edge_templates(
                    relationships,
                    detected_domain=effective_domain,
                    get_domain_edge_templates=self._get_domain_edge_templates,
                ),
            )
            inverse_rels = self._get_domain_inverse_relationships(effective_domain)
            return node_suggestions, edge_suggestions, inverse_rels

        async def _do_embeddings() -> dict[str, Any] | None:
            """Generate embeddings for normalized entities, or None if disabled/skipped."""
            if not generate_embeddings or not normalized:
                return None
            try:
                result = await self._generate_embeddings(normalized, cached_embeddings)
                logger.info(
                    "embeddings_generated",
                    entity_count=result["count"],
                    model=result["model"],
                    dimensions=result["dimensions"],
                    cached_count=result.get("cached_count", 0),
                )
                return result
            except Exception as e:
                logger.exception(
                    "embeddings_generation_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                return None

        (
            (node_suggestions, edge_suggestions, inverse_relationships),
            embeddings_result,
        ) = await asyncio.gather(_suggest_templates(), _do_embeddings())

        if node_suggestions:
            logger.info("node_templates_suggested", suggestion_count=len(node_suggestions))
        if edge_suggestions:
            logger.info("edge_templates_suggested", suggested_count=len(edge_suggestions))

        # Step 5: Build results
        metadata: dict[str, Any] = {
            "extraction_depth": extraction_depth,
            "total_entities": len(normalized),
            "total_relationships": len(relationships),
            "detected_domain": detected_domain,
            "embeddings_generated": embeddings_result is not None,
        }
        if forced_domain is not None:
            metadata["forced_domain"] = forced_domain

        results: dict[str, Any] = {
            "entities": normalized,
            "relationships": relationships,
            "matched_templates": [],  # Templates matched at commit time, not extraction
            "suggested_templates": node_suggestions,
            "suggested_edge_templates": edge_suggestions,
            "inverse_relationships": inverse_relationships,
            "metadata": metadata,
        }
        if embeddings_result:
            results["embeddings"] = embeddings_result

        return results

    # ------------------------------------------------------------------ #
    #  Domain helpers
    # ------------------------------------------------------------------ #

    def _resolve_domain(self, domain_name: str | None, *, forced: bool = False) -> Any | None:
        """Resolve a domain name to a domain object.

        Args:
            domain_name: Name of the domain (may be None for "no specific domain").
            forced: When True, the caller is honoring an explicit user choice
                (e.g., the source row's ``forced_domain`` column). Registry
                failures must propagate so the user sees a clear error
                rather than silently running as a generic extraction.
                When False (auto-detection), failures are swallowed and
                cached as None for best-effort progress.

        Returns:
            Domain object or None when domain_name is None or auto-detect
            failed.

        Raises:
            Anything ``registry.get_domain`` raises, when ``forced=True``.

        """
        if not domain_name:
            return None

        # Use instance-level cache (lives for the duration of one extraction call)
        cache = getattr(self, "_domain_cache", None)
        if cache is None:
            cache = {}
            self._domain_cache = cache

        if domain_name in cache:
            return cache[domain_name]

        try:
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                get_domain_registry,
            )

            registry = get_domain_registry(self.settings)
            domain = registry.get_domain(domain_name)
            cache[domain_name] = domain
            return domain
        except Exception:
            if forced:
                # User explicitly forced this domain — don't silently
                # downgrade. Phase 1 (2026-05-08).
                logger.exception(
                    "forced_domain_resolution_failed",
                    domain=domain_name,
                )
                raise
            logger.debug("domain_resolution_failed", domain=domain_name)
            cache[domain_name] = None
            return None

    def _get_domain_title_words(self, domain_name: str | None) -> frozenset[str] | None:
        """Get title words from domain config, if available.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'historical')

        Returns:
            Frozenset of lowercase title words, or None if unavailable.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            words = domain.get_title_words()
            return frozenset(w.lower() for w in words) if words else None
        except Exception:
            logger.debug("domain_title_words_unavailable", domain=domain_name)
        return None

    def _get_domain_type_compatibility(
        self, domain_name: str | None
    ) -> dict[str, list[str]] | None:
        """Get type compatibility groups from domain config, if available.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'technical')

        Returns:
            Dictionary of compatibility groups, or None if unavailable.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            compat = domain.get_type_compatibility()
            return compat if compat else None
        except Exception:
            logger.debug("domain_type_compatibility_unavailable", domain=domain_name)
        return None

    def _get_domain_node_templates(self, domain_name: str) -> list[dict[str, Any]] | None:
        """Get node templates from a domain by name.

        Args:
            domain_name: Name of the domain (e.g., 'philosophical', 'historical')

        Returns:
            List of node template dicts with 'name' and 'description', or None

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            templates = domain.get_templates()
            node_templates: list[dict[str, Any]] = templates.get("node_templates", [])
            if node_templates:
                logger.debug(
                    "domain_node_templates_found",
                    domain=domain_name,
                    count=len(node_templates),
                )
                return node_templates
            return None
        except Exception as e:
            logger.warning(
                "domain_node_templates_lookup_failed",
                domain=domain_name,
                error=str(e),
            )
            return None

    def _get_domain_normalization_rules(self, domain_name: str | None) -> dict[str, list[str]]:
        """Get type-normalization rules from domain config.

        Mirrors the standalone helper's call to
        ``ConfigurableDomain.get_normalization_rules()`` so the distributed
        finalizer can re-type generic entities (``Item`` -> ``Class``)
        the same way ``extract_entities_from_groups`` does.

        Args:
            domain_name: Name of the domain (e.g., 'technical', 'literary').

        Returns:
            Mapping of target type to keyword list, or empty dict.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return {}
        try:
            result: dict[str, list[str]] = domain.get_normalization_rules()
            return result or {}
        except Exception:
            logger.debug("domain_normalization_rules_unavailable", domain=domain_name)
            return {}

    def _get_domain_inverse_relationships(self, domain_name: str | None) -> dict[str, str]:
        """Get inverse relationship map from domain config.

        Args:
            domain_name: Name of the domain (e.g., 'literary', 'historical')

        Returns:
            Mapping of edge type to inverse edge type, or empty dict.

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return {}
        try:
            result: dict[str, str] = domain.get_inverse_relationships()
            return result
        except Exception:
            logger.debug("domain_inverse_relationships_unavailable", domain=domain_name)
        return {}

    def _get_domain_edge_templates(self, domain_name: str) -> list[dict[str, Any]] | None:
        """Get edge templates from a domain by name.

        Args:
            domain_name: Name of the domain (e.g., 'philosophical', 'historical')

        Returns:
            List of edge template dicts with 'name' and 'description', or None

        """
        domain = self._resolve_domain(domain_name)
        if not domain:
            return None
        try:
            templates = domain.get_templates()
            edge_templates: list[dict[str, Any]] = templates.get("edge_templates", [])
            if edge_templates:
                logger.debug(
                    "domain_edge_templates_found",
                    domain=domain_name,
                    count=len(edge_templates),
                )
                return edge_templates
            return None
        except Exception as e:
            logger.warning(
                "domain_edge_templates_lookup_failed",
                domain=domain_name,
                error=str(e),
            )
            return None

    # ------------------------------------------------------------------ #
    #  Delegating methods
    # ------------------------------------------------------------------ #

    async def _generate_embeddings(
        self, entities: list[dict[str, Any]], cached_embeddings: list[list[float]] | None = None
    ) -> dict[str, Any]:
        """Generate embeddings for entities.

        Delegates to :func:`extractor.generate_embeddings`.

        Args:
            entities: List of entities
            cached_embeddings: Optional cached embeddings from deduplication

        Returns:
            Dictionary with count, model, dimensions, cached_count

        """
        return await generate_embeddings(
            entities=entities,
            embedding_service=self.embedding_service,
            settings=self.settings,
            cached_embeddings=cached_embeddings,
        )
