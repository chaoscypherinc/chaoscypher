# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Template Matcher.

Matches entities to templates created for the current source.
Each source gets its own templates - no cross-source template sharing.

Extracted from commit_service.py for SRP compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.settings import GraphSettings


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)

# Load graph settings for default template
_DEFAULT_NODE_TEMPLATE = GraphSettings().default_node_template


class EntityTemplateMatcher:
    """Matches entities to templates created for the current source.

    With per-source templates, matching is simplified:
    1. Exact match in current source's templates (from template_name_to_id)
    2. Fallback to system_template_item

    No cross-source template reuse, no synonym matching, no partial matching.
    Each source creates its own templates based on AI suggestions.

    Attributes:
        graph_repository: GraphRepository instance (kept for interface compatibility)
        _template_cache: Dict mapping cache keys to template IDs for performance

    Example:
        >>> from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher
        >>> from chaoscypher_core.adapters.sqlite.repos import GraphRepository
        >>>
        >>> graph_repo = GraphRepository(session=session, database_name="default")
        >>> matcher = EntityTemplateMatcher(graph_repo)
        >>>
        >>> # Match entities to templates created for this source
        >>> template_name_to_id = {"person": "template_xyz", "organization": "template_abc"}
        >>> entity = {"type": "PERSON", "name": "Einstein"}
        >>> template_id = matcher.match(
        ...     entity_data=entity,
        ...     all_templates=[],  # Not used for per-source matching
        ...     suggested_templates=None,  # Not used
        ...     template_name_to_id=template_name_to_id
        ... )
        >>> print(f"Matched to: {template_id}")
        Matched to: template_xyz

    Note:
        System templates (workflow, lens) are excluded from matching.

    """

    # System templates to skip during matching
    SKIP_TEMPLATES: ClassVar[set[str]] = {"system_workflow", "system_workflow_step", "system_lens"}

    def __init__(self, graph_repository: GraphRepositoryProtocol) -> None:
        """Initialize the matcher.

        Args:
            graph_repository: GraphRepository instance — used both for entity
                template matching and for the lazy fallback-template self-heal
                in :meth:`_ensure_fallback_template_exists`.

        """
        self.graph_repository = graph_repository
        self._template_cache: dict[str, str] = {}
        # Tracks whether the fallback template's existence has been confirmed
        # for the lifetime of this matcher instance. A matcher is constructed
        # fresh for every commit (see SourceCommitService.__init__), so the
        # check fires at most once per commit even when the entity loop hits
        # the fallback path many times.
        self._fallback_verified: bool = False

    def match(
        self,
        entity_data: dict,
        all_templates: list[Any],
        suggested_templates: list[dict] | None = None,
        template_name_to_id: dict[str, str] | None = None,
    ) -> str:
        """Match an entity to a template from the current source.

        Args:
            entity_data: Entity data with 'type', 'name', etc.
            all_templates: List of all available templates (not used for per-source matching)
            suggested_templates: Not used (kept for interface compatibility)
            template_name_to_id: Mapping of template names to IDs for current source

        Returns:
            Template ID (defaults to 'system_template_item' if no match)

        """
        entity_type = entity_data.get("type", "").lower()

        if not entity_type:
            return _DEFAULT_NODE_TEMPLATE

        # Check cache first
        cache_key = self._build_cache_key(entity_type, template_name_to_id)
        if cache_key in self._template_cache:
            logger.debug("template_cache_hit", entity_type=entity_type)
            return self._template_cache[cache_key]

        # Match in current source's templates (exact match only)
        if template_name_to_id:
            template_id = self._match_in_source_templates(entity_type, template_name_to_id)
            if template_id:
                self._template_cache[cache_key] = template_id
                return template_id

        # Fallback to system_template_item — defensively make sure the row
        # is actually there. If a delete cascade or buggy reset removed it,
        # the FK on graph_nodes.template_id would otherwise blow up the
        # entire commit batch with "FOREIGN KEY constraint failed". Migration
        # 0015 prevents the cascade-delete path going forward, but the
        # self-heal stays as a defense-in-depth covering any other future
        # path that might lose it (manual SQL, plugin bug, partial restore).
        self._ensure_fallback_template_exists()

        logger.info(
            "template_match_fallback",
            entity_type=entity_type,
            fallback_template=_DEFAULT_NODE_TEMPLATE,
        )
        self._template_cache[cache_key] = _DEFAULT_NODE_TEMPLATE
        return _DEFAULT_NODE_TEMPLATE

    def _build_cache_key(self, entity_type: str, template_name_to_id: dict[str, str] | None) -> str:
        """Build a cache key from entity type and template mapping."""
        template_hash = hash(frozenset(template_name_to_id.items())) if template_name_to_id else 0
        return f"{entity_type}:{template_hash}"

    def _ensure_fallback_template_exists(self) -> None:
        """Lazily re-create the fallback template if it was deleted.

        Runs at most once per matcher instance (per commit) — guarded by
        ``_fallback_verified``. The first call after a fresh commit pays one
        ``get_template`` SELECT; if the row is present, every subsequent
        fallback in the same commit is a flag check.

        If the row is missing, rebuild it from
        ``chaoscypher_core.templates.default_templates`` so the FK on
        ``graph_nodes.template_id`` stays satisfiable. Logs a warning when
        the heal fires — that's a signal something else deleted a system
        template and is worth investigating.

        Raises:
            RuntimeError: (programmer error) If ``default_node_template``
                in settings has drifted from ``DEFAULT_NODE_TEMPLATES``.
                This indicates a config inconsistency, not user input.

        """
        if self._fallback_verified:
            return

        existing = self.graph_repository.get_template(_DEFAULT_NODE_TEMPLATE)
        if existing is not None:
            self._fallback_verified = True
            return

        # Re-create from defaults. Imported inside the method to avoid a
        # module-load-time dependency on the templates package — the matcher
        # is otherwise a leaf in the import graph.
        from chaoscypher_core.models import PropertyDefinition, TemplateCreate
        from chaoscypher_core.templates.default_templates import DEFAULT_NODE_TEMPLATES

        spec = next(
            (t for t in DEFAULT_NODE_TEMPLATES if t["id"] == _DEFAULT_NODE_TEMPLATE),
            None,
        )
        if spec is None:
            # The fallback constant points at an id not in the seeded defaults
            # — a config drift the matcher can't paper over. Loud failure now
            # is better than 212 silent FK errors later.
            msg = (
                f"Fallback template {_DEFAULT_NODE_TEMPLATE!r} is not in "
                "DEFAULT_NODE_TEMPLATES — settings.GraphSettings.default_node_template "
                "and templates/default_templates.py have drifted apart."
            )
            raise RuntimeError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: config drift between default_node_template setting and DEFAULT_NODE_TEMPLATES; cannot reach a user
                msg
            )

        properties = [
            p if isinstance(p, PropertyDefinition) else PropertyDefinition(**dict(p))
            for p in spec.get("properties", [])
        ]
        template_create = TemplateCreate(
            name=spec["name"],
            description=spec.get("description"),
            template_type=spec["template_type"],
            properties=properties,
            icon=spec.get("icon"),
            color=spec.get("color"),
        )
        self.graph_repository.create_template(
            template_create,
            custom_id=_DEFAULT_NODE_TEMPLATE,
            is_system=True,
        )
        logger.warning(
            "fallback_template_self_healed",
            template_id=_DEFAULT_NODE_TEMPLATE,
            reason="missing_at_commit_time",
        )
        self._fallback_verified = True

    def _match_in_source_templates(
        self, entity_type: str, template_name_to_id: dict[str, str]
    ) -> str | None:
        """Match entity type to templates created for this source.

        Uses exact matching only - no partial/fuzzy matching.
        Template names in template_name_to_id are already lowercase.

        Args:
            entity_type: Lowercase entity type from extraction
            template_name_to_id: Mapping of template names to IDs for current source

        Returns:
            Template ID if matched, None otherwise

        """
        # Exact match (case-insensitive - entity_type and keys are both lowercase)
        if entity_type in template_name_to_id:
            template_id = template_name_to_id[entity_type]
            logger.info(
                "template_matched_source",
                entity_type=entity_type,
                template_id=template_id,
            )
            return template_id

        return None
