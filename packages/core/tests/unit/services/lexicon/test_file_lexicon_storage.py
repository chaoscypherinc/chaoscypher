# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""FileLexiconStorage persists lexicon login state to auth.json.

Pins the Tier-3 move from ``credentials.json`` to ``auth.json``
(PathSettings.auth_file). There is no read-fallback to the old file
(D3: no compat shims).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from chaoscypher_core.services.lexicon.models import LexiconAuthConfig
from chaoscypher_core.services.lexicon.storage import FileLexiconStorage


@pytest.mark.unit
@pytest.mark.core
class TestFileLexiconStorageAuthJson:
    """FileLexiconStorage reads/writes auth.json, never credentials.json."""

    def test_save_writes_auth_json_not_credentials_json(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        storage.save_credentials(
            "https://lexicon.example", LexiconAuthConfig(token="tok", username="alice")
        )

        assert (tmp_path / "auth.json").exists()
        assert not (tmp_path / "credentials.json").exists()

    def test_auth_file_attribute_points_at_auth_json(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        assert storage.auth_file == tmp_path / "auth.json"

    def test_round_trips_credentials(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        storage.save_credentials(
            "https://lexicon.example",
            LexiconAuthConfig(
                token="tok", refresh_token="ref", username="alice", expires_at="2030-01-01"
            ),
        )

        revived = FileLexiconStorage(tmp_path).load_credentials()
        assert revived is not None
        assert revived.token is not None
        assert revived.token.get_secret_value() == "tok"
        assert revived.refresh_token is not None
        assert revived.refresh_token.get_secret_value() == "ref"
        assert revived.username == "alice"
        assert revived.expires_at == "2030-01-01"

    def test_get_lexicon_url_reads_from_auth_json(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        storage.save_credentials("https://custom.hub.example", LexiconAuthConfig(token="tok"))
        assert FileLexiconStorage(tmp_path).get_lexicon_url() == "https://custom.hub.example"

    def test_no_read_fallback_to_credentials_json(self, tmp_path: Path) -> None:
        """A leftover credentials.json is ignored — load returns None."""
        (tmp_path / "credentials.json").write_text(
            '{"token": "stale", "username": "old"}', encoding="utf-8"
        )
        assert FileLexiconStorage(tmp_path).load_credentials() is None

    def test_clear_removes_auth_json(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        storage.save_credentials("https://lexicon.example", LexiconAuthConfig(token="tok"))
        assert (tmp_path / "auth.json").exists()

        storage.clear_credentials()
        assert not (tmp_path / "auth.json").exists()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
    def test_auth_json_has_0600_permissions(self, tmp_path: Path) -> None:
        storage = FileLexiconStorage(tmp_path)
        storage.save_credentials("https://lexicon.example", LexiconAuthConfig(token="tok"))

        mode = stat.S_IMODE((tmp_path / "auth.json").stat().st_mode)
        assert mode == 0o600
