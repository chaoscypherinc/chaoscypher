# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Task E: AIEntityExtractor consumes LLMProviderPort."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock


AI_ENTITIES_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "sources"
    / "engine"
    / "extraction"
    / "utils"
    / "ai_entities.py"
)


def test_ai_entities_no_module_level_adapter_import() -> None:
    tree = ast.parse(AI_ENTITIES_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("chaoscypher_core.adapters"), (
                f"module-level adapter import present: {node.module}"
            )


def test_ai_entities_adapter_import_only_in_lazy_fallback() -> None:
    source = AI_ENTITIES_FILE.read_text(encoding="utf-8")
    hits = source.count("from chaoscypher_core.adapters.llm import ProviderFactory")
    assert hits == 1, (
        f"expected one (allowlisted) ProviderFactory import in the lazy fallback; found {hits}"
    )


def test_ai_entities_accepts_injected_provider() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )

    fake_port = MagicMock()
    settings = MagicMock()
    extractor = AIEntityExtractor(settings, llm_provider=fake_port)
    assert extractor._llm_provider is fake_port
    assert extractor._get_llm_provider() is fake_port


def test_ai_entities_default_is_none_until_first_call() -> None:
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )

    settings = MagicMock()
    extractor = AIEntityExtractor(settings)
    assert extractor._llm_provider is None
