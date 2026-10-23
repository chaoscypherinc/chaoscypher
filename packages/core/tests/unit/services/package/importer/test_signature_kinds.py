# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Which signature results refuse an import: a failed ed25519 entry, never one the library cannot check."""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.package.importer.service import refuse_failed_signatures


def test_a_failed_key_signature_refuses() -> None:
    with pytest.raises(ValidationError, match="signature"):
        refuse_failed_signatures(
            [{"path": "sig", "format": "ed25519", "verified": False, "error": "bad", "key": "k"}]
        )


def test_an_entry_the_library_cannot_verify_is_skipped_not_refused() -> None:
    """A sigstore bundle is reported unverified without the extra; it must not block the import."""
    skipped = refuse_failed_signatures(
        [
            {
                "path": "sig",
                "format": "sigstore",
                "verified": False,
                "error": "sigstore extra missing",
            },
            {"path": "sig2", "format": "ed25519", "verified": True, "error": None, "key": "k"},
        ]
    )
    assert [s["format"] for s in skipped] == ["sigstore"]


def test_no_signatures_verify_nothing() -> None:
    assert refuse_failed_signatures([]) == []
