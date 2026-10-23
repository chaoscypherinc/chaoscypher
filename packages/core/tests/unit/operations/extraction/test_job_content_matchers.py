# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The queue chunk handler strips re-fetched rows like the group planner did.

``_job_content_matchers`` resolves the content-exclusion matchers from the
domain pinned on the job and the source row's ``content_filtering`` toggle, the
same decision ``_apply_content_filtering`` makes when the groups are built, so
the handler numbers the same group text (``build_group_text``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from chaoscypher_core.operations.extraction.chunk_extraction_service import (
    _job_content_matchers,
)


_REGISTRY = "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry"


def _domain() -> Any:
    """A pack whose only exclusion is a page-header line pattern."""
    return SimpleNamespace(
        name="paged",
        role="pack",
        get_content_exclusions=lambda: {
            "custom_patterns": [
                {
                    "regex": r"^Page \d+$",
                    "mode": "line_ratio",
                    "threshold": 0.4,
                    "description": "page header",
                }
            ]
        },
    )


def _floor() -> Any:
    """The floor, excluding a copyright line (every source's stack has it)."""
    return SimpleNamespace(
        name="generic",
        role="floor",
        get_content_exclusions=lambda: {
            "custom_patterns": [
                {
                    "regex": r"^Copyright .*$",
                    "mode": "line_ratio",
                    "threshold": 0.4,
                    "description": "copyright",
                }
            ]
        },
    )


def _matchers_for(job: dict[str, Any], source_row: dict[str, Any] | None) -> list[Any]:
    adapter = MagicMock()
    adapter.get_source.return_value = source_row
    domains = {"generic": _floor(), "paged": _domain()}
    registry = MagicMock()
    registry.get_domain.side_effect = domains.get
    with patch(_REGISTRY, return_value=registry):
        return _job_content_matchers(adapter, job, "default", MagicMock())


def test_job_domain_matchers_are_resolved() -> None:
    matchers = _matchers_for(
        {"id": "j", "source_id": "s", "detected_domain": "paged"}, {"content_filtering": True}
    )
    # the job's stack: the floor's exclusions and the pack's (Task 7: not the first pack alone)
    assert len(matchers) == 2
    assert matchers[0].match_and_strip("Copyright 1869\nBody text.")[1] == "Body text."
    assert matchers[1].match_and_strip("Page 3\nBody text.")[1] == "Body text."


def test_source_toggle_off_means_no_stripping() -> None:
    assert (
        _matchers_for(
            {"id": "j", "source_id": "s", "forced_domain": "paged"}, {"content_filtering": False}
        )
        == []
    )


def test_job_without_domain_means_no_stripping() -> None:
    assert _matchers_for({"id": "j", "source_id": "s"}, None) == []


def test_registry_failure_is_not_fatal() -> None:
    with patch(_REGISTRY, side_effect=RuntimeError("boom")):
        assert _job_content_matchers(MagicMock(), {"detected_domain": "x"}, "default", None) == []
