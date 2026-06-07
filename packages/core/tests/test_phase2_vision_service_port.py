# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 2 Task F: VisionService types against LLMProviderPort."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


VISION_SERVICE_FILE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "vision"
    / "service.py"
)


def test_vision_service_has_no_module_level_adapter_import() -> None:
    tree = ast.parse(VISION_SERVICE_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("chaoscypher_core.adapters"), (
                f"module-level adapter import in vision/service.py: {node.module}"
            )


def test_vision_service_adapter_import_only_in_factory() -> None:
    source = VISION_SERVICE_FILE.read_text(encoding="utf-8")
    assert source.count("from chaoscypher_core.adapters.llm.provider import LLMProvider") == 1, (
        "expected exactly one allowlisted factory-local adapter import"
    )


def test_vision_service_constructor_typed_against_port() -> None:
    from chaoscypher_core.services.vision.service import VisionService

    sig = inspect.signature(VisionService.__init__)
    annotation = sig.parameters["llm_provider"].annotation
    # under `from __future__ import annotations`, the annotation is a str
    assert "LLMProviderPort" in str(annotation), (
        f"VisionService.__init__.llm_provider annotation is {annotation!r} — "
        f"expected LLMProviderPort"
    )


def test_create_vision_provider_returns_port_annotation() -> None:
    from chaoscypher_core.services.vision.service import create_vision_provider

    sig = inspect.signature(create_vision_provider)
    assert "LLMProviderPort" in str(sig.return_annotation)
