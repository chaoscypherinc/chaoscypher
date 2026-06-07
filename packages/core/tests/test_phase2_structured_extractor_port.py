# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Tasks G + I + H: StructuredExtractorPort, ToolExecutionContext typing, ai plugins."""

from __future__ import annotations

import ast
from pathlib import Path


PLUGINS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "workflows"
    / "tools"
    / "plugins"
)
CONTEXT_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "workflows"
    / "tools"
    / "engine"
    / "context.py"
)


# ---------------------------------------------------------------------------
# Task G — StructuredExtractorPort exists, is runtime_checkable
# ---------------------------------------------------------------------------


def test_structured_extractor_port_runtime_checkable() -> None:
    from chaoscypher_core.ports.structured_extraction import StructuredExtractorPort

    class Fake:
        async def extract_structured(self, text, json_schema, **kwargs):  # type: ignore[no-untyped-def]
            return {"_metadata": {"success": True}}

    assert isinstance(Fake(), StructuredExtractorPort)


def test_concrete_structured_extractor_satisfies_port() -> None:
    from unittest.mock import MagicMock

    from chaoscypher_core.adapters.llm.schema.extractor import StructuredExtractor
    from chaoscypher_core.ports.structured_extraction import StructuredExtractorPort

    instance = StructuredExtractor(provider_factory=MagicMock())
    assert isinstance(instance, StructuredExtractorPort)


# ---------------------------------------------------------------------------
# Task I — ToolExecutionContext has typed port fields
# ---------------------------------------------------------------------------


def test_context_has_embedding_provider_field() -> None:
    from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext

    assert "embedding_provider" in ToolExecutionContext.__dataclass_fields__


def test_context_has_structured_extractor_field() -> None:
    from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext

    assert "structured_extractor" in ToolExecutionContext.__dataclass_fields__


def test_context_embedding_field_uses_protocol_annotation() -> None:
    source = CONTEXT_FILE.read_text(encoding="utf-8")
    assert "embedding_provider: EmbeddingProviderProtocol | None" in source
    assert "structured_extractor: StructuredExtractorPort | None" in source


# ---------------------------------------------------------------------------
# Task H — plugins prefer port over adapter factory
# ---------------------------------------------------------------------------


def test_embedding_plugin_no_module_level_adapter_import() -> None:
    tree = ast.parse((PLUGINS_ROOT / "ai_generate_embedding_plugin.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("chaoscypher_core.adapters"), (
                f"module-level adapter import found: {node.module}"
            )


def test_extract_json_plugin_no_module_level_adapter_import() -> None:
    tree = ast.parse((PLUGINS_ROOT / "ai_extract_json_plugin.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("chaoscypher_core.adapters"), (
                f"module-level adapter import found: {node.module}"
            )


def test_embedding_plugin_reads_context_field() -> None:
    source = (PLUGINS_ROOT / "ai_generate_embedding_plugin.py").read_text(encoding="utf-8")
    assert "context.embedding_provider" in source


def test_extract_json_plugin_reads_context_field() -> None:
    source = (PLUGINS_ROOT / "ai_extract_json_plugin.py").read_text(encoding="utf-8")
    assert "context.structured_extractor" in source


def test_embedding_plugin_adapter_import_only_once_in_fallback() -> None:
    source = (PLUGINS_ROOT / "ai_generate_embedding_plugin.py").read_text(encoding="utf-8")
    assert (
        source.count("from chaoscypher_core.adapters.embedding import create_embedding_provider")
        == 1
    )


def test_extract_json_plugin_adapter_import_only_once_in_fallback() -> None:
    source = (PLUGINS_ROOT / "ai_extract_json_plugin.py").read_text(encoding="utf-8")
    assert (
        source.count(
            "from chaoscypher_core.adapters.llm import ProviderFactory, StructuredExtractor"
        )
        == 1
    )
