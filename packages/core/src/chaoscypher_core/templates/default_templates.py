# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Default Templates for Knowledge Engine.

This file defines all default templates that are created in new databases.
Edit this file to add, remove, or modify default templates.
"""

from typing import Any

from chaoscypher_core.models import PropertyDefinition, PropertyType


# ============================================================================
# Default Node Templates
# ============================================================================

DEFAULT_NODE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system_template_note",
        "name": "Note",
        "description": "General note or observation",
        "template_type": "node",
        "is_system": True,
        "icon": "Description",
        "color": "#90a4ae",
        "properties": [
            PropertyDefinition(
                name="content",
                display_name="Content",
                property_type=PropertyType.TEXT,
                required=True,
            ),
            PropertyDefinition(
                name="tags",
                display_name="Tags",
                property_type=PropertyType.STRING,
                description="Comma-separated tags",
            ),
        ],
    },
    {
        "id": "system_template_item",
        "name": "Item",
        "description": "It represents a core subject—a person, a place, a specific idea.",
        "template_type": "node",
        "is_system": True,
        "icon": "Category",
        "color": "#78909c",
        "properties": [
            PropertyDefinition(
                name="definition",
                display_name="Definition",
                property_type=PropertyType.TEXT,
                required=True,
            ),
            PropertyDefinition(
                name="domain",
                display_name="Domain",
                property_type=PropertyType.STRING,
                description="Domain or field this concept belongs to",
            ),
        ],
    },
    {
        "id": "system_template_person",
        "name": "Person",
        "description": "An individual human being",
        "template_type": "node",
        "is_system": True,
        "icon": "Person",
        "color": "#5c6bc0",
        "properties": [
            PropertyDefinition(
                name="full_name",
                display_name="Full Name",
                property_type=PropertyType.STRING,
                required=True,
            ),
            PropertyDefinition(
                name="biography",
                display_name="Biography",
                property_type=PropertyType.TEXT,
                description="Biographical information",
            ),
            PropertyDefinition(
                name="birth_date",
                display_name="Birth Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="occupation",
                display_name="Occupation",
                property_type=PropertyType.STRING,
            ),
        ],
    },
    {
        "id": "system_template_organization",
        "name": "Organization",
        "description": "Companies, institutions, groups, or other collective entities",
        "template_type": "node",
        "is_system": True,
        "icon": "Business",
        "color": "#26a69a",
        "properties": [
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
            ),
            PropertyDefinition(
                name="founded",
                display_name="Founded",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="industry",
                display_name="Industry",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="headquarters",
                display_name="Headquarters",
                property_type=PropertyType.STRING,
            ),
        ],
    },
    {
        "id": "system_template_concept",
        "name": "Concept",
        "description": "Ideas, theories, principles, or abstract notions",
        "template_type": "node",
        "is_system": True,
        "icon": "Lightbulb",
        "color": "#ab47bc",
        "properties": [
            PropertyDefinition(
                name="definition",
                display_name="Definition",
                property_type=PropertyType.TEXT,
                required=True,
            ),
            PropertyDefinition(
                name="domain",
                display_name="Domain",
                property_type=PropertyType.STRING,
                description="Field or domain this concept belongs to",
            ),
        ],
    },
    {
        "id": "system_template_event",
        "name": "Event",
        "description": "Historical events, occurrences, or milestones",
        "template_type": "node",
        "is_system": True,
        "icon": "Event",
        "color": "#ffa726",
        "properties": [
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
            ),
            PropertyDefinition(
                name="date",
                display_name="Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="location",
                display_name="Location",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="significance",
                display_name="Significance",
                property_type=PropertyType.TEXT,
            ),
        ],
    },
    {
        "id": "system_template_location",
        "name": "Location",
        "description": "Places, geographical areas, or addresses",
        "template_type": "node",
        "is_system": True,
        "icon": "Place",
        "color": "#ef5350",
        "properties": [
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
            ),
            PropertyDefinition(
                name="address",
                display_name="Address",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="coordinates",
                display_name="Coordinates",
                property_type=PropertyType.STRING,
                description="Latitude, Longitude",
            ),
            PropertyDefinition(
                name="region",
                display_name="Region/Country",
                property_type=PropertyType.STRING,
            ),
        ],
    },
    {
        "id": "system_template_document",
        "name": "Document",
        "description": "Documents, articles, books, or other written works",
        "template_type": "node",
        "is_system": True,
        "icon": "Article",
        "color": "#42a5f5",
        "properties": [
            PropertyDefinition(
                name="summary",
                display_name="Summary",
                property_type=PropertyType.TEXT,
            ),
            PropertyDefinition(
                name="author",
                display_name="Author",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="publication_date",
                display_name="Publication Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="source_url",
                display_name="Source URL",
                property_type=PropertyType.URL,
            ),
        ],
    },
    {
        "id": "system_template_topic",
        "name": "Topic",
        "description": "Subject areas, themes, or domains of knowledge",
        "template_type": "node",
        "is_system": True,
        "icon": "Topic",
        "color": "#ba68c8",
        "properties": [
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
            ),
            PropertyDefinition(
                name="keywords",
                display_name="Keywords",
                property_type=PropertyType.STRING,
                description="Comma-separated keywords",
            ),
        ],
    },
]


# ============================================================================
# Default Edge Templates
# ============================================================================

DEFAULT_EDGE_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system_template_link",
        "name": "link",
        "description": "Generic relationship between items",
        "template_type": "edge",
        "is_system": True,
        "icon": "Link",
        "color": "#78909c",
        "properties": [
            PropertyDefinition(
                name="relationship_type",
                display_name="Relationship Type",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
            ),
        ],
    },
    {
        "id": "system_edge_works_at",
        "name": "works_at",
        "description": "Person works at an organization",
        "template_type": "edge",
        "is_system": True,
        "icon": "Work",
        "color": "#26a69a",
        "properties": [
            PropertyDefinition(
                name="position",
                display_name="Position/Role",
                property_type=PropertyType.STRING,
                description="Job title or role",
            ),
            PropertyDefinition(
                name="start_date",
                display_name="Start Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="end_date",
                display_name="End Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_located_in",
        "name": "located_in",
        "description": "Entity is located in a place",
        "template_type": "edge",
        "is_system": True,
        "icon": "Place",
        "color": "#ef5350",
        "properties": [
            PropertyDefinition(
                name="address",
                display_name="Address",
                property_type=PropertyType.STRING,
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_related_to",
        "name": "related_to",
        "description": "Generic relationship between entities",
        "template_type": "edge",
        "is_system": True,
        "icon": "Link",
        "color": "#9575cd",
        "properties": [
            PropertyDefinition(
                name="relationship_type",
                display_name="Relationship Type",
                property_type=PropertyType.STRING,
                description="Type of relationship (e.g., colleague, friend, competitor)",
            ),
            PropertyDefinition(
                name="strength",
                display_name="Relationship Strength",
                property_type=PropertyType.ENUM,
                enum_values=["weak", "moderate", "strong"],
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_part_of",
        "name": "part_of",
        "description": "Component is part of a larger whole",
        "template_type": "edge",
        "is_system": True,
        "icon": "AccountTree",
        "color": "#ab47bc",
        "properties": [
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            )
        ],
    },
    {
        "id": "system_edge_created_by",
        "name": "created_by",
        "description": "Work or artifact created by a person or organization",
        "template_type": "edge",
        "is_system": True,
        "icon": "Create",
        "color": "#42a5f5",
        "properties": [
            PropertyDefinition(
                name="creation_date",
                display_name="Creation Date",
                property_type=PropertyType.DATE,
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_influences",
        "name": "influences",
        "description": "Entity influences or affects another entity",
        "template_type": "edge",
        "is_system": True,
        "icon": "TrendingUp",
        "color": "#ffa726",
        "properties": [
            PropertyDefinition(
                name="influence_type",
                display_name="Type of Influence",
                property_type=PropertyType.STRING,
                description="How this entity influences the other (e.g., inspires, affects, shapes)",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_similar_to",
        "name": "similar_to",
        "description": "Entities share similarities or are comparable",
        "template_type": "edge",
        "is_system": True,
        "icon": "CompareArrows",
        "color": "#66bb6a",
        "properties": [
            PropertyDefinition(
                name="similarity_type",
                display_name="Type of Similarity",
                property_type=PropertyType.STRING,
                description="What makes them similar",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_derived_from",
        "name": "derived_from",
        "description": "Concept or entity derived from another",
        "template_type": "edge",
        "is_system": True,
        "icon": "SubdirectoryArrowRight",
        "color": "#29b6f6",
        "properties": [
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            )
        ],
    },
    {
        "id": "system_edge_mentions",
        "name": "mentions",
        "description": "Document or text mentions an entity",
        "template_type": "edge",
        "is_system": True,
        "icon": "FormatQuote",
        "color": "#ffb74d",
        "properties": [
            PropertyDefinition(
                name="context",
                display_name="Context",
                property_type=PropertyType.TEXT,
                description="Context of the mention",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_collaborates_with",
        "name": "collaborates_with",
        "description": "Person collaborates with another person",
        "template_type": "edge",
        "is_system": True,
        "icon": "Handshake",
        "color": "#8d6e63",
        "properties": [
            PropertyDefinition(
                name="collaboration_type",
                display_name="Type of Collaboration",
                property_type=PropertyType.STRING,
                description="Nature of the collaboration",
            ),
            PropertyDefinition(
                name="project",
                display_name="Project/Context",
                property_type=PropertyType.STRING,
                description="Project or context of collaboration",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_instance_of",
        "name": "instance_of",
        "description": "This item is an example of a more general concept",
        "template_type": "edge",
        "is_system": True,
        "icon": "Label",
        "color": "#7e57c2",
        "properties": [
            PropertyDefinition(
                name="category",
                display_name="Category",
                property_type=PropertyType.STRING,
                description="The general category or concept",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
    {
        "id": "system_edge_contains",
        "name": "contains",
        "description": "This item contains or possesses another item",
        "template_type": "edge",
        "is_system": True,
        "icon": "Inbox",
        "color": "#00897b",
        "properties": [
            PropertyDefinition(
                name="containment_type",
                display_name="Containment Type",
                property_type=PropertyType.STRING,
                description="Type of containment (e.g., physical, logical, organizational)",
            ),
            PropertyDefinition(
                name="justification",
                display_name="AI Justification",
                property_type=PropertyType.TEXT,
                description="Source text that indicates this relationship",
            ),
        ],
    },
]


# ============================================================================
# Default Lens Templates (for interpretation rules)
# ============================================================================

DEFAULT_LENS_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system_lens",
        "name": "Lens",
        "description": "A lens for interpreting and transforming knowledge",
        "template_type": "node",
        "is_system": True,
        "icon": "Visibility",
        "color": "#00acc1",
        "properties": [
            PropertyDefinition(
                name="lens_name",
                display_name="Lens Name",
                property_type=PropertyType.STRING,
                required=True,
                description="Name of this lens",
            ),
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
                required=True,
                description="What this lens does",
            ),
            PropertyDefinition(
                name="input_template",
                display_name="Input Template",
                property_type=PropertyType.STRING,
                description="Template ID this lens operates on",
            ),
            PropertyDefinition(
                name="output_template",
                display_name="Output Template",
                property_type=PropertyType.STRING,
                description="Template ID this lens produces",
            ),
            PropertyDefinition(
                name="transformation_rules",
                display_name="Transformation Rules",
                property_type=PropertyType.JSON,
                description="JSON rules for transforming data",
            ),
        ],
    }
]


# ============================================================================
# Default Workflow Templates (for executable processes)
# ============================================================================

DEFAULT_WORKFLOW_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "system_workflow",
        "name": "Workflow",
        "description": "A workflow defining an executable process",
        "template_type": "node",
        "is_system": True,
        "icon": "AccountTree",
        "color": "#607d8b",
        "properties": [
            PropertyDefinition(
                name="workflow_name",
                display_name="Workflow Name",
                property_type=PropertyType.STRING,
                required=True,
                description="Name of this workflow",
            ),
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
                description="What this workflow does",
            ),
            PropertyDefinition(
                name="inputs",
                display_name="Inputs (JSON Schema)",
                property_type=PropertyType.JSON,
                description="JSON Schema defining expected inputs",
            ),
            PropertyDefinition(
                name="outputs",
                display_name="Outputs (JSON Schema)",
                property_type=PropertyType.JSON,
                description="JSON Schema defining expected outputs",
            ),
            PropertyDefinition(
                name="enabled",
                display_name="Enabled",
                property_type=PropertyType.BOOLEAN,
                default_value=True,
                description="Whether this workflow is active",
            ),
            PropertyDefinition(
                name="is_system",
                display_name="System Workflow",
                property_type=PropertyType.BOOLEAN,
                default_value=False,
                description="Whether this is a built-in system workflow (cannot be deleted or edited)",
            ),
        ],
    },
    {
        "id": "system_workflow_step",
        "name": "Workflow Step",
        "description": "A single step within a workflow",
        "template_type": "node",
        "is_system": True,
        "icon": "PlayArrow",
        "color": "#78909c",
        "properties": [
            PropertyDefinition(
                name="step_name",
                display_name="Step Name",
                property_type=PropertyType.STRING,
                required=True,
                description="Name of this step",
            ),
            PropertyDefinition(
                name="order",
                display_name="Order",
                property_type=PropertyType.INTEGER,
                required=True,
                description="Execution order of this step",
            ),
            PropertyDefinition(
                name="description",
                display_name="Description",
                property_type=PropertyType.TEXT,
                description="What this step does",
            ),
            PropertyDefinition(
                name="tool",
                display_name="Tool",
                property_type=PropertyType.STRING,
                required=True,
                description="Tool to execute (e.g., core:query, ai:summarize)",
            ),
            PropertyDefinition(
                name="parameters",
                display_name="Parameters",
                property_type=PropertyType.JSON,
                description="Parameters for tool execution (supports {{steps.1.output}})",
            ),
            PropertyDefinition(
                name="depends_on",
                display_name="Depends On",
                property_type=PropertyType.JSON,
                description="JSON array of step IDs this depends on",
            ),
            PropertyDefinition(
                name="workflow_id",
                display_name="Workflow ID",
                property_type=PropertyType.NODE_REFERENCE,
                required=True,
                description="Parent workflow this step belongs to",
                allowed_node_types=["system_workflow"],
            ),
        ],
    },
]


# ============================================================================
# Helper Functions
# ============================================================================


def get_all_default_templates() -> list[dict[str, Any]]:
    """Get all default templates (nodes + edges + lenses + workflows)."""
    return (
        DEFAULT_NODE_TEMPLATES
        + DEFAULT_EDGE_TEMPLATES
        + DEFAULT_LENS_TEMPLATES
        + DEFAULT_WORKFLOW_TEMPLATES
    )
