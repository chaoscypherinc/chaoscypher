# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pre-launch alias cleanup: legacy filtering modes must not be accepted."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)


CANONICAL_MODES = {"maximum", "strict", "balanced", "lenient", "minimal", "unfiltered"}
LEGACY_ALIASES = {"standard", "narrative", "precise", "permissive", "raw"}


def test_canonical_modes_parse() -> None:
    """All six canonical modes resolve without error."""
    for name in CANONICAL_MODES:
        result = resolve_filtering_config(name)
        assert result is not None, f"Canonical mode {name!r} should resolve"


@pytest.mark.parametrize("alias", sorted(LEGACY_ALIASES))
def test_legacy_aliases_rejected(alias: str) -> None:
    """Legacy alias names must raise ValueError, not silently succeed."""
    with pytest.raises(ValueError) as exc:
        resolve_filtering_config(alias)
    assert alias in str(exc.value), (
        f"ValueError message should mention the rejected alias {alias!r}"
    )
