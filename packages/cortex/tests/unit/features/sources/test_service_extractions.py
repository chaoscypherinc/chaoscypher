# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task F: sources list+extraction business logic moved into SourceService."""

from __future__ import annotations

import ast
from pathlib import Path


SOURCES_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "chaoscypher_cortex" / "features" / "sources"
)
SOURCES_API = SOURCES_DIR / "api.py"
EXTRACTION_API = SOURCES_DIR / "extraction_api.py"
SOURCES_SERVICE = SOURCES_DIR / "service.py"


def _service_method_names() -> set[str]:
    tree = ast.parse(SOURCES_SERVICE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_service_defines_list_sources_enriched() -> None:
    assert "list_sources_enriched" in _service_method_names()


def test_service_defines_trigger_extraction() -> None:
    assert "trigger_extraction" in _service_method_names()


def test_list_sources_handler_no_inline_enrichment() -> None:
    """The ``GET /sources`` handler must delegate enrichment to the service."""
    tree = ast.parse(SOURCES_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_sources":
            dump = ast.unparse(node)
            for forbidden in (
                "add_duration_fields",
                "get_source_tags_batch",
                "build_domain_icon_map",
                "enrich_domain_icons",
            ):
                assert forbidden not in dump, (
                    f"list_sources handler still references {forbidden!r}; "
                    "move into SourceService.list_sources_enriched"
                )
            return
    msg = "list_sources handler not found"
    raise AssertionError(msg)


def test_trigger_extraction_handler_no_inline_logic() -> None:
    """The ``POST /sources/{id}/extraction`` handler must delegate to the service."""
    tree = ast.parse(EXTRACTION_API.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "trigger_extraction":
            dump = ast.unparse(node)
            for forbidden in (
                "queue_import_analysis",
                "get_provider_factory",
                "file_info",
                "OP_IMPORT_ANALYSIS",
            ):
                assert forbidden not in dump, (
                    f"trigger_extraction handler still contains {forbidden!r}; "
                    "move into SourceService.trigger_extraction"
                )
            return
    msg = "trigger_extraction handler not found"
    raise AssertionError(msg)
