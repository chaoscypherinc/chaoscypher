# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SecretStr redaction + disk round-trip for lexicon credentials.

These tests pin the dual invariant that motivated the migration:
1. ``repr()`` and ``str()`` of the settings/config object must NOT
   contain the plaintext token (logs / error messages / structlog
   events serialize via repr).
2. ``model_dump()`` MUST emit the plaintext token, because the
   settings.yaml + auth.json round-trip writes via model_dump and
   reads via model_validate. Breaking this round-trip drops the user
   out of session on the next restart.
"""

from pydantic import SecretStr

from chaoscypher_core.services.lexicon.models import LexiconAuthConfig
from chaoscypher_core.settings import LexiconSettings


def test_lexicon_settings_refresh_token_redacted_in_repr():
    s = LexiconSettings(refresh_token="rt_super_secret_value")
    assert "rt_super_secret_value" not in repr(s)
    assert "rt_super_secret_value" not in str(s)


def test_lexicon_settings_token_redacted_in_repr():
    s = LexiconSettings(token="tk_access_secret_value")
    assert "tk_access_secret_value" not in repr(s)
    assert "tk_access_secret_value" not in str(s)


def test_lexicon_settings_dump_preserves_plaintext_for_disk():
    s = LexiconSettings(token="tk_v", refresh_token="rt_v")
    dumped = s.model_dump()
    assert dumped["token"] == "tk_v"
    assert dumped["refresh_token"] == "rt_v"


def test_lexicon_settings_roundtrip_through_model_dump():
    original = LexiconSettings(token="tk_v", refresh_token="rt_v")
    revived = LexiconSettings.model_validate(original.model_dump())
    assert revived.token is not None
    assert revived.refresh_token is not None
    assert revived.token.get_secret_value() == "tk_v"
    assert revived.refresh_token.get_secret_value() == "rt_v"


def test_lexicon_settings_field_type_is_secret_str():
    s = LexiconSettings(token="tk_v")
    assert isinstance(s.token, SecretStr)
    assert s.token.get_secret_value() == "tk_v"


def test_lexicon_auth_config_refresh_token_redacted_in_repr():
    c = LexiconAuthConfig(refresh_token="rt_super_secret_value")
    assert "rt_super_secret_value" not in repr(c)


def test_lexicon_auth_config_token_redacted_in_repr():
    c = LexiconAuthConfig(token="tk_secret_value")
    assert "tk_secret_value" not in repr(c)


def test_lexicon_auth_config_dump_preserves_plaintext_for_disk():
    c = LexiconAuthConfig(token="tk_v", refresh_token="rt_v")
    dumped = c.model_dump()
    assert dumped["token"] == "tk_v"
    assert dumped["refresh_token"] == "rt_v"


def test_lexicon_auth_config_roundtrip():
    original = LexiconAuthConfig(token="tk_v", refresh_token="rt_v")
    revived = LexiconAuthConfig.model_validate(original.model_dump())
    assert revived.token is not None
    assert revived.refresh_token is not None
    assert revived.token.get_secret_value() == "tk_v"
    assert revived.refresh_token.get_secret_value() == "rt_v"


def test_file_lexicon_storage_disk_roundtrip_preserves_plaintext(tmp_path):
    """Writing then reading auth.json preserves token plaintext.

    save_credentials hand-builds a dict and json.dumps it, so it
    bypasses model_dump_json. This pins that the file-on-disk path
    still emits plaintext tokens (so the next startup can authenticate).
    """
    from chaoscypher_core.services.lexicon.storage import FileLexiconStorage

    storage = FileLexiconStorage(tmp_path)
    config = LexiconAuthConfig(token="tk_disk_value", refresh_token="rt_disk_value")
    storage.save_credentials("https://lexicon.example", config)

    # The on-disk file must contain plaintext (otherwise the next
    # startup can't reuse the session).
    raw = storage.auth_file.read_text(encoding="utf-8")
    assert "tk_disk_value" in raw
    assert "rt_disk_value" in raw

    # Loading it back yields a config whose tokens are still usable.
    revived = FileLexiconStorage(tmp_path).load_credentials()
    assert revived is not None
    assert revived.token is not None
    assert revived.token.get_secret_value() == "tk_disk_value"
    assert revived.refresh_token is not None
    assert revived.refresh_token.get_secret_value() == "rt_disk_value"
