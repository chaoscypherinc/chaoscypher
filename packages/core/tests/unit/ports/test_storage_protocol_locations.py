# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression test pinning the canonical import path of each storage Protocol.

The `storage_<domain>.py` filename pattern is the project convention
for storage Protocols (see the public contributor guide and existing code patterns).
If a Protocol gets moved in the future, this test is the canary.
"""

from __future__ import annotations

import importlib


def _assert_protocol_at(module_path: str, class_name: str) -> None:
    """Import ``class_name`` from ``module_path`` and confirm it's a runtime_checkable Protocol."""
    module = importlib.import_module(module_path)
    assert hasattr(module, class_name), f"{class_name} not found in {module_path}"
    cls = getattr(module, class_name)
    # runtime_checkable Protocols have _is_runtime_protocol=True
    assert getattr(cls, "_is_runtime_protocol", False), (
        f"{class_name} at {module_path} is not a @runtime_checkable Protocol"
    )


# Assertions added one-per-task as each Protocol migrates to its new location.


def test_workflow_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_workflows",
        "WorkflowStorageProtocol",
    )


def test_workflow_execution_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_workflow_executions",
        "WorkflowExecutionStorageProtocol",
    )


def test_tool_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_tools",
        "ToolStorageProtocol",
    )


def test_chat_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_chats",
        "ChatStorageProtocol",
    )


def test_trigger_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_triggers",
        "TriggerStorageProtocol",
    )


def test_llm_metrics_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_llm_metrics",
        "LLMMetricsStorageProtocol",
    )


def test_extraction_submission_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_extraction_submissions",
        "ExtractionSubmissionStorageProtocol",
    )


def test_source_storage_protocol_location() -> None:
    _assert_protocol_at(
        "chaoscypher_core.ports.storage_sources",
        "SourceStorageProtocol",
    )


# GraphSnapshotStorageProtocol and GraphBreakdownQueryProtocol are NOT
# `@runtime_checkable` (intentional — they're structural Protocols only), so
# `_assert_protocol_at` (which enforces `_is_runtime_protocol`) cannot be used.
# The weaker `hasattr` check still pins the canonical module location, which is
# the regression-safety property this test file guards.


def test_graph_snapshot_storage_protocol_location() -> None:
    module = importlib.import_module("chaoscypher_core.ports.storage_graph_snapshot")
    assert hasattr(module, "GraphSnapshotStorageProtocol"), (
        "GraphSnapshotStorageProtocol not found in storage_graph_snapshot"
    )


def test_graph_breakdown_query_protocol_location() -> None:
    module = importlib.import_module("chaoscypher_core.ports.storage_graph_snapshot")
    assert hasattr(module, "GraphBreakdownQueryProtocol"), (
        "GraphBreakdownQueryProtocol not found in storage_graph_snapshot"
    )
