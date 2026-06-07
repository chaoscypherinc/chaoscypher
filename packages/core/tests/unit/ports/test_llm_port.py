# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for LLMProviderPort and TaskType.

LLMProviderPort is the service-facing contract for LLM access. Phase 2 will
migrate ~15 service files off direct adapter imports onto this port. TaskType
lives in the same ports module so it's part of the port's vocabulary.
"""

from chaoscypher_core.ports.llm import LLMProviderPort, TaskType


def test_task_type_round_trips() -> None:
    """TaskType members can be round-tripped via their string values."""
    assert TaskType("chat") == TaskType.CHAT
    assert TaskType("embedding") == TaskType.EMBEDDING
    assert TaskType("tool") == TaskType.TOOL


def test_task_type_str_values() -> None:
    """TaskType StrEnum members have the expected lowercase string values."""
    assert str(TaskType.CHAT) == "chat"
    assert str(TaskType.EMBEDDING) == "embedding"
    assert str(TaskType.TOOL) == "tool"


def test_llm_provider_port_is_runtime_checkable() -> None:
    """LLMProviderPort is @runtime_checkable so isinstance() works at runtime."""

    class DuckProvider:
        async def chat(self, messages, tools=None, stream=False, **kwargs):  # type: ignore[override]
            return None

    assert isinstance(DuckProvider(), LLMProviderPort)


def test_object_without_chat_does_not_satisfy_port() -> None:
    """An object missing ``chat`` is correctly rejected by the Protocol check."""

    class NotAProvider:
        def other_method(self) -> None:
            pass

    assert not isinstance(NotAProvider(), LLMProviderPort)


def test_concrete_provider_satisfies_port() -> None:
    """The concrete LLMProvider from adapters/llm must structurally satisfy the port."""
    from chaoscypher_core.adapters.llm.provider import LLMProvider

    # Verify method presence (class-level check — instantiation requires IO).
    assert hasattr(LLMProvider, "chat"), (
        "LLMProvider is missing method `chat` required by LLMProviderPort"
    )


def test_task_type_shim_module_deleted() -> None:
    """The Phase 1 shim module at adapters/llm/types.py must be gone."""
    import importlib
    from pathlib import Path

    shim_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "chaoscypher_core"
        / "adapters"
        / "llm"
        / "types.py"
    )
    assert not shim_path.exists(), f"Phase 1 TaskType shim still present at {shim_path}"

    try:
        importlib.import_module("chaoscypher_core.adapters.llm.types")
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover - regression guard
        msg = "chaoscypher_core.adapters.llm.types is still importable"
        raise AssertionError(msg)


def test_no_runtime_imports_of_types_shim() -> None:
    """No source file under packages/*/src imports the deleted TaskType shim."""
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
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "chaoscypher_core.adapters.llm.types",
                    "chaoscypher_core.adapters.llm.schema",
                }:
                    # schema barrel no longer re-exports TaskType; flag explicit name imports.
                    imported = {alias.name for alias in node.names}
                    if "TaskType" in imported:
                        bad.append(f"{path}:{node.lineno} (from {node.module})")
    assert not bad, "Unexpected TaskType imports from deleted shim paths:\n" + "\n".join(bad)
