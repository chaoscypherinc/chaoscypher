# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Visual Defaults.

Canonical location for template visual helpers (icons and colors).
Relocated from `chaoscypher_core.services.sources.engine.extraction.utils.template_visuals`
because the old location caused adapters/sqlite/engine.py to import from the
services layer, inverting hexagonal architecture direction. This package
(chaoscypher_core.templates) is neutral and may be imported by both services
and adapters.

CONTRACT: This module must NOT import from `chaoscypher_core.services.*` or
`chaoscypher_core.adapters.*`.

Provides default icon and color suggestions for template types using a hybrid
approach: keyword matching first, then embedding similarity for novel types.
"""

from typing import Any

import structlog


logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_EDGE_COLOR",
    "DEFAULT_EDGE_ICON",
    "DEFAULT_NODE_COLOR",
    "DEFAULT_NODE_ICON",
    "EDGE_VISUAL_DEFAULTS",
    "NODE_VISUAL_DEFAULTS",
    "resolve_edge_visuals",
    "resolve_node_visuals",
    "resolve_node_visuals_with_embedding",
]

# Universal fallback icons for unmatched types — ensures everything has an icon
DEFAULT_NODE_ICON = "Label"
DEFAULT_NODE_COLOR = "#78909c"
DEFAULT_EDGE_ICON = "Link"
DEFAULT_EDGE_COLOR = "#78909c"


# Mapping of common entity types to their visual defaults.
# Each entry has: icon (MUI icon name), color (hex), keywords (aliases for matching).
NODE_VISUAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "Person": {
        "icon": "Person",
        "color": "#5c6bc0",
        "keywords": [
            "person",
            "individual",
            "human",
            "character",
            "figure",
            "actor",
            "author",
            "speaker",
            "protagonist",
            "antagonist",
        ],
    },
    "Organization": {
        "icon": "Business",
        "color": "#26a69a",
        "keywords": [
            "organization",
            "company",
            "corporation",
            "institution",
            "firm",
            "agency",
            "enterprise",
            "association",
            "group",
            "team",
        ],
    },
    "Place": {
        "icon": "Place",
        "color": "#ef5350",
        "keywords": [
            "place",
            "location",
            "city",
            "country",
            "region",
            "area",
            "territory",
            "site",
            "venue",
            "address",
            "geography",
        ],
    },
    "Event": {
        "icon": "Event",
        "color": "#ffa726",
        "keywords": [
            "event",
            "incident",
            "occurrence",
            "happening",
            "meeting",
            "conference",
            "battle",
            "war",
            "ceremony",
            "election",
        ],
    },
    "Document": {
        "icon": "Article",
        "color": "#42a5f5",
        "keywords": [
            "document",
            "article",
            "paper",
            "report",
            "publication",
            "book",
            "manuscript",
            "letter",
            "memo",
            "text",
        ],
    },
    "Concept": {
        "icon": "Lightbulb",
        "color": "#ab47bc",
        "keywords": [
            "concept",
            "idea",
            "theory",
            "principle",
            "notion",
            "philosophy",
            "doctrine",
            "ideology",
            "paradigm",
            "theme",
        ],
    },
    "Technology": {
        "icon": "Memory",
        "color": "#78909c",
        "keywords": [
            "technology",
            "software",
            "hardware",
            "system",
            "platform",
            "tool",
            "framework",
            "library",
            "application",
            "device",
        ],
    },
    "Product": {
        "icon": "Inventory2",
        "color": "#8d6e63",
        "keywords": [
            "product",
            "item",
            "goods",
            "merchandise",
            "commodity",
            "service",
            "offering",
        ],
    },
    "Law": {
        "icon": "Gavel",
        "color": "#7e57c2",
        "keywords": [
            "law",
            "regulation",
            "statute",
            "legislation",
            "act",
            "ordinance",
            "rule",
            "mandate",
            "decree",
            "policy",
        ],
    },
    "Disease": {
        "icon": "Coronavirus",
        "color": "#e53935",
        "keywords": [
            "disease",
            "illness",
            "condition",
            "disorder",
            "syndrome",
            "infection",
            "pathology",
            "ailment",
        ],
    },
    "Drug": {
        "icon": "Medication",
        "color": "#00897b",
        "keywords": [
            "drug",
            "medication",
            "medicine",
            "pharmaceutical",
            "treatment",
            "therapy",
            "remedy",
            "vaccine",
        ],
    },
    "Species": {
        "icon": "Pets",
        "color": "#4caf50",
        "keywords": [
            "species",
            "animal",
            "organism",
            "creature",
            "plant",
            "microbe",
            "bacteria",
            "virus",
        ],
    },
    "Date": {
        "icon": "CalendarMonth",
        "color": "#ff7043",
        "keywords": [
            "date",
            "time",
            "period",
            "era",
            "epoch",
            "year",
            "decade",
            "century",
        ],
    },
    "Money": {
        "icon": "AttachMoney",
        "color": "#66bb6a",
        "keywords": [
            "money",
            "currency",
            "fund",
            "budget",
            "price",
            "cost",
            "payment",
            "investment",
            "revenue",
        ],
    },
    "Class": {
        "icon": "Code",
        "color": "#29b6f6",
        "keywords": [
            "class",
            "interface",
            "type",
            "struct",
            "enum",
            "abstract",
            "mixin",
        ],
    },
    "Function": {
        "icon": "Functions",
        "color": "#26c6da",
        "keywords": [
            "function",
            "method",
            "procedure",
            "routine",
            "subroutine",
            "callback",
            "handler",
            "hook",
        ],
    },
    "Module": {
        "icon": "ViewModule",
        "color": "#5c6bc0",
        "keywords": [
            "module",
            "package",
            "library",
            "namespace",
            "crate",
            "bundle",
            "component",
        ],
    },
    "Endpoint": {
        "icon": "Api",
        "color": "#ec407a",
        "keywords": [
            "endpoint",
            "api",
            "route",
            "url",
            "uri",
            "webhook",
            "service",
        ],
    },
    "School": {
        "icon": "School",
        "color": "#5c6bc0",
        "keywords": [
            "school",
            "university",
            "college",
            "academy",
            "institute",
            "education",
        ],
    },
    "Science": {
        "icon": "Science",
        "color": "#00acc1",
        "keywords": [
            "science",
            "research",
            "study",
            "experiment",
            "hypothesis",
            "finding",
            "discovery",
        ],
    },
    "Security": {
        "icon": "Security",
        "color": "#f44336",
        "keywords": [
            "security",
            "vulnerability",
            "threat",
            "attack",
            "exploit",
            "malware",
            "breach",
        ],
    },
    "Database": {
        "icon": "Storage",
        "color": "#607d8b",
        "keywords": [
            "database",
            "table",
            "schema",
            "repository",
            "datastore",
            "collection",
        ],
    },
    "Network": {
        "icon": "Hub",
        "color": "#9575cd",
        "keywords": [
            "network",
            "server",
            "host",
            "node",
            "cluster",
            "infrastructure",
            "protocol",
        ],
    },
    "File": {
        "icon": "InsertDriveFile",
        "color": "#90a4ae",
        "keywords": [
            "file",
            "config",
            "configuration",
            "script",
            "template",
            "resource",
            "asset",
        ],
    },
    "Topic": {
        "icon": "Topic",
        "color": "#ba68c8",
        "keywords": [
            "topic",
            "subject",
            "category",
            "genre",
            "field",
            "domain",
            "discipline",
            "area",
        ],
    },
    "Quote": {
        "icon": "FormatQuote",
        "color": "#ffb74d",
        "keywords": [
            "quote",
            "passage",
            "excerpt",
            "citation",
            "statement",
            "saying",
            "proverb",
        ],
    },
    "Award": {
        "icon": "EmojiEvents",
        "color": "#ffd54f",
        "keywords": [
            "award",
            "prize",
            "medal",
            "honor",
            "achievement",
            "recognition",
            "title",
        ],
    },
    "Weapon": {
        "icon": "Shield",
        "color": "#795548",
        "keywords": [
            "weapon",
            "arms",
            "military",
            "defense",
            "ammunition",
        ],
    },
}

# Default visuals for common edge/relationship types.
EDGE_VISUAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "works_at": {
        "icon": "Work",
        "color": "#26a69a",
        "keywords": ["works_at", "employed_by", "works_for", "employment"],
    },
    "located_in": {
        "icon": "Place",
        "color": "#ef5350",
        "keywords": ["located_in", "based_in", "resides_in", "lives_in", "situated_in"],
    },
    "created_by": {
        "icon": "Create",
        "color": "#42a5f5",
        "keywords": ["created_by", "authored_by", "written_by", "developed_by", "built_by"],
    },
    "part_of": {
        "icon": "AccountTree",
        "color": "#ab47bc",
        "keywords": ["part_of", "member_of", "belongs_to", "component_of", "subset_of"],
    },
    "related_to": {
        "icon": "Link",
        "color": "#78909c",
        "keywords": ["related_to", "associated_with", "connected_to", "linked_to"],
    },
    "parent_of": {
        "icon": "FamilyRestroom",
        "color": "#5c6bc0",
        "keywords": ["parent_of", "father_of", "mother_of", "child_of"],
    },
    "knows": {
        "icon": "People",
        "color": "#66bb6a",
        "keywords": ["knows", "knows_of", "acquainted_with", "familiar_with"],
    },
    "owns": {
        "icon": "AccountBalance",
        "color": "#8d6e63",
        "keywords": ["owns", "possesses", "controls", "manages", "operates"],
    },
    "uses": {
        "icon": "Build",
        "color": "#ffa726",
        "keywords": ["uses", "utilizes", "employs", "applies", "leverages"],
    },
    "inherits_from": {
        "icon": "SubdirectoryArrowRight",
        "color": "#29b6f6",
        "keywords": ["inherits_from", "extends", "derives_from", "subclass_of"],
    },
    "imports": {
        "icon": "FileDownload",
        "color": "#26c6da",
        "keywords": ["imports", "includes", "requires", "depends_on"],
    },
    "references": {
        "icon": "Link",
        "color": "#7e57c2",
        "keywords": ["references", "cites", "mentions", "refers_to"],
    },
    "occurred_at": {
        "icon": "CalendarMonth",
        "color": "#ff7043",
        "keywords": ["occurred_at", "happened_at", "took_place", "dated"],
    },
    "treats": {
        "icon": "Healing",
        "color": "#00897b",
        "keywords": ["treats", "cures", "remedies", "alleviates", "prescribed_for"],
    },
    "causes": {
        "icon": "TrendingUp",
        "color": "#e53935",
        "keywords": ["causes", "leads_to", "results_in", "triggers", "produces"],
    },
    "participates_in": {
        "icon": "Groups",
        "color": "#ec407a",
        "keywords": ["participates_in", "involved_in", "engages_in", "takes_part_in"],
    },
    "funded_by": {
        "icon": "Payments",
        "color": "#66bb6a",
        "keywords": ["funded_by", "financed_by", "sponsored_by", "backed_by"],
    },
}

# Pre-built keyword-to-type lookup for fast matching
_NODE_KEYWORD_MAP: dict[str, str] = {}
for _type_name, _config in NODE_VISUAL_DEFAULTS.items():
    _NODE_KEYWORD_MAP[_type_name.lower()] = _type_name
    for _kw in _config["keywords"]:
        _NODE_KEYWORD_MAP[_kw] = _type_name

_EDGE_KEYWORD_MAP: dict[str, str] = {}
for _type_name, _config in EDGE_VISUAL_DEFAULTS.items():
    _EDGE_KEYWORD_MAP[_type_name.lower()] = _type_name
    for _kw in _config["keywords"]:
        _EDGE_KEYWORD_MAP[_kw] = _type_name


def resolve_node_visuals(entity_type: str) -> dict[str, str | None]:
    """Resolve icon and color for a node entity type.

    Uses keyword matching first, then falls back to the universal default
    (Label icon). Embedding-based matching is handled separately when available.

    Args:
        entity_type: The entity type name (e.g., "Person", "Protagonist")

    Returns:
        Dict with 'icon' and 'color' keys (always non-None)

    """
    type_lower = entity_type.lower().strip()

    # Direct keyword match
    matched_type = _NODE_KEYWORD_MAP.get(type_lower)
    if matched_type:
        config = NODE_VISUAL_DEFAULTS[matched_type]
        return {"icon": config["icon"], "color": config["color"]}

    return {"icon": DEFAULT_NODE_ICON, "color": DEFAULT_NODE_COLOR}


def resolve_edge_visuals(rel_type: str) -> dict[str, str | None]:
    """Resolve icon and color for an edge relationship type.

    Uses keyword matching first, then falls back to the universal default
    (Link icon).

    Args:
        rel_type: The relationship type name (e.g., "works_at", "employed_by")

    Returns:
        Dict with 'icon' and 'color' keys (always non-None)

    """
    type_lower = rel_type.lower().strip().replace(" ", "_")

    # Direct keyword match
    matched_type = _EDGE_KEYWORD_MAP.get(type_lower)
    if matched_type:
        config = EDGE_VISUAL_DEFAULTS[matched_type]
        return {"icon": config["icon"], "color": config["color"]}

    return {"icon": DEFAULT_EDGE_ICON, "color": DEFAULT_EDGE_COLOR}


async def resolve_node_visuals_with_embedding(
    entity_type: str,
    get_embedding: Any = None,
    threshold: float = 0.75,
) -> dict[str, str | None]:
    """Resolve visuals with embedding fallback for novel types.

    Tries keyword match first. If no match and an embedding function is provided,
    uses cosine similarity against the mapping table keys.

    Args:
        entity_type: The entity type name
        get_embedding: Async callable that returns an embedding vector for text
        threshold: Minimum cosine similarity to accept an embedding match

    Returns:
        Dict with 'icon' and 'color' keys (values may be None)

    """
    # Try keyword match first
    result = resolve_node_visuals(entity_type)
    if result["icon"] is not None:
        return result

    # Embedding fallback
    if get_embedding is None:
        return result

    try:
        import numpy as np

        query_embedding = await get_embedding(entity_type)
        if query_embedding is None:
            return result

        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return result

        best_score = 0.0
        best_type = None

        for type_name in NODE_VISUAL_DEFAULTS:
            type_embedding = await get_embedding(type_name)
            if type_embedding is None:
                continue

            type_vec = np.array(type_embedding, dtype=np.float32)
            type_norm = np.linalg.norm(type_vec)
            if type_norm == 0:
                continue

            similarity = float(np.dot(query_vec, type_vec) / (query_norm * type_norm))
            if similarity > best_score:
                best_score = similarity
                best_type = type_name

        if best_type and best_score >= threshold:
            config = NODE_VISUAL_DEFAULTS[best_type]
            logger.debug(
                "visual_embedding_match",
                entity_type=entity_type,
                matched=best_type,
                score=round(best_score, 3),
            )
            return {"icon": config["icon"], "color": config["color"]}

    except Exception:
        logger.debug("visual_embedding_fallback_failed", entity_type=entity_type, exc_info=True)

    return result
