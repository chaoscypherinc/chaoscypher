# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for dependency cycle + dangling-reference validation (finding #3)."""

from chaoscypher_core.services.workflows.engine.validator import WorkflowValidator


def _step(step_id: str, depends_on: list[str] | None = None) -> dict:
    return {
        "id": step_id,
        "tool_id": "t",
        "configuration": {},
        "depends_on": depends_on or [],
    }


def test_detects_self_cycle() -> None:
    wf = {
        "id": "w",
        "name": "n",
        "steps": [_step("a", ["a"])],
    }
    errors = WorkflowValidator.validate_workflow(wf)
    assert any("cycle" in e.lower() and "a" in e for e in errors)


def test_detects_two_step_cycle() -> None:
    wf = {
        "id": "w",
        "name": "n",
        "steps": [_step("a", ["b"]), _step("b", ["a"])],
    }
    errors = WorkflowValidator.validate_workflow(wf)
    assert any("cycle" in e.lower() for e in errors)


def test_detects_dangling_reference() -> None:
    wf = {
        "id": "w",
        "name": "n",
        "steps": [_step("a", ["ghost"])],
    }
    errors = WorkflowValidator.validate_workflow(wf)
    assert any("ghost" in e and "depends_on" in e for e in errors)


def test_valid_dag_passes() -> None:
    wf = {
        "id": "w",
        "name": "n",
        "steps": [_step("a"), _step("b", ["a"]), _step("c", ["a", "b"])],
    }
    errors = WorkflowValidator.validate_workflow(wf)
    assert errors == []
