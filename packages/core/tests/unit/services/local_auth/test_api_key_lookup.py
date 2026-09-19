# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the keyed-selector API-key lookup (bcrypt CPU-exhaustion fix).

The bearer arm of the nginx ``auth_request`` subrequest used to bcrypt-verify
an attacker-supplied ``cc_live_`` token against *every* stored hash. These
tests pin the replacement: an HMAC selector indexes the single candidate
entry, so an unknown key costs zero bcrypt calls.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.services.local_auth import api_key_lookup
from chaoscypher_core.services.local_auth.api_key_lookup import resolve_api_key
from chaoscypher_core.services.local_auth.api_keys import (
    compute_api_key_selector,
    generate_api_key,
    hash_api_key,
)
from chaoscypher_core.services.local_auth.credentials import CredentialsFile
from chaoscypher_core.services.local_auth.errors import CorruptCredentialsFile


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def store(tmp_path: Path) -> CredentialsFile:
    """An initialized credentials file with no API keys yet."""
    creds = CredentialsFile(tmp_path / "credentials.json")
    creds.initialize("admin", "password123")
    return creds


def _add_key(store: CredentialsFile, name: str) -> tuple[str, str]:
    """Mint a key the way the service does; return ``(plaintext, key_id)``."""
    key = generate_api_key()
    secret = store.get_or_create_api_key_selector_secret()
    key_id = store.add_api_key(
        name, hash_api_key(key), selector=compute_api_key_selector(key, secret)
    )
    return key, key_id


def _add_legacy_key(store: CredentialsFile, name: str) -> tuple[str, str]:
    """Mint a key the pre-selector way (bcrypt hash only)."""
    key = generate_api_key()
    return key, store.add_api_key(name, hash_api_key(key))


class _BcryptCounter:
    """Counts calls to the bcrypt verify the lookup module uses."""

    def __init__(self) -> None:
        self.calls = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = api_key_lookup.verify_api_key

        def _counting(key: str, hashed: str) -> bool:
            self.calls += 1
            return bool(real(key, hashed))

        monkeypatch.setattr(api_key_lookup, "verify_api_key", _counting)


# --------------------------------------------------------------------------
# Selector secret
# --------------------------------------------------------------------------


def test_selector_secret_is_created_once_and_is_stable(store: CredentialsFile) -> None:
    """The per-install HMAC secret is minted on first need and then reused."""
    first = store.get_or_create_api_key_selector_secret()
    second = store.get_or_create_api_key_selector_secret()
    assert first == second
    assert len(first) == 64  # 32 bytes, hex


def test_selector_secret_differs_per_install(tmp_path: Path) -> None:
    """Two installs must not share a selector secret."""
    one = CredentialsFile(tmp_path / "a.json")
    one.initialize("admin", "password123")
    two = CredentialsFile(tmp_path / "b.json")
    two.initialize("admin", "password123")
    assert one.get_or_create_api_key_selector_secret() != (
        two.get_or_create_api_key_selector_secret()
    )


def test_selector_is_not_the_key_and_is_deterministic() -> None:
    """The selector is an index, not a credential: keyed, hex, and stable."""
    key = generate_api_key()
    secret = "ab" * 32
    selector = compute_api_key_selector(key, secret)
    assert selector == compute_api_key_selector(key, secret)
    assert key not in selector
    assert len(selector) == 64
    assert selector != compute_api_key_selector(key, "cd" * 32)


# --------------------------------------------------------------------------
# Selector storage + lookup
# --------------------------------------------------------------------------


def test_new_key_is_stored_with_a_selector(store: CredentialsFile) -> None:
    """``add_api_key`` persists the selector alongside the bcrypt hash."""
    key, key_id = _add_key(store, "CLI")
    secret = store.get_or_create_api_key_selector_secret()
    found = store.find_api_key_by_selector(compute_api_key_selector(key, secret))
    assert found is not None
    assert found[0] == key_id


def test_resolve_matches_the_selector_entry(store: CredentialsFile) -> None:
    """A valid key resolves to its id through the selector index."""
    _add_key(store, "one")
    key, key_id = _add_key(store, "two")
    _add_key(store, "three")
    assert resolve_api_key(key, store) == key_id


def test_resolve_matching_key_does_exactly_one_bcrypt(
    store: CredentialsFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with many stored keys, a hit costs one bcrypt verify."""
    for name in ("one", "two", "three"):
        _add_key(store, name)
    key, key_id = _add_key(store, "four")
    counter = _BcryptCounter()
    counter.install(monkeypatch)
    assert resolve_api_key(key, store) == key_id
    assert counter.calls == 1


def test_unknown_key_against_selector_entries_does_zero_bcrypt(
    store: CredentialsFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this fix exists for: no bcrypt work for an unknown key."""
    for name in ("one", "two", "three"):
        _add_key(store, name)
    counter = _BcryptCounter()
    counter.install(monkeypatch)
    assert resolve_api_key(generate_api_key(), store) is None
    assert counter.calls == 0


def test_wrong_prefix_never_touches_the_store(store: CredentialsFile) -> None:
    """A token without the ``cc_live_`` prefix is rejected before any I/O."""
    assert resolve_api_key("not-a-real-prefix-xxx", store) is None


def test_revoked_key_no_longer_resolves(store: CredentialsFile) -> None:
    """Revocation drops the selector entry too."""
    key, key_id = _add_key(store, "CLI")
    store.revoke_api_key(key_id)
    assert resolve_api_key(key, store) is None


# --------------------------------------------------------------------------
# Legacy (selector-less) entries: fallback + backfill
# --------------------------------------------------------------------------


def test_legacy_entry_still_verifies(store: CredentialsFile) -> None:
    """A key minted before this change keeps working."""
    key, key_id = _add_legacy_key(store, "old")
    assert resolve_api_key(key, store) == key_id


def test_legacy_entry_gains_a_selector_on_first_success(store: CredentialsFile) -> None:
    """A successful legacy verify backfills the selector and persists it."""
    key, key_id = _add_legacy_key(store, "old")
    assert store.get_legacy_api_key_hashes() == [(key_id, store.get_api_key_hashes()[0][1])]

    assert resolve_api_key(key, store) == key_id

    assert store.get_legacy_api_key_hashes() == []
    secret = store.get_or_create_api_key_selector_secret()
    found = store.find_api_key_by_selector(compute_api_key_selector(key, secret))
    assert found is not None
    assert found[0] == key_id


def test_backfilled_entry_then_costs_zero_bcrypt_for_a_wrong_key(
    store: CredentialsFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After migration the legacy loop is empty, so unknown keys are free."""
    key, _ = _add_legacy_key(store, "old")
    resolve_api_key(key, store)

    counter = _BcryptCounter()
    counter.install(monkeypatch)
    assert resolve_api_key(generate_api_key(), store) is None
    assert counter.calls == 0


def test_unknown_key_only_scans_the_legacy_entries(
    store: CredentialsFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selector-bearing entries are excluded from the migration loop."""
    for name in ("new-one", "new-two", "new-three"):
        _add_key(store, name)
    _add_legacy_key(store, "old-one")
    _add_legacy_key(store, "old-two")

    counter = _BcryptCounter()
    counter.install(monkeypatch)
    assert resolve_api_key(generate_api_key(), store) is None
    assert counter.calls == 2


def test_legacy_backfill_leaves_other_records_untouched(store: CredentialsFile) -> None:
    """Backfilling one entry must not disturb the rest of the file."""
    kept_key, kept_id = _add_key(store, "kept")
    legacy_key, legacy_id = _add_legacy_key(store, "old")
    epoch_before = store.get_session_epoch()

    assert resolve_api_key(legacy_key, store) == legacy_id

    assert store.get_session_epoch() == epoch_before
    assert {rec["id"] for rec in store.list_api_keys()} == {kept_id, legacy_id}
    assert resolve_api_key(kept_key, store) == kept_id


def test_corrupt_selector_secret_raises_a_typed_error(store: CredentialsFile) -> None:
    """A non-hex secret must surface as CorruptCredentialsFile, not ValueError.

    Otherwise every bearer verify — i.e. every ``/api/`` request's auth
    subrequest — 500s with a bare stdlib exception.
    """
    _add_key(store, "CLI")
    path = store._path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["api_key_selector_secret"] = "not-hex-at-all"  # pragma: allowlist secret
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorruptCredentialsFile):
        store.get_or_create_api_key_selector_secret()
    with pytest.raises(CorruptCredentialsFile):
        resolve_api_key(generate_api_key(), store)


def test_corrupt_selector_secret_error_leaks_neither_secret_nor_path(
    store: CredentialsFile,
) -> None:
    """The message carries no secret and no path — it is rendered into a 401.

    Every ``LocalAuthError`` maps to HTTP 401 and its message goes into the
    response envelope, and this error is reachable from the unauthenticated
    bearer arm of ``/auth/verify``. The path an operator needs is on the
    exception as ``.path`` and in the server-side log, not on the wire.
    """
    secret = store.get_or_create_api_key_selector_secret()
    path = store._path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["api_key_selector_secret"] = secret + "zz"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(CorruptCredentialsFile) as exc:
        store.get_or_create_api_key_selector_secret()

    message = str(exc.value)
    assert secret not in message
    assert str(path) not in message
    assert path.name not in message
    assert exc.value.path == str(path)
