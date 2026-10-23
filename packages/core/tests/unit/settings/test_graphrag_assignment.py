# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAG levers set after construction are validated (audit R9).

``sweep_overrides.apply_set`` (and every profile, test or tool) sets a lever
with ``setattr(settings.graphrag, name, value)``. Until 2026-10-06 that skipped
validation: ``triple_selection='conected'`` ran ``all_edges`` and was pinned
under a fingerprint of its own.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chaoscypher_core.settings import EngineSettings


def test_a_misspelt_lever_value_raises_on_assignment() -> None:
    settings = EngineSettings()
    with pytest.raises(ValidationError, match="triple_selection"):
        setattr(settings.graphrag, "triple_selection", "conected")  # noqa: B010 - the setter apply_set uses
    assert settings.graphrag.triple_selection == "connected"


def test_out_of_range_and_unknown_levers_raise_on_assignment() -> None:
    settings = EngineSettings()
    with pytest.raises(ValidationError):
        settings.graphrag.vector_reserve = -1
    with pytest.raises(ValidationError):
        setattr(settings.graphrag, "vector_reserv", 8)  # noqa: B010
    settings.graphrag.vector_reserve = 8
    assert settings.graphrag.vector_reserve == 8
