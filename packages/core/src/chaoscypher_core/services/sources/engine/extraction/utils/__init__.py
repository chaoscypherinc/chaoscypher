# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction utility classes for chaoscypher-engine.

Utilities for entity extraction operations:
- AIEntityExtractor: Extract entities from text chunks using LLM (2-pass)
- EntityProcessor: Entity deduplication (exact + semantic) - from deduplication module
- Entity cleaner: Cleanup, validation functions
- Quality analyzer: Density stats
- Type inferencer: Domain detection and inference
- Template formatters: Format templates for LLM prompts
- Type normalizer: Fix entity types post-extraction
- Line parser: Parse line-based LLM output format (E|R|P| lines)
- Sentence splitter: Deterministic sentence splitting for evidence-gated extraction
- Evidence validator: Validate extraction output against sentence-level evidence
- Type rescue: Three-tier rescue system for entities with invalid types
"""

from chaoscypher_core.services.sources.engine.deduplication import EntityProcessor
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    apply_properties_to_entities,
    parse_index,
    validate_relationships,
)
from chaoscypher_core.services.sources.engine.extraction.utils.evidence_validator import (
    filter_entities_by_evidence,
    filter_relationships_by_evidence,
    validate_entity_evidence,
    validate_relationship_evidence,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    VALID_PRESETS,
    FilteringConfig,
    resolve_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_log import (
    FilteredItem,
    FilteringLog,
)
from chaoscypher_core.services.sources.engine.extraction.utils.line_parser import (
    parse_entity_line,
    parse_extraction_output,
    parse_property_line,
    parse_relationship_line,
    parse_rename_line,
    safe_float,
    unescape_field,
)
from chaoscypher_core.services.sources.engine.extraction.utils.post_extraction import (
    apply_domain_type_aliases,
    apply_structural_and_normalization,
    get_domain_structural_filters,
)
from chaoscypher_core.services.sources.engine.extraction.utils.quality_analyzer import (
    calculate_density_stats,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    format_numbered_sentences,
    get_referenced_sentences,
    parse_sent_ref,
    split_into_sentences,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_domain_edge_templates,
    format_domain_node_templates,
    template_name_to_snake_case,
)
from chaoscypher_core.services.sources.engine.extraction.utils.text_preparation import (
    prepare_text_for_extraction,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_inferencer import (
    detect_domain,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
    apply_type_aliases,
    filter_structural_entities,
    normalize_entity_types,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    rescue_invalid_entity_types,
)


__all__ = [
    "VALID_PRESETS",
    "AIEntityExtractor",
    "EntityProcessor",
    "FilteredItem",
    "FilteringConfig",
    "FilteringLog",
    "apply_domain_type_aliases",
    "apply_properties_to_entities",
    "apply_structural_and_normalization",
    "apply_type_aliases",
    "calculate_density_stats",
    "detect_domain",
    "filter_entities_by_evidence",
    "filter_relationships_by_evidence",
    "filter_structural_entities",
    "format_domain_edge_templates",
    "format_domain_node_templates",
    "format_numbered_sentences",
    "get_domain_structural_filters",
    "get_referenced_sentences",
    "normalize_entity_types",
    "parse_entity_line",
    "parse_extraction_output",
    "parse_index",
    "parse_property_line",
    "parse_relationship_line",
    "parse_rename_line",
    "parse_sent_ref",
    "prepare_text_for_extraction",
    "rescue_invalid_entity_types",
    "resolve_filtering_config",
    "safe_float",
    "split_into_sentences",
    "template_name_to_snake_case",
    "unescape_field",
    "validate_entity_evidence",
    "validate_relationship_evidence",
    "validate_relationships",
]
