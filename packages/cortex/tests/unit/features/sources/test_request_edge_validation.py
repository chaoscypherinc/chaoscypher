# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The request edges refuse a bad depth and a non-positive degree cap.

Regression (section-audit 2026-10-12): ``analysis_depth`` was a plain
``str`` on URL import, trigger and confirm (and the two multipart Form
params), so ``"deep"`` got a 202, was stored, and failed in the worker —
after ``force=true`` had already deleted a committed source's graph. The
depth is :data:`chaoscypher_core.models.AnalysisDepth` at every edge, and
every copy of the vocabulary (the worker's validator, the CLI choices, the
MCP tool schemas) derives from it. ``max_entity_degree_override`` ``<= 0``
was refused by the confirm request only; it is one positive-int type at
every edge.
"""

from __future__ import annotations

from typing import get_args

import click
import pytest
from pydantic import ValidationError

from chaoscypher_core.models import AnalysisDepth
from chaoscypher_cortex.features.sources.extraction_api import (
    ConfirmExtractionRequest,
    TriggerExtractionRequest,
)
from chaoscypher_cortex.features.sources.models import UrlImportRequest


DEPTHS = get_args(AnalysisDepth)


@pytest.mark.parametrize("depth", DEPTHS)
def test_every_depth_is_accepted_at_every_edge(depth: str) -> None:
    assert (
        UrlImportRequest(url="https://example.com/", analysis_depth=depth).analysis_depth == depth
    )
    assert TriggerExtractionRequest(analysis_depth=depth).analysis_depth == depth
    assert ConfirmExtractionRequest(analysis_depth=depth).analysis_depth == depth


@pytest.mark.parametrize("bad", ["deep", "FULL", "", "quick "])
def test_an_unknown_depth_is_refused_at_every_edge(bad: str) -> None:
    with pytest.raises(ValidationError):
        UrlImportRequest(url="https://example.com/", analysis_depth=bad)
    with pytest.raises(ValidationError):
        TriggerExtractionRequest(analysis_depth=bad)
    with pytest.raises(ValidationError):
        ConfirmExtractionRequest(analysis_depth=bad)


def test_the_multipart_forms_and_worker_share_the_vocabulary() -> None:
    """One source: the Form params, the worker validator, the CLI and MCP copies."""
    from chaoscypher_cli.commands.source import confirm, extract
    from chaoscypher_core.mcp import tools as mcp_tools
    from chaoscypher_core.services.sources.engine.extraction import orchestration
    from chaoscypher_cortex.features.sources import api

    assert frozenset(DEPTHS) == orchestration._VALID_DEPTH_VALUES
    for command in (extract.extract_cmd, confirm.confirm_cmd):
        choice = next(p.type for p in command.params if p.name in {"depth", "analysis_depth"})
        assert isinstance(choice, click.Choice) and tuple(choice.choices) == DEPTHS
    enums = [
        prop["enum"]
        for tool in mcp_tools.TOOL_DEFINITIONS
        for prop in tool.input_schema.get("properties", {}).values()
        if prop.get("description", "").startswith("Extraction depth")
    ]
    assert enums and all(tuple(e) == DEPTHS for e in enums)
    for route_fn in (api.upload_file, api.upload_batch):
        param = route_fn.__annotations__["analysis_depth"]
        assert param is AnalysisDepth


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_degree_cap_is_refused_at_every_edge(bad: int) -> None:
    with pytest.raises(ValidationError):
        UrlImportRequest(url="https://example.com/", max_entity_degree_override=bad)
    with pytest.raises(ValidationError):
        TriggerExtractionRequest(max_entity_degree_override=bad)
    with pytest.raises(ValidationError):
        ConfirmExtractionRequest(max_entity_degree_override=bad)


def test_a_positive_degree_cap_is_accepted() -> None:
    assert TriggerExtractionRequest(max_entity_degree_override=3).max_entity_degree_override == 3
