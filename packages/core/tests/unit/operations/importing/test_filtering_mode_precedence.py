# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""An explicit per-call ``filtering_mode`` must beat the persisted row value.

The cascade used to read ``source_row.filtering_mode`` first and fall back to
``file_info.filtering_mode`` only when the row value was empty. Because
``sources.filtering_mode`` is non-nullable with ``default="balanced"``, the row
value is never empty -- so the payload branch was unreachable for every real
source and a caller passing ``filtering_mode`` paid for a full LLM
re-extraction under the persisted mode instead.
"""

from __future__ import annotations

import json
from typing import Any

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.operations.importing.import_service import _build_extraction_config
from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack


def _mode_for(file_info: dict[str, Any], source_row: dict[str, Any] | None) -> str | None:
    """Resolve the effective filtering mode the extractor would receive (the floor alone).

    The stack's own preset is consulted *after* both the payload and the row,
    so none of these cases reaches it.
    """
    raw = _build_extraction_config(
        stack=DomainStack.resolve(DomainRegistry(), []),
        settings=get_settings(),
        file_info=file_info,
        source_row=source_row,
    )
    return json.loads(raw).get("filtering_mode")


def test_explicit_payload_mode_beats_the_persisted_row() -> None:
    """The load-bearing case: a per-call override actually reaches the extractor.

    With the old row-first ordering this returns ``"balanced"`` -- the row wins
    and the caller's ``"strict"`` is silently discarded.
    """
    mode = _mode_for(
        file_info={"filtering_mode": "strict"},
        source_row={"filtering_mode": "balanced"},
    )
    assert mode == "strict"


def test_absent_payload_mode_falls_through_to_the_row() -> None:
    """Omitting the override must preserve the previous behaviour exactly.

    The enqueue site writes ``file_info["filtering_mode"] = filtering_mode``
    verbatim and that parameter defaults to ``None``, so this is what every
    caller that does not override looks like.
    """
    mode = _mode_for(
        file_info={"filtering_mode": None},
        source_row={"filtering_mode": "lenient"},
    )
    assert mode == "lenient"


def test_missing_payload_key_falls_through_to_the_row() -> None:
    """A payload with no ``filtering_mode`` key at all behaves the same."""
    mode = _mode_for(file_info={}, source_row={"filtering_mode": "lenient"})
    assert mode == "lenient"


def test_empty_string_payload_mode_is_not_treated_as_an_override() -> None:
    """An empty string is not an explicit choice -- fall through, don't blank."""
    mode = _mode_for(
        file_info={"filtering_mode": ""},
        source_row={"filtering_mode": "balanced"},
    )
    assert mode == "balanced"


def test_payload_mode_applies_when_there_is_no_source_row() -> None:
    """Legacy path: no row at all, payload is the only signal."""
    mode = _mode_for(file_info={"filtering_mode": "strict"}, source_row=None)
    assert mode == "strict"
