# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for EmbeddingProviderProtocol and EmbeddingHealthStatus.

EmbeddingProviderProtocol is the service-facing contract for embedding
generation. It is already narrow enough for direct service consumption
(Scenario A — no new EmbeddingPort added).

EmbeddingHealthStatus was previously defined in adapters/embedding/models,
creating a hex-arch inversion (port importing from its own adapter). It now
lives in ports/embedding as part of the port's vocabulary — analogous to
TaskType living in ports/llm. The Phase 1 shim re-export from
adapters/embedding/models has been removed; callers must import the class
from ports/embedding or the adapters/embedding barrel.
"""

import pytest

from chaoscypher_core.ports.embedding import EmbeddingHealthStatus, EmbeddingProviderProtocol


def test_embedding_health_status_fields() -> None:
    """EmbeddingHealthStatus is constructible with required fields and correct defaults."""
    status = EmbeddingHealthStatus(healthy=True, provider="ollama", model="nomic-embed-text")
    assert status.healthy is True
    assert status.provider == "ollama"
    assert status.model == "nomic-embed-text"
    assert status.dimensions == 0  # default
    assert status.message is None  # default
    assert status.response_time_ms is None  # default


def test_embedding_health_status_all_fields() -> None:
    """EmbeddingHealthStatus accepts all optional fields."""
    status = EmbeddingHealthStatus(
        healthy=False,
        provider="openai",
        model="text-embedding-3-small",
        dimensions=1536,
        message="Connection refused",
        response_time_ms=250,
    )
    assert status.healthy is False
    assert status.dimensions == 1536
    assert status.message == "Connection refused"
    assert status.response_time_ms == 250


def test_models_module_no_longer_exports_health_status() -> None:
    """adapters/embedding/models.py must not re-export EmbeddingHealthStatus."""
    from chaoscypher_core.adapters.embedding import models as adapter_models

    assert not hasattr(adapter_models, "EmbeddingHealthStatus"), (
        "adapters/embedding/models.py must not re-export EmbeddingHealthStatus — "
        "the canonical home is chaoscypher_core.ports.embedding."
    )
    assert "EmbeddingHealthStatus" not in adapter_models.__all__


def test_embedding_health_status_adapter_barrel_identity() -> None:
    """The adapters/embedding __init__ barrel re-exports the canonical port class."""
    from chaoscypher_core.adapters.embedding import (
        EmbeddingHealthStatus as EmbeddingHealthStatusBarrel,
    )
    from chaoscypher_core.ports.embedding import EmbeddingHealthStatus as EmbeddingHealthStatusPort

    assert EmbeddingHealthStatusBarrel is EmbeddingHealthStatusPort


def test_no_runtime_imports_of_health_status_from_models() -> None:
    """No source under packages/*/src imports EmbeddingHealthStatus from adapters/embedding/models."""
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[5]
    targets = [
        repo_root / "packages" / "core" / "src",
        repo_root / "packages" / "cortex" / "src",
        repo_root / "packages" / "cli" / "src",
        repo_root / "packages" / "neuron" / "src",
    ]
    bad: list[str] = []
    for root in targets:
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # fmt: skip
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "chaoscypher_core.adapters.embedding.models"
                    and any(alias.name == "EmbeddingHealthStatus" for alias in node.names)
                ):
                    bad.append(f"{path}:{node.lineno}")
    assert not bad, "Unexpected imports from the removed shim path:\n" + "\n".join(bad)


def test_embedding_provider_protocol_is_runtime_checkable() -> None:
    """EmbeddingProviderProtocol is @runtime_checkable so isinstance() works."""

    class DuckEmbedding:
        model_name: str = "duck-embed"

        @property
        def provider_type(self) -> str:
            return "duck"

        async def embed(self, text: str):  # type: ignore[override]
            return None

        async def batch_embed(self, texts: list[str], batch_size: int = 64):  # type: ignore[override]
            return None

        async def check_health(self):  # type: ignore[override]
            return None

    assert isinstance(DuckEmbedding(), EmbeddingProviderProtocol)


def test_object_missing_embed_does_not_satisfy_protocol() -> None:
    """An object missing ``embed`` is correctly rejected by the Protocol check."""

    class Incomplete:
        model_name: str = "x"

        @property
        def provider_type(self) -> str:
            return "x"

        async def batch_embed(self, texts: list[str], batch_size: int = 64):  # type: ignore[override]
            return None

        async def check_health(self):  # type: ignore[override]
            return None

    assert not isinstance(Incomplete(), EmbeddingProviderProtocol)


def test_ports_embedding_has_no_runtime_adapter_imports():
    """Ports must not import from adapters at runtime (TYPE_CHECKING blocks are fine).

    Catches the exact regression this task fixes — an adapter import re-appearing
    at module top-level. Uses AST to distinguish runtime-level imports from
    imports nested inside `if TYPE_CHECKING:` blocks.
    """
    import ast
    import inspect

    import chaoscypher_core.ports.embedding as port_mod

    tree = ast.parse(inspect.getsource(port_mod))
    offending: list[str] = []
    for node in tree.body:  # top-level only — doesn't descend into TYPE_CHECKING If
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (
                node.module == "chaoscypher_core.adapters"
                or node.module.startswith("chaoscypher_core.adapters.")
            )
        ):
            offending.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            offending.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name == "chaoscypher_core.adapters"
                or alias.name.startswith("chaoscypher_core.adapters.")
            )

    assert not offending, (
        "ports/embedding.py has runtime imports from chaoscypher_core.adapters:\n  "
        + "\n  ".join(offending)
    )


def test_embedding_health_status_rejects_extra_fields():
    """EmbeddingHealthStatus has ConfigDict(extra='forbid') — unknown keys must raise."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EmbeddingHealthStatus(
            healthy=True,
            provider="ollama",
            model="nomic-embed-text",
            bogus_field="oops",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "provider_path",
    [
        "chaoscypher_core.adapters.embedding.ollama_provider.OllamaEmbeddingProvider",
        "chaoscypher_core.adapters.embedding.openai_provider.OpenAIEmbeddingProvider",
        "chaoscypher_core.adapters.embedding.gemini_provider.GeminiEmbeddingProvider",
        "chaoscypher_core.adapters.embedding.local_provider.LocalEmbeddingProvider",
    ],
)
def test_concrete_provider_classes_satisfy_protocol_surface(provider_path):
    """Each concrete adapter provider exposes every attribute EmbeddingProviderProtocol requires.

    Class-level check (no instantiation — avoids I/O setup). Catches silent drift
    if a provider is renamed or a method signature removed.

    ``model_name`` is an instance attribute set in ``__init__`` (not a class-level
    attribute), so it is verified via ``__init__`` parameter inspection rather than
    ``hasattr`` on the class object.
    """
    import importlib
    import inspect

    module_path, class_name = provider_path.rsplit(".", 1)
    provider_cls = getattr(importlib.import_module(module_path), class_name)

    # Class-level attributes/methods (defined on the class itself)
    for attr_name in ("provider_type", "embed", "batch_embed", "check_health"):
        assert hasattr(provider_cls, attr_name), (
            f"{class_name} is missing `{attr_name}` required by EmbeddingProviderProtocol"
        )

    # model_name is assigned as self.model_name inside __init__ — verify via parameter
    init_params = list(inspect.signature(provider_cls.__init__).parameters.keys())
    assert "model_name" in init_params, (
        f"{class_name}.__init__ is missing `model_name` parameter required by EmbeddingProviderProtocol"
    )


def test_embedding_health_status_reachable_via_ports_init() -> None:
    """EmbeddingHealthStatus is exported from the top-level ports package."""
    from chaoscypher_core.ports import EmbeddingHealthStatus as EmbeddingHealthStatusFromInit

    assert EmbeddingHealthStatusFromInit is EmbeddingHealthStatus
