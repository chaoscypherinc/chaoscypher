# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for output_schema validation (finding #8)."""

import pytest

from chaoscypher_core.exceptions import SchemaValidationError
from chaoscypher_core.services.workflows.engine.output_parser import OutputManager


def test_valid_output_passes() -> None:
    manager = OutputManager()
    workflow = {
        "output_schema": {
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"type": "string"}},
        }
    }
    step_outputs = {"s1": {"result": "ok"}}
    out = manager.extract_outputs(workflow, step_outputs)
    assert out == {"result": "ok"}


def test_missing_required_raises_schema_validation_error() -> None:
    manager = OutputManager()
    workflow = {
        "output_schema": {
            "type": "object",
            "required": ["result"],
            "properties": {"result": {"type": "string"}},
        }
    }
    step_outputs = {"s1": {"other": "x"}}
    with pytest.raises(SchemaValidationError, match="result"):
        manager.extract_outputs(workflow, step_outputs)


def test_wrong_type_raises_schema_validation_error() -> None:
    manager = OutputManager()
    workflow = {
        "output_schema": {
            "properties": {"count": {"type": "integer"}},
        }
    }
    step_outputs = {"s1": {"count": "not-a-number"}}
    with pytest.raises(SchemaValidationError, match="count"):
        manager.extract_outputs(workflow, step_outputs)
