# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One commit payload: the finalizer, recovery and the CLI build it the same way (audit W2-13).

Recovery's re-commit copied the finalizer's dict by hand and dropped
``symmetric_relationships``, so a recovered source committed its symmetric
facts unoriented. Every builder now calls ``build_commit_payload``; these
tests drive the real builders and guard against a fourth hand-built copy.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.commit.payload import (
    COMMIT_PAYLOAD_KEYS,
    build_commit_payload,
)


_STORED = {
    "entities": [{"name": "Natásha"}],
    "relationships": [{"source": 0, "target": 0, "type": "sibling_of"}],
    "suggested_templates": [{"name": "Character"}],
    "suggested_edge_templates": [{"name": "sibling_of"}],
    "inverse_relationships": {"parent_of": "child_of"},
    "symmetric_relationships": ["sibling_of", "married_to"],
}


def test_the_builder_writes_every_key_and_empty_values_for_missing_ones() -> None:
    """Exactly the shared keys; a bare call gives the empty rules."""
    full = build_commit_payload(entities=[], relationships=[], extraction_results=_STORED)
    bare = build_commit_payload(entities=[], relationships=[])
    assert tuple(full) == COMMIT_PAYLOAD_KEYS
    assert tuple(bare) == COMMIT_PAYLOAD_KEYS
    assert full["symmetric_relationships"] == ["sibling_of", "married_to"]
    assert bare["symmetric_relationships"] == []
    assert bare["inverse_relationships"] == {}


@pytest.mark.asyncio
async def test_recovery_re_commit_carries_the_symmetric_relationships() -> None:
    """The recovery dispatch hands the stored payload's rules on, every key included."""
    from chaoscypher_core.services.sources.recovery import SourceRecovery

    service = SourceRecovery(adapter=MagicMock(), queue_client=AsyncMock())
    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:
        await service._dispatch_commit(
            source={"id": "src", "filename": "f.txt", "filepath": "/f.txt", "file_type": "text"},
            database_name="default",
            commit_data=dict(_STORED),
        )
    payload = mock_qic.await_args.kwargs["commit_data"]
    assert tuple(payload) == COMMIT_PAYLOAD_KEYS
    assert payload["symmetric_relationships"] == ["sibling_of", "married_to"]
    assert payload["inverse_relationships"] == {"parent_of": "child_of"}


def test_recovery_rebuild_from_rows_has_the_shared_keys() -> None:
    """With no stored payload, the rows rebuild through the same builder."""
    from chaoscypher_core.services.sources.recovery import SourceRecovery

    adapter = MagicMock()
    adapter.get_source_commit_payload.return_value = None
    adapter.list_source_entities.return_value = [{"name": "A"}]
    adapter.list_source_relationships.return_value = []
    service = SourceRecovery(adapter=adapter, queue_client=AsyncMock())
    data = service._load_commit_data(source_id="src", database_name="default")
    assert data is not None
    assert tuple(data) == COMMIT_PAYLOAD_KEYS


@pytest.mark.asyncio
async def test_the_finalizer_and_recovery_send_one_key_set() -> None:
    """The finalizer's queued payload has exactly the keys recovery sends."""
    from chaoscypher_core.operations.extraction import extraction_finalizer

    adapter = MagicMock()
    adapter.get_file.return_value = {"id": "src", "filename": "f.txt"}
    settings: Any = MagicMock()
    settings.auto_enable = True
    with patch(
        "chaoscypher_core.operations.queue_utils.queue_import_commit", new_callable=AsyncMock
    ) as mock_qic:
        await extraction_finalizer._queue_commit_phase(
            adapter,
            "src",
            "default",
            [{"name": "Natásha"}],
            [],
            dict(_STORED),
            settings,
        )
    payload = mock_qic.await_args.kwargs["commit_data"]
    assert tuple(payload) == COMMIT_PAYLOAD_KEYS
    assert payload["symmetric_relationships"] == ["sibling_of", "married_to"]


_PACKAGES = Path(__file__).resolve().parents[8] / "packages"
_PAYLOAD_MODULE = (
    _PACKAGES / "core/src/chaoscypher_core/services/sources/engine/commit/payload.py"
).resolve()


def _hand_built_payloads(path: Path) -> list[int]:
    """Lines of dict literals in ``path`` that carry the commit's relationship-rule keys.

    An extraction result (``ExtractionService``'s, with its ``metadata``) is the
    payload's input, not a payload, and is not counted.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if {"suggested_edge_templates", "inverse_relationships"} <= keys and (
                "metadata" not in keys
            ):
                lines.append(node.lineno)
    return lines


def test_no_source_file_hand_builds_a_commit_payload() -> None:
    """A dict literal with the payload's rule keys outside ``payload.py`` is a drifting copy."""
    roots = [_PACKAGES / "core/src", _PACKAGES / "cli/src"]
    assert all(r.is_dir() for r in roots), roots
    offenders = [
        f"{path}:{line}"
        for root in roots
        for path in sorted(root.rglob("*.py"))
        if path.resolve() != _PAYLOAD_MODULE
        for line in _hand_built_payloads(path)
    ]
    assert offenders == [], offenders
