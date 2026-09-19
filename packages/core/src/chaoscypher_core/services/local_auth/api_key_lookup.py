# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve a plaintext API key to its stored record without scanning bcrypt.

Why this module exists
----------------------
nginx runs ``/auth/verify`` as an ``auth_request`` subrequest on *every*
``/api/`` request, and the bearer arm of that endpoint used to bcrypt-verify
the caller's token against every stored hash before giving up. At cost-12
that is ~259 ms of CPU per failed attempt, unauthenticated, with the work
running in a thread pool shared with the operator's own cookie verifications
— so any LAN host could pin the CPU and deny the whole API.

The fix is the standard selector/verifier split. A ``cc_live_`` key carries
192 bits of entropy, so it does not need bcrypt's slowness to survive
guessing; bcrypt is there to protect a *stolen credentials file*. So each
record stores, next to its bcrypt hash, a keyed ``HMAC-SHA256`` of the
plaintext key under a per-install secret. Verification computes that selector
and looks up the single record that can possibly match: an unknown key costs
one HMAC and zero bcrypt calls. A matching key still has to pass bcrypt, so a
leaked credentials file — secret and all — still reveals no key material.

Migration
---------
Records minted before this change have no ``selector``. They keep verifying
through the old loop, but that loop now runs only over the *selector-less*
records, and a successful verify backfills the selector and persists it. Each
legacy key therefore costs the old scan exactly once, after which the loop is
empty and unknown keys are free again. No file-format version bump is
involved: ``selector`` is simply an optional field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.services.local_auth.api_keys import (
    API_KEY_PREFIX,
    compute_api_key_selector,
    verify_api_key,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.local_auth.credentials import CredentialsFile


def resolve_api_key(key: str, store: CredentialsFile) -> str | None:
    """Return the id of the stored key matching ``key``, or ``None``.

    Args:
        key: Plaintext API key supplied by the caller.
        store: Credentials file holding the hashes and the selector secret.

    Returns:
        The matching key id, or ``None`` when nothing matches. Never raises
        on a wrong key.

    Raises:
        CredentialsNotInitialized: If the credentials file does not exist
            (unchanged from the pre-selector behaviour).

    """
    if not key.startswith(API_KEY_PREFIX):
        return None

    selector = compute_api_key_selector(key, store.get_or_create_api_key_selector_secret())

    indexed = store.find_api_key_by_selector(selector)
    if indexed is not None:
        key_id, hashed = indexed
        return key_id if verify_api_key(key, hashed) else None

    # Migration path: only records that predate the selector index.
    for key_id, hashed in store.get_legacy_api_key_hashes():
        if verify_api_key(key, hashed):
            store.set_api_key_selector(key_id, selector)
            return key_id
    return None
