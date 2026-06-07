# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template operations mixin for GraphRepository."""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlmodel import func, select

from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.graph_mixin_base import GraphMixinBase
from chaoscypher_core.models import PropertyDefinition, Template, TemplateCreate, TemplateUpdate


logger = structlog.get_logger(__name__)


def _stable_template_id(
    *,
    database_name: str,
    source_id: str | None,
    template_type: str,
    name: str,
) -> str:
    """Derive a content-addressed template ID from commit-time inputs.

    Per the per-source-templates model (migration 008), templates are
    owned by a specific source. Within a source, a (template_type,
    normalized name) pair uniquely identifies a template — so hashing
    those three scopes gives a key that stays identical across
    crash-and-resume commit attempts.

    Args:
        database_name: Active database.
        source_id: Source that owns this template. Templates without
            a source (system templates) fall back to "no_source".
        template_type: "node" or "edge".
        name: Template name; normalized (strip + lower) before hashing.

    Returns:
        Deterministic string of the form ``template_<24-hex-chars>``.
    """
    canonical_name = (name or "").strip().lower()
    scope_source = source_id or "no_source"
    raw = f"{database_name}:{scope_source}:{template_type}:{canonical_name}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"template_{digest}"


class TemplateOperationsMixin(GraphMixinBase):
    """Mixin providing template CRUD operations for GraphRepository."""

    def create_template(
        self,
        template_create: TemplateCreate,
        custom_id: str | None = None,
        is_system: bool = False,
    ) -> Template:
        """Create a new template."""
        template_id = custom_id or self._generate_id("template")

        # Convert PropertyDefinition objects to dicts for storage
        properties_data = [p.model_dump() for p in template_create.properties]

        db_template = GraphTemplate(
            id=template_id,
            database_name=self.database_name,
            name=template_create.name,
            template_type=template_create.template_type,
            description=template_create.description,
            is_system=is_system,
            properties=properties_data,
            icon=template_create.icon,
            color=template_create.color,
            source_id=template_create.source_id,
        )

        self.session.add(db_template)
        self.session.maybe_commit()
        self.session.refresh(db_template)

        return self._db_template_to_model(db_template)

    async def create_templates_batch(
        self, templates_to_create: list[TemplateCreate]
    ) -> list[Template]:
        """Batch create multiple templates."""
        if not templates_to_create:
            return []

        created_templates = []

        for template_create in templates_to_create:
            template_id = self._generate_id("template")
            properties_data = [p.model_dump() for p in template_create.properties]

            db_template = GraphTemplate(
                id=template_id,
                database_name=self.database_name,
                name=template_create.name,
                template_type=template_create.template_type,
                description=template_create.description,
                is_system=False,
                properties=properties_data,
                icon=template_create.icon,
                color=template_create.color,
                source_id=template_create.source_id,
            )
            self.session.add(db_template)
            created_templates.append(db_template)

        self.session.maybe_commit()

        result = [self._db_template_to_model(t) for t in created_templates]

        logger.info(
            "templates_batch_created",
            template_count=len(result),
            template_type=result[0].template_type if result else None,
        )

        return result

    def upsert_template(
        self,
        template_create: TemplateCreate,
        is_system: bool = False,
    ) -> tuple[Template, bool]:
        """Idempotently create a template by stable content key.

        Commit-path entry point. Templates are the first step of the
        commit phase — nodes hash their template_id into their own
        stable key, so templates MUST be upserted before nodes. On a
        re-dispatched commit, the template row already exists and is
        returned unchanged.

        Semantics mirror upsert_nodes_batch: first write wins, no
        property overwrite on re-upsert, preserving whatever the
        first successful commit pass established.

        Args:
            template_create: Template to create (or reuse).
            is_system: Whether this is a system template (affects
                ``is_system`` column but not the stable key scope —
                system templates still scope by source_id=None).

        Returns:
            Tuple of:
            - Template Pydantic model with a stable .id.
            - True if the template was newly inserted, False if pre-existing.
        """
        template_id = _stable_template_id(
            database_name=self.database_name,
            source_id=template_create.source_id,
            template_type=template_create.template_type,
            name=template_create.name,
        )

        existing = self.session.get(GraphTemplate, template_id)
        if existing is not None:
            logger.debug(
                "template_upsert_reused",
                template_id=template_id,
                name=template_create.name,
                source_id=template_create.source_id,
            )
            return self._db_template_to_model(existing), False

        properties_data = [p.model_dump() for p in template_create.properties]
        db_template = GraphTemplate(
            id=template_id,
            database_name=self.database_name,
            name=template_create.name,
            template_type=template_create.template_type,
            description=template_create.description,
            is_system=is_system,
            properties=properties_data,
            icon=template_create.icon,
            color=template_create.color,
            source_id=template_create.source_id,
        )
        self.session.add(db_template)
        self.session.maybe_commit()
        self.session.refresh(db_template)

        logger.info(
            "template_upsert_created",
            template_id=template_id,
            name=template_create.name,
            source_id=template_create.source_id,
        )
        return self._db_template_to_model(db_template), True

    async def upsert_templates_batch(
        self, templates_to_create: list[TemplateCreate]
    ) -> tuple[list[Template], int]:
        """Idempotently create a batch of templates by stable content key.

        Mirror of ``upsert_nodes_batch`` / ``upsert_edges_batch``: uses
        a single bulk SELECT-by-id to detect pre-existing rows, then
        inserts only the genuinely new ones. Returns the full list in
        input order.

        Returns:
            Tuple of:
            - List of Template objects (created or pre-existing) in input order.
            - Count of rows actually inserted (not counting dedup reuses).
        """
        if not templates_to_create:
            return [], 0

        stable_ids = [
            _stable_template_id(
                database_name=self.database_name,
                source_id=tc.source_id,
                template_type=tc.template_type,
                name=tc.name,
            )
            for tc in templates_to_create
        ]

        existing_rows: dict[str, GraphTemplate] = {}
        if stable_ids:
            from sqlmodel import col

            existing_stmt = select(GraphTemplate).where(
                GraphTemplate.database_name == self.database_name,
                col(GraphTemplate.id).in_(stable_ids),
            )
            for row in self.session.scalars(existing_stmt).all():
                existing_rows[row.id] = row

        new_entities: list[GraphTemplate] = []
        result_entities: list[GraphTemplate] = []
        batch_seen: dict[str, GraphTemplate] = {}
        for stable_id, tc in zip(stable_ids, templates_to_create, strict=True):
            if stable_id in existing_rows:
                result_entities.append(existing_rows[stable_id])
                continue
            if stable_id in batch_seen:
                result_entities.append(batch_seen[stable_id])
                continue
            properties_data = [p.model_dump() for p in tc.properties]
            db_template = GraphTemplate(
                id=stable_id,
                database_name=self.database_name,
                name=tc.name,
                template_type=tc.template_type,
                description=tc.description,
                is_system=False,
                properties=properties_data,
                icon=tc.icon,
                color=tc.color,
                source_id=tc.source_id,
            )
            self.session.add(db_template)
            new_entities.append(db_template)
            result_entities.append(db_template)
            batch_seen[stable_id] = db_template

        inserted_count = len(new_entities)

        if new_entities:
            self.session.maybe_commit()

        logger.info(
            "templates_batch_upserted",
            total=len(templates_to_create),
            new=inserted_count,
            reused=len(templates_to_create) - inserted_count,
        )
        return [self._db_template_to_model(t) for t in result_entities], inserted_count

    def get_template(self, template_id: str) -> Template | None:
        """Get a template by ID."""
        statement = select(GraphTemplate).where(
            GraphTemplate.id == template_id,
            GraphTemplate.database_name == self.database_name,
        )
        db_template = self.session.exec(statement).first()

        if db_template is None:
            return None

        return self._db_template_to_model(db_template)

    def list_templates(
        self,
        template_type: str | None = None,
        include_disabled_sources: bool = False,
        source_id: str | None = None,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Template]:
        """List templates, optionally filtered by type and source enabled status.

        Args:
            template_type: Filter by template type ('node' or 'edge').
            include_disabled_sources: If False, hide templates from disabled sources.
            source_id: Filter by source ID (optional).
            skip: Number of results to skip (for SQL-level pagination).
            limit: Maximum number of results. None returns all matching templates.

        Returns:
            List of Template objects.

        """
        statement = (
            select(GraphTemplate)
            .options(
                load_only(
                    GraphTemplate.id,
                    GraphTemplate.database_name,
                    GraphTemplate.name,
                    GraphTemplate.template_type,
                    GraphTemplate.description,
                    GraphTemplate.is_system,
                    GraphTemplate.icon,
                    GraphTemplate.color,
                    GraphTemplate.properties,
                    GraphTemplate.created_at,
                    GraphTemplate.updated_at,
                    GraphTemplate.source_id,
                    # Excluded: embedding (BLOB), embedding_model, embedding_dimensions
                )
            )
            .where(GraphTemplate.database_name == self.database_name)
        )

        if template_type is not None:
            statement = statement.where(GraphTemplate.template_type == template_type)

        if source_id is not None:
            statement = statement.where(GraphTemplate.source_id == source_id)

        # Filter by source enabled status (unless including disabled or filtering by specific source)
        if not include_disabled_sources and source_id is None:
            # Show templates where:
            # - source_id is NULL (system templates, always visible)
            # - OR source is enabled
            statement = statement.outerjoin(
                SourceRow, GraphTemplate.source_id == SourceRow.id
            ).where(
                (GraphTemplate.source_id.is_(None)) | (SourceRow.enabled == True)  # noqa: E712
            )

        statement = statement.order_by(GraphTemplate.id)
        if skip:
            statement = statement.offset(skip)
        if limit is not None:
            statement = statement.limit(limit)

        db_templates = self.session.exec(statement).all()

        return [self._db_template_to_model(t) for t in db_templates]

    def update_template(
        self, template_id: str, template_update: TemplateUpdate | dict[str, Any]
    ) -> Template | None:
        """Update an existing template.

        Args:
            template_id: ID of the template to update
            template_update: TemplateUpdate model or dict with fields to update

        Returns:
            Updated Template model or None if not found

        """
        statement = select(GraphTemplate).where(
            GraphTemplate.id == template_id,
            GraphTemplate.database_name == self.database_name,
        )
        db_template = self.session.exec(statement).first()

        if db_template is None:
            return None

        # Handle both TemplateUpdate model and dict
        if isinstance(template_update, dict):
            update_data = template_update
        else:
            update_data = template_update.model_dump(exclude_unset=True)

        if "name" in update_data and update_data["name"] is not None:
            db_template.name = update_data["name"]

        if "description" in update_data and update_data["description"] is not None:
            db_template.description = update_data["description"]

        if "properties" in update_data and update_data["properties"] is not None:
            props = update_data["properties"]
            if props and hasattr(props[0], "model_dump"):
                db_template.properties = [p.model_dump() for p in props]
            else:
                db_template.properties = props

        # Handle visual identity fields
        if "icon" in update_data:
            db_template.icon = update_data["icon"]

        if "color" in update_data:
            db_template.color = update_data["color"]

        # Handle embedding fields
        if "embedding" in update_data and update_data["embedding"] is not None:
            db_template.embedding = update_data["embedding"]

        if "embedding_model" in update_data and update_data["embedding_model"] is not None:
            db_template.embedding_model = update_data["embedding_model"]

        if (
            "embedding_dimensions" in update_data
            and update_data["embedding_dimensions"] is not None
        ):
            db_template.embedding_dimensions = update_data["embedding_dimensions"]

        db_template.updated_at = datetime.now(UTC)

        self.session.add(db_template)
        self.session.maybe_commit()
        self.session.refresh(db_template)

        return self._db_template_to_model(db_template)

    def delete_template(self, template_id: str, force: bool = False) -> bool:
        """Delete a template by ID.

        Behavior:
            * ``force=False`` (default) — if any nodes or edges reference
              this template, raises ``ValueError`` with a count. The
              cortex handler maps this to HTTP 409 ``TEMPLATE_IN_USE``.
            * ``force=True`` — cascade-delete every dependent edge and
              node first, then the template. Used by the UI's
              "force delete" affordance so an operator can drop a
              template without manually hunting down everything that
              uses it. Without this, FK constraint added by migration
              0014 (``graph_nodes.template_id ON DELETE RESTRICT``)
              fires as IntegrityError and surfaces as a generic 500.
        """
        statement = select(GraphTemplate).where(
            GraphTemplate.id == template_id,
            GraphTemplate.database_name == self.database_name,
        )
        db_template = self.session.exec(statement).first()

        if db_template is None:
            return False

        nodes_count = self.session.exec(
            select(func.count(GraphNode.id)).where(
                GraphNode.database_name == self.database_name,
                GraphNode.template_id == template_id,
            )
        ).one()
        edges_count = self.session.exec(
            select(func.count(GraphEdge.id)).where(
                GraphEdge.database_name == self.database_name,
                GraphEdge.template_id == template_id,
            )
        ).one()

        if not force:
            if nodes_count > 0:
                msg = f"Unable to delete '{db_template.name}': in use by {nodes_count} nodes"
                raise ValueError(msg)
            if edges_count > 0:
                msg = f"Unable to delete '{db_template.name}': in use by {edges_count} edges"
                raise ValueError(msg)
        else:
            # Cascade. Order matters because of FK constraints (migration
            # 0014 on graph_nodes.template_id, plus graph_edges
            # source_node_id / target_node_id):
            #   1. Edges that touch any dependent node (otherwise step 3
            #      would fail with FK on source/target).
            #   2. Edges whose own template_id is this template.
            #   3. The dependent nodes themselves.
            #   4. The template (now has no incoming refs).
            dep_node_ids = list(
                self.session.exec(
                    select(GraphNode.id).where(
                        GraphNode.database_name == self.database_name,
                        GraphNode.template_id == template_id,
                    )
                ).all()
            )
            if dep_node_ids:
                self.session.exec(
                    sa_delete(GraphEdge).where(
                        GraphEdge.database_name == self.database_name,
                        or_(
                            GraphEdge.source_node_id.in_(dep_node_ids),
                            GraphEdge.target_node_id.in_(dep_node_ids),
                        ),
                    )
                )
            self.session.exec(
                sa_delete(GraphEdge).where(
                    GraphEdge.database_name == self.database_name,
                    GraphEdge.template_id == template_id,
                )
            )
            if dep_node_ids:
                self.session.exec(
                    sa_delete(GraphNode).where(
                        GraphNode.database_name == self.database_name,
                        GraphNode.template_id == template_id,
                    )
                )
            logger.info(
                "template_force_delete_cascade",
                template_id=template_id,
                nodes_deleted=nodes_count,
                edges_deleted=edges_count,
            )

        self.session.delete(db_template)
        self.session.maybe_commit()

        logger.info("template_deleted", template_id=template_id, forced=force)
        return True

    def ensure_default_templates_exist(
        self, default_templates_provider: Callable[[], list[dict[str, Any]]] | None = None
    ) -> int:
        """Ensure default templates exist, creating them if missing."""
        if default_templates_provider is None:
            logger.debug("ensure_default_templates_exist called with no provider, skipping")
            return 0

        existing_templates = self.list_templates()
        existing_ids = {t.id for t in existing_templates}

        all_templates = default_templates_provider()
        missing_count = 0

        for template_data in all_templates:
            if template_data["id"] not in existing_ids:
                properties = []
                for prop in template_data["properties"]:
                    if isinstance(prop, PropertyDefinition):
                        properties.append(prop)
                    elif isinstance(prop, dict):
                        properties.append(PropertyDefinition(**prop))
                    else:
                        properties.append(PropertyDefinition(**dict(prop)))

                template_create = TemplateCreate(
                    name=template_data["name"],
                    description=template_data.get("description"),
                    template_type=template_data["template_type"],
                    properties=properties,
                    icon=template_data.get("icon"),
                    color=template_data.get("color"),
                )
                self.create_template(
                    template_create,
                    custom_id=template_data["id"],
                    is_system=template_data.get("is_system", True),
                )
                missing_count += 1

        if missing_count > 0:
            logger.info("default_templates_created", template_count=missing_count)

        return missing_count

    def _db_template_to_model(self, db_template: GraphTemplate) -> Template:
        """Convert database template to Pydantic model."""
        properties = (
            [PropertyDefinition(**p) for p in db_template.properties]
            if db_template.properties
            else []
        )

        return Template(
            id=db_template.id,
            name=db_template.name,
            template_type=db_template.template_type,
            description=db_template.description,
            properties=properties,
            is_system=db_template.is_system,
            icon=db_template.icon,
            color=db_template.color,
            created_at=db_template.created_at,
            updated_at=db_template.updated_at,
            source_id=db_template.source_id,
        )
