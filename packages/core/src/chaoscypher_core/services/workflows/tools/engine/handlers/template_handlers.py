# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Tool Handlers.

Handles template listing, creation, deletion, and search operations.

Extracted from tool_executor.py for SRP compliance.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import structlog

from chaoscypher_core.models import PropertyDefinition, TemplateCreate
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol

logger = structlog.get_logger(__name__)


class TemplateToolHandlers:
    """Handles all template-related tool operations."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol | None = None,
        embedding_callback: Callable[[str], Any] | None = None,
    ):
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph operations.
            search_repository: Repository for search operations (optional, for semantic search).
            embedding_callback: Async callback for generating embeddings (optional).

        """
        self.graph = graph_repository
        self.search = search_repository
        self._embedding_callback = embedding_callback

    @tool_handler("list_templates_failed")
    async def list_templates(self, template_type: str | None = None) -> dict:
        """List available templates."""
        templates = self.graph.list_templates(template_type=template_type)

        return {
            "success": True,
            "count": len(templates),
            "templates": [
                {
                    "id": template.id,
                    "name": template.name,
                    "template_type": template.template_type,
                    "description": template.description,
                    "properties": [
                        {
                            "name": prop.name,
                            "display_name": prop.display_name,
                            "property_type": prop.property_type.value,
                            "required": prop.required,
                        }
                        for prop in (template.properties or [])
                    ],
                }
                for template in templates
            ],
        }

    @tool_handler("create_template_failed")
    async def create_template(
        self,
        name: str,
        template_type: str,
        description: str = "",
        properties: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Create a new template."""
        if properties is None:
            properties = []

        # Convert property dicts to PropertyDefinition objects
        prop_defs = [PropertyDefinition(**prop) for prop in properties]

        template = self.graph.create_template(
            TemplateCreate(
                name=name,
                template_type=template_type,
                description=description,
                properties=prop_defs,
            )
        )

        return {
            "success": True,
            "message": f"Created template: {name}",
            "template_id": template.id,
        }

    @tool_handler("delete_template_failed")
    async def delete_template(self, template_id: str) -> dict:
        """Delete a template."""
        success = self.graph.delete_template(template_id)
        if success:
            return {"success": True, "message": f"Deleted template: {template_id}"}
        return {"success": False, "error": "Failed to delete template"}

    @tool_handler("search_templates_failed")
    async def search_templates(
        self,
        query: str,
        template_type: str | None = None,
        limit: int = 5,
    ) -> dict:
        """Search templates by semantic similarity with keyword fallback.

        Uses embedding search to find templates matching the query concept,
        enhanced with keyword matching to catch related templates that may
        have lower embedding similarity (e.g., "Character" when searching "people").

        Args:
            query: Concept or description to search for
            template_type: Filter by template type ("node" or "edge")
            limit: Maximum number of results

        Returns:
            Dict with search results and usage hints

        """
        # Check if search repository and embedding callback are available
        if not self.search or not self._embedding_callback:
            # Fall back to keyword matching on template names/descriptions
            return await self._keyword_search_templates(query, template_type, limit)

        # Generate query embedding — callback may return dict or EmbedResult
        result = await self._embedding_callback(query)
        if isinstance(result, dict):
            query_embedding = result.get("embedding", [])
        elif hasattr(result, "embedding"):
            query_embedding = result.embedding
        else:
            query_embedding = result

        if not query_embedding:
            return await self._keyword_search_templates(query, template_type, limit)

        # Search templates by embedding similarity (lower threshold to catch more)
        search_results = self.search.template_semantic_search(
            query_embedding=query_embedding,
            k=limit * 3,  # Get more to combine with keyword matches
            min_similarity=0.4,  # Lower threshold to include more candidates
        )

        # Get template details and filter by type
        templates = []
        seen_ids = set()
        for template_id, score in search_results:
            template = self.graph.get_template(template_id)
            if template:
                if template_type and template.template_type != template_type:
                    continue
                templates.append(
                    {
                        "id": template.id,
                        "name": template.name,
                        "template_type": template.template_type,
                        "description": template.description,
                        "similarity_score": round(score, 3),
                    }
                )
                seen_ids.add(template.id)
            if len(templates) >= limit:
                break

        # Also do keyword search to catch templates with matching names
        # This helps find "Character" when searching for "character" or related terms
        keyword_result = await self._keyword_search_templates(query, template_type, limit=limit)
        if keyword_result.get("success"):
            for kt in keyword_result.get("templates", []):
                if kt["id"] not in seen_ids:
                    # Add keyword matches with their score
                    templates.append(kt)
                    seen_ids.add(kt["id"])

        # Get entity counts for all found templates
        template_ids_for_counts = [str(t["id"]) for t in templates if isinstance(t["id"], str)]
        usage_counts = {}
        if template_ids_for_counts and hasattr(self.graph, "get_template_usage_counts"):
            usage_counts = self.graph.get_template_usage_counts(template_ids_for_counts)

        # Add entity counts to each template
        for t in templates:
            t_id = str(t["id"]) if isinstance(t["id"], str) else ""
            counts = usage_counts.get(t_id, {"nodes": 0, "edges": 0})
            t["entity_count"] = counts.get("nodes", 0) + counts.get("edges", 0)

        # Sort by entity_count (prefer templates with data), then by similarity score
        templates.sort(key=lambda x: (x["entity_count"], x["similarity_score"]), reverse=True)
        templates = templates[:limit]

        return {
            "success": True,
            "query": query,
            "count": len(templates),
            "templates": templates,
            "hint": "Use template IDs with entity_count > 0 for best results. Templates with 0 entities have no data.",
        }

    # Common synonym groups for template search
    TEMPLATE_SYNONYMS: ClassVar[dict[str, list[str]]] = {
        "people": ["person", "character", "individual", "human", "figure"],
        "person": ["people", "character", "individual", "human", "figure"],
        "character": ["person", "people", "individual", "figure"],
        "place": ["location", "setting", "venue", "site", "area"],
        "location": ["place", "setting", "venue", "site", "area"],
        "thing": ["object", "item", "entity", "artifact"],
        "object": ["thing", "item", "entity", "artifact"],
        "event": ["occurrence", "incident", "happening"],
        "organization": ["company", "group", "institution", "entity"],
        "company": ["organization", "business", "corporation", "firm"],
    }

    async def _keyword_search_templates(
        self,
        query: str,
        template_type: str | None = None,
        limit: int = 5,
    ) -> dict:
        """Keyword search for templates with synonym expansion.

        Args:
            query: Search query
            template_type: Filter by type
            limit: Maximum results

        Returns:
            Dict with matching templates

        """
        templates = self.graph.list_templates(template_type=template_type)
        query_lower = query.lower()

        # Build search terms including synonyms
        search_terms = {query_lower}
        if query_lower in self.TEMPLATE_SYNONYMS:
            search_terms.update(self.TEMPLATE_SYNONYMS[query_lower])

        # Keyword matching with synonyms
        matches = []
        for template in templates:
            template_name_lower = template.name.lower()
            template_desc_lower = (template.description or "").lower()

            # Check each search term
            best_score = 0.0
            for term in search_terms:
                if term in template_name_lower:
                    # Exact term in name gets high score
                    best_score = max(best_score, 1.0 if term == query_lower else 0.85)
                elif term in template_desc_lower:
                    # Term in description gets lower score
                    best_score = max(best_score, 0.7 if term == query_lower else 0.6)

            if best_score > 0:
                matches.append(
                    {
                        "id": template.id,
                        "name": template.name,
                        "template_type": template.template_type,
                        "description": template.description,
                        "similarity_score": best_score,
                    }
                )

        # Get entity counts for matched templates
        template_ids_for_counts = [str(m["id"]) for m in matches if isinstance(m["id"], str)]
        usage_counts = {}
        if template_ids_for_counts and hasattr(self.graph, "get_template_usage_counts"):
            usage_counts = self.graph.get_template_usage_counts(template_ids_for_counts)

        # Add entity counts to each template
        for m in matches:
            m_id = str(m["id"]) if isinstance(m["id"], str) else ""
            counts = usage_counts.get(m_id, {"nodes": 0, "edges": 0})
            m["entity_count"] = counts.get("nodes", 0) + counts.get("edges", 0)

        # Sort by entity_count (prefer templates with data), then by similarity score
        matches.sort(key=lambda x: (x["entity_count"], x["similarity_score"]), reverse=True)
        matches = matches[:limit]

        return {
            "success": True,
            "query": query,
            "count": len(matches),
            "templates": matches,
            "hint": "Use template IDs with entity_count > 0 for best results. Templates with 0 entities have no data.",
            "search_method": "keyword_fallback",
        }
