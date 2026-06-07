# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Targeted unit tests for ``chaoscypher_core.utils.id.generate_id``.

The file is small but called from hundreds of sites; mutation testing
of this surface keeps the canonical helper honest. Tests assert the
exact shape so mutations that flip the prefix join, drop the UUID, or
swap the conditional get caught.
"""

from __future__ import annotations

import re
import uuid

from chaoscypher_core.utils.id import generate_id


# RFC 4122 v4 UUID: 8-4-4-4-12 hex with the canonical version (4) +
# variant nibble. ``uuid.uuid4`` guarantees both.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def test_no_prefix_returns_bare_uuid_v4() -> None:
    """No prefix → plain UUID v4 (36 chars, no underscore)."""
    out = generate_id()
    assert "_" not in out
    assert len(out) == 36
    assert _UUID_RE.match(out) is not None


def test_none_prefix_returns_bare_uuid_v4() -> None:
    """Explicit ``None`` matches the default branch."""
    out = generate_id(None)
    assert "_" not in out
    assert _UUID_RE.match(out) is not None


def test_prefix_attached_with_underscore() -> None:
    """Prefix is joined with a single underscore — not a colon, not a dash."""
    out = generate_id("node")
    assert out.startswith("node_")
    assert out.count("_") == 1  # not "node__"
    rest = out[len("node_") :]
    assert _UUID_RE.match(rest) is not None


def test_prefix_preserved_verbatim() -> None:
    """Prefix string is not normalised, lower-cased, or truncated."""
    out = generate_id("CHUNK")
    assert out.startswith("CHUNK_")
    assert "chunk" not in out[: len("CHUNK_")].lower().replace("chunk_", "")


def test_empty_prefix_treated_as_no_prefix() -> None:
    """Empty string is falsy → bare UUID, no leading underscore."""
    out = generate_id("")
    assert not out.startswith("_")
    assert _UUID_RE.match(out) is not None


def test_each_call_returns_a_new_id() -> None:
    """Two consecutive calls never collide."""
    a = generate_id()
    b = generate_id()
    assert a != b


def test_uuid_part_is_a_valid_v4() -> None:
    """The UUID portion parses with ``uuid.UUID`` and reports version 4."""
    raw = generate_id()
    parsed = uuid.UUID(raw)
    assert parsed.version == 4


def test_uuid_part_after_prefix_is_a_valid_v4() -> None:
    """Same invariant when a prefix is present."""
    raw = generate_id("edge")
    _, _, rest = raw.partition("_")
    parsed = uuid.UUID(rest)
    assert parsed.version == 4
