# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Analyzer Plugin System.

Provides domain-specific extraction guidance, templates, and normalization
rules for entity extraction. Domains are configured via JSON-LD files.

Domain Storage:
- plugins/: Built-in domains (shared across all databases)
- data/databases/{db_name}/domains/: Per-database custom domains

Each domain folder contains:
- domain.jsonld (or domain.json): Configuration file with all domain settings

Usage:
    from chaoscypher_core.services.sources.engine.extraction.domains import (
        get_domain_registry,
        DomainAnalyzer,
    )

    # Get registry for a specific database
    registry = get_domain_registry(settings, database_name="research")

    # Find best matching domain for content
    domain, confidence = registry.get_best_domain(
        text="Sample document text...",
        filename="document.pdf",
        metadata={"source": "upload"},
    )

    # Force a specific domain
    domain = registry.get_domain("technical")

    # Use domain for extraction
    guidance = domain.get_guidance()
    normalization_rules = domain.get_normalization_rules()

Adding a New Domain:
    1. Create folder: data/databases/{db_name}/domains/my_domain/
    2. Add domain.jsonld with name, detection, guidance, templates
    3. Restart application - domain auto-discovered for that database!
"""

from typing import Any

from chaoscypher_core.services.sources.engine.extraction.domains.base import (
    DomainAnalyzer,
)
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
    load_domain_config,
)
from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
    clear_domain_registry_cache,
    get_domain_registry,
)
from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
    DomainRegistry,
)


def create_domain_sample_text(
    content_items: list[str] | list[dict[str, Any]],
    content_key: str = "combined_content",
    max_sample_length: int = 12000,
    per_item_limit: int = 1500,
) -> str:
    """Create sample text for domain detection from content items.

    Samples from multiple locations for better domain detection:
    - Beginning: first 5 items (captures title, author, intro, framing)
    - Middle: 3 items from ~40% in (captures representative content)

    Sized for the per-plugin keyword detection so that domain-distinctive
    vocabulary lands in the sample. Bumped 2026-05-23 from (3 + 2, 8000)
    to (5 + 3, 12000) after the 38-fixture audit showed the actual
    pipeline samples were ~4000 chars (chunks are ~800 chars in practice)
    and correctly-detectable content was scoring 0.2-0.3 lower than dry-run
    predictions, pushing it below the registry's 1.0 absolute floor.

    This is the single source of truth for domain detection sampling.
    Use this function instead of implementing sampling logic inline.

    Args:
        content_items: Either list of strings (chunks) or list of dicts with content
        content_key: Key to access content in dict items (default: "combined_content")
        max_sample_length: Maximum total sample length (default: 12000 chars)
        per_item_limit: Maximum chars per item sample (default: 1500 chars)

    Returns:
        Sample text ready for domain detection

    Example:
        # For list of strings (chunks):
        sample = create_domain_sample_text(chunks)

        # For list of dicts (hierarchical groups):
        sample = create_domain_sample_text(groups, content_key="combined_content")

    """
    if not content_items:
        return ""

    # Helper to extract content from item (handles both str and dict)
    def get_content(item: str | dict[str, Any]) -> str:
        from typing import cast

        if isinstance(item, str):
            return item
        return cast("str", item.get(content_key, ""))

    sample_parts = []

    # Beginning: first 5 items (captures title, author, intro, framing)
    for item in content_items[:5]:
        content = get_content(item)
        sample_parts.append(content[:per_item_limit])

    # Middle: 3 items from ~40% into document for representative content.
    # Only fire when the doc is big enough that the middle window doesn't
    # overlap the beginning window (need >= 8 items so mid_idx >= 5).
    if len(content_items) >= 8:
        mid_idx = len(content_items) * 2 // 5  # ~40% in
        # Clamp to ensure no overlap with the first-5 prefix above.
        mid_idx = max(mid_idx, 5)
        for item in content_items[mid_idx : mid_idx + 3]:
            content = get_content(item)
            sample_parts.append(content[:per_item_limit])

    return "\n".join(sample_parts)[:max_sample_length]


__all__ = [
    # Configurable domain
    "ConfigurableDomain",
    # Protocol
    "DomainAnalyzer",
    # Registry
    "DomainRegistry",
    "clear_domain_registry_cache",
    # Helper function
    "create_domain_sample_text",
    # Factory
    "get_domain_registry",
    "load_domain_config",
]
