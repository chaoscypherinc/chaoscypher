# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for user-principal normalization (finding #7)."""

from types import SimpleNamespace

import pytest

from chaoscypher_core.services.workflows.management.history import _normalize_user


def test_none_user_returns_none() -> None:
    assert _normalize_user(None) is None


def test_dict_user() -> None:
    u = _normalize_user({"id": 1, "is_admin": True})
    assert u is not None
    assert u.id == 1
    assert u.is_admin is True


def test_object_user() -> None:
    u = _normalize_user(SimpleNamespace(id=2, is_admin=False))
    assert u is not None
    assert u.id == 2
    assert u.is_admin is False


def test_invalid_shape_raises_type_error() -> None:
    with pytest.raises(TypeError, match="user must be"):
        _normalize_user(42)
