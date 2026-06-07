# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for tool-reference validation at execution (finding #10)."""

from typing import Any

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.workflows.orchestrator import (
    execute_workflow_task,
)


class StubWorkflowService:
    def get_workflow(self, wid: str) -> dict[str, Any]:
        return {
            "id": wid,
            "name": "x",
            "is_active": True,
            "output_schema": None,
            "input_schema": {"type": "object"},
        }

    def list_workflow_steps(self, wid: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "s1",
                "step_number": 1,
                "name": "Step1",
                "tool_type": "system_tool",
                "tool_id": "nonexistent_tool",
                "configuration": {},
            }
        ]


class StubToolService:
    def list_system_tools(self) -> list[dict[str, Any]]:
        return [{"id": "real_tool"}]

    def list_user_tools(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_missing_tool_rejected_before_execution() -> None:
    with pytest.raises(ValidationError, match="nonexistent_tool"):
        await execute_workflow_task(
            workflow_id="w",
            inputs={},
            workflow_service=StubWorkflowService(),
            tool_service=StubToolService(),
            llm_service=None,
            graph_repository=None,  # type: ignore[arg-type]
            search_repository=None,
            database_name="default",
        )
