# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Property-based tests for ``chaoscypher_core.utils.id.generate_id``.

Invariants pinned:

1. Always returns a non-empty string.
2. Calling N times yields N distinct values (UUID4 collision probability ≈ 0).
3. With a prefix, result is ``"<prefix>_<uuid>"`` where the suffix parses
   as a UUID4.
4. Without a prefix, the result parses as a UUID4.
"""

from __future__ import annotations

import uuid

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from chaoscypher_core.utils.id import generate_id


# Prefixes used in production are lowercase ASCII identifiers like
# ``"node"`` / ``"chunk"`` / ``"emb"``. The function itself accepts any
# string, but we constrain the strategy to non-empty strings that don't
# contain the ``_`` separator so the rsplit-style invariant in test 3 is
# unambiguous.
_PREFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
_prefix_strategy = st.text(alphabet=_PREFIX_ALPHABET, min_size=1, max_size=20)


@given(prefix=_prefix_strategy)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_generate_id_with_prefix_starts_with_prefix_underscore(prefix: str) -> None:
    """With a prefix, result starts with ``"<prefix>_"`` and suffix is UUID4."""
    result = generate_id(prefix)
    assert result.startswith(f"{prefix}_")
    suffix = result[len(prefix) + 1 :]
    # Must parse as a UUID (specifically version 4).
    parsed = uuid.UUID(suffix)
    assert parsed.version == 4


@given(st.integers(min_value=1, max_value=200))
@settings(max_examples=20)
def test_generate_id_returns_unique_values(n_calls: int) -> None:
    """Generating N IDs yields N distinct values."""
    ids = {generate_id() for _ in range(n_calls)}
    assert len(ids) == n_calls


@given(prefix=_prefix_strategy)
@settings(max_examples=100)
def test_generate_id_with_prefix_is_non_empty(prefix: str) -> None:
    """Result is always a non-empty string regardless of prefix."""
    result = generate_id(prefix)
    assert isinstance(result, str)
    assert len(result) > 0


def test_generate_id_without_prefix_parses_as_uuid4() -> None:
    """Without a prefix, result parses as a UUID4. Repeats inline for breadth."""
    # Run a large batch to exercise the no-prefix branch broadly.
    for _ in range(500):
        uid = generate_id()
        parsed = uuid.UUID(uid)
        assert parsed.version == 4


@given(st.none() | _prefix_strategy)
@settings(max_examples=100)
def test_generate_id_accepts_none_or_string(prefix: str | None) -> None:
    """Both ``None`` and any string prefix produce a non-empty string."""
    result = generate_id(prefix)
    assert isinstance(result, str)
    assert len(result) > 0
    if prefix is None:
        # No-prefix path: bare UUID4.
        parsed = uuid.UUID(result)
        assert parsed.version == 4
