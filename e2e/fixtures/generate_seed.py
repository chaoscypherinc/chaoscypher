# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generate seed.ccx fixture for E2E tests.

Run: python e2e/fixtures/generate_seed.py

Creates a minimal CCX v2.0 package with:
- 2 node templates (person, organization)
- 2 edge templates (works_at, relates_to)
- 4 nodes (2 people, 2 orgs)
- 3 edges (employment + collaboration)

Manifest matches ``ExportManifest`` (extra="forbid") in
``chaoscypher_core.services.export.models.schemas``. If the manifest
schema evolves, this generator and the canonical model must drift in
lockstep — re-run after the change and commit the resulting fixture.
"""

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime


def sha256(data: bytes) -> str:
    """Compute SHA-256 hash as hex string."""
    return hashlib.sha256(data).hexdigest()


def sha512(data: bytes) -> str:
    """Compute SHA-512 hash as hex string."""
    return hashlib.sha512(data).hexdigest()


def build_templates() -> bytes:
    """Build templates.jsonld content."""
    data = {
        "templates": [
            {
                "id": "e2e_person",
                "name": "Person",
                "template_type": "node",
                "description": "A person entity for E2E testing",
                "properties": [
                    {
                        "name": "full_name",
                        "display_name": "Full Name",
                        "property_type": "string",
                        "required": True,
                        "description": "Person's full name",
                    },
                    {
                        "name": "role",
                        "display_name": "Role",
                        "property_type": "string",
                        "required": False,
                        "description": "Person's role or title",
                    },
                ],
                "is_system": False,
                "color": "#4A90D9",
                "icon": "user",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "e2e_organization",
                "name": "Organization",
                "template_type": "node",
                "description": "An organization entity for E2E testing",
                "properties": [
                    {
                        "name": "name",
                        "display_name": "Name",
                        "property_type": "string",
                        "required": True,
                        "description": "Organization name",
                    },
                    {
                        "name": "industry",
                        "display_name": "Industry",
                        "property_type": "string",
                        "required": False,
                        "description": "Industry sector",
                    },
                ],
                "is_system": False,
                "color": "#7B68EE",
                "icon": "building",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "e2e_works_at",
                "name": "Works At",
                "template_type": "edge",
                "description": "Employment relationship",
                "properties": [
                    {
                        "name": "since",
                        "display_name": "Since",
                        "property_type": "string",
                        "required": False,
                        "description": "Start year",
                    },
                ],
                "is_system": False,
                "color": "#2ECC71",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "id": "e2e_relates_to",
                "name": "Relates To",
                "template_type": "edge",
                "description": "Generic relationship",
                "properties": [],
                "is_system": False,
                "color": "#95A5A6",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        ]
    }
    return json.dumps(data, indent=2).encode("utf-8")


def build_knowledge() -> bytes:
    """Build knowledge.jsonld content."""
    now = "2026-01-01T00:00:00+00:00"
    data = {
        "nodes": [
            {
                "id": "e2e_node_alice",
                "label": "Alice Smith",
                "template_name": "Person",
                "properties": {"full_name": "Alice Smith", "role": "Engineer"},
                "position": {"x": 0, "y": 0},
                "source_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "e2e_node_bob",
                "label": "Bob Jones",
                "template_name": "Person",
                "properties": {"full_name": "Bob Jones", "role": "Designer"},
                "position": {"x": 200, "y": 0},
                "source_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "e2e_node_acme",
                "label": "Acme Corporation",
                "template_name": "Organization",
                "properties": {
                    "name": "Acme Corporation",
                    "industry": "Technology",
                },
                "position": {"x": 0, "y": 200},
                "source_id": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "e2e_node_techstartup",
                "label": "TechStartup Inc",
                "template_name": "Organization",
                "properties": {
                    "name": "TechStartup Inc",
                    "industry": "Software",
                },
                "position": {"x": 200, "y": 200},
                "source_id": None,
                "created_at": now,
                "updated_at": now,
            },
        ],
        "edges": [
            {
                "id": "e2e_edge_alice_acme",
                "template_name": "Works At",
                "source_node_id": "e2e_node_alice",
                "target_node_id": "e2e_node_acme",
                "label": "works at",
                "properties": {"since": "2023"},
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "e2e_edge_bob_techstartup",
                "template_name": "Works At",
                "source_node_id": "e2e_node_bob",
                "target_node_id": "e2e_node_techstartup",
                "label": "works at",
                "properties": {"since": "2024"},
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "e2e_edge_alice_bob",
                "template_name": "Relates To",
                "source_node_id": "e2e_node_alice",
                "target_node_id": "e2e_node_bob",
                "label": "collaborates with",
                "properties": {},
                "created_at": now,
                "updated_at": now,
            },
        ],
    }
    return json.dumps(data, indent=2).encode("utf-8")


def build_manifest(contents: list[dict]) -> bytes:
    """Build manifest.json content (CCX v2.0 schema)."""
    now = datetime.now(UTC).isoformat()
    data = {
        # GraphBreakdown fields (inherited by ExportManifest)
        "version": 2,
        "generated_at": now,
        "database_name": "e2e-seed",
        "title": "E2E seed fixture",
        "stats": {
            "total_nodes": 4,
            "total_edges": 3,
            "total_sources": 0,
        },
        "sources": [],
        # ExportManifest fields
        "ccx_version": "2.0",
        "package_type": ["knowledge", "templates"],
        "name": "e2e/seed-fixture",
        "package_version": "1.0.0",
        "author": "ChaosCypher E2E Tests",
        "license": "MIT",
        "description": "Seed fixture for E2E testing - 4 nodes, 3 edges, 4 templates",
        "tags": ["e2e", "test", "seed"],
        "created_at": now,
        "derived_from": {},
        "dependencies": {},
        "contents": contents,
        "template_stats": {"total": 4, "node_templates": 2, "edge_templates": 2},
        "knowledge_stats": {"total_nodes": 4, "total_edges": 3},
        "lens_stats": None,
        "workflow_stats": None,
        "source_stats": None,
        "generator": "ChaosCypher E2E Seed Generator@1.0.0",
    }
    return json.dumps(data, indent=2).encode("utf-8")


def main() -> None:
    """Generate seed.ccx fixture."""
    output_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(output_dir, "seed.ccx")

    templates_bytes = build_templates()
    knowledge_bytes = build_knowledge()

    contents = [
        {
            "type": "templates",
            "path": "templates.jsonld",
            "media_type": "application/ld+json",
            "file_size_bytes": len(templates_bytes),
            "checksum_sha256": sha256(templates_bytes),
            "checksum_sha512": sha512(templates_bytes),
        },
        {
            "type": "knowledge",
            "path": "knowledge.jsonld",
            "media_type": "application/ld+json",
            "file_size_bytes": len(knowledge_bytes),
            "checksum_sha256": sha256(knowledge_bytes),
            "checksum_sha512": sha512(knowledge_bytes),
        },
    ]

    manifest_bytes = build_manifest(contents)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("templates.jsonld", templates_bytes)
        zf.writestr("knowledge.jsonld", knowledge_bytes)

    size = os.path.getsize(output_path)
    print(f"Generated {output_path} ({size} bytes)")


if __name__ == "__main__":
    main()
