# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for VisionStorageProtocol contract.

These tests don't exercise behaviour — they assert the protocol's
shape (method names, signatures) and the TypedDict structure. Real
behaviour tests live with the adapter mixin in
test_vision_pages_mixin.py.
"""

from __future__ import annotations

import inspect

from chaoscypher_core.ports.storage_vision import (
    VisionJob,
    VisionPageDescription,
    VisionStorageProtocol,
)


def test_protocol_has_expected_methods() -> None:
    expected = {
        "create_vision_job_with_pages",
        "get_vision_job",
        "get_vision_job_by_source",
        "list_vision_page_descriptions",
        "update_vision_page_description",
        "increment_vision_job_completed_and_check",
        "reset_vision_page_for_retry",
    }
    actual = {
        name
        for name, value in inspect.getmembers(VisionStorageProtocol)
        if not name.startswith("_") and callable(value)
    }
    assert expected.issubset(actual), f"missing: {expected - actual}"


def test_vision_job_typeddict_keys() -> None:
    expected = {
        "id",
        "source_id",
        "total_pages",
        "completed",
        "failed",
        "created_at",
        "updated_at",
    }
    assert set(VisionJob.__annotations__.keys()) == expected


def test_vision_page_description_typeddict_keys() -> None:
    expected = {
        "id",
        "source_id",
        "vision_job_id",
        "page_number",
        "region_index",
        "kind",
        "status",
        "description",
        "image_path",
        "finish_reason",
        "error_message",
        "attempts",
        "created_at",
        "updated_at",
    }
    assert set(VisionPageDescription.__annotations__.keys()) == expected
