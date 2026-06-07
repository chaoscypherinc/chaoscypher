# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backoff-multiplier resolution for ``SafeSession.commit()`` (Task C6).

The commit-retry backoff multiplier no longer reads the app singleton. It
resolves to an explicit ``backoff_multiplier`` parameter when supplied, else
to ``BackoffSettings().exponential_multiplier`` (the class default).

These tests drive the retry path with a fake parent ``commit`` that raises a
lock ``OperationalError`` once, then succeeds — capturing the delay handed to
``_retry_delay`` so the multiplier in effect is observable.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.settings import BackoffSettings


def _lock_error() -> OperationalError:
    return OperationalError("stmt", {}, Exception("database is locked"))


def _make_session(**kwargs: object) -> SafeSession:
    """Build a SafeSession without binding a real engine.

    ``Session.__init__`` is bypassed so we exercise only the retry/backoff
    logic in ``commit()``.
    """
    sess = SafeSession.__new__(SafeSession)
    sess._max_attempts = 3
    sess._base_delay = 2.0
    sess._transaction_depth = 0
    sess._backoff_multiplier = kwargs.get("backoff_multiplier")  # type: ignore[attr-defined]
    return sess


def _drive_commit(sess: SafeSession) -> list[float]:
    """Run commit() through one lock-retry and return the recorded delays."""
    delays: list[float] = []
    # First attempt raises a lock error, second succeeds.
    parent_calls = {"n": 0}

    def _fake_parent_commit(_self: object, **_kw: object) -> None:
        parent_calls["n"] += 1
        if parent_calls["n"] == 1:
            raise _lock_error()

    with (
        patch.object(Session, "commit", _fake_parent_commit),
        patch.object(SafeSession, "rollback", lambda self: None),
        patch.object(SafeSession, "new", []),
        patch.object(SafeSession, "dirty", []),
        patch.object(SafeSession, "deleted", []),
        patch.object(SafeSession, "_retry_delay", staticmethod(delays.append)),
    ):
        sess.commit()
    return delays


def test_explicit_multiplier_honored() -> None:
    """An explicit ``backoff_multiplier`` drives the commit-retry delay."""
    sess = _make_session(backoff_multiplier=3.0)
    delays = _drive_commit(sess)
    # base_delay 2.0 * 3**0 (attempt 0) = 2.0
    assert delays == [2.0]


def test_none_uses_class_default_not_singleton() -> None:
    """``backoff_multiplier=None`` resolves to the class default, not the singleton."""
    poisoned = MagicMock()
    poisoned.backoff.exponential_multiplier = 9.0

    sess = _make_session(backoff_multiplier=None)
    with patch("chaoscypher_core.app_config.get_settings", return_value=poisoned):
        delays = _drive_commit(sess)

    expected = 2.0 * (BackoffSettings().exponential_multiplier ** 0)
    assert delays == [expected]
    assert BackoffSettings().exponential_multiplier != 9.0
