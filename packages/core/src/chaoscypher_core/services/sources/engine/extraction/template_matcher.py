# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template matching and validation for extracted entities.

Provides edge template suggestion generation from extracted relationships,
using domain-specific descriptions when available.
"""

from typing import Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)


logger = structlog.get_logger(__name__)


def suggest_edge_templates(
    relationships: list[dict[str, Any]],
    *,
    detected_domain: str | None = None,
    get_domain_edge_templates: Any,
) -> list[dict[str, Any]]:
    """Generate edge template suggestions from extracted relationships.

    Analyzes relationship types and suggests edge templates for types
    that don't have existing templates.  Uses domain-specific descriptions
    when available.

    Args:
        relationships: List of extracted relationships with ``'type'`` field.
        detected_domain: Name of detected domain for looking up edge descriptions.
        get_domain_edge_templates: Callable that accepts a domain name and
            returns a list of edge template dicts (or None).

    Returns:
        List of suggested edge templates with name, description, reason, count.

    """
    if not relationships:
        return []

    try:
        # Get domain edge templates for descriptions only
        domain_edge_templates = None
        if detected_domain:
            domain_edge_templates = get_domain_edge_templates(detected_domain)

        return TemplateExtractor.generate_suggestions_from_relationships(
            relationships=relationships,
            domain_templates=domain_edge_templates,
        )

    except Exception as e:
        logger.exception(
            "edge_template_suggestions_failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return []
