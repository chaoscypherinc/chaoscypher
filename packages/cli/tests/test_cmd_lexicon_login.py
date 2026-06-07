# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chaoscypher_cli.commands.lexicon.login (login/logout/whoami).

Tier 3: lexicon login state lives in ``auth.json`` (PathSettings.auth_file),
written/read through the core ``FileLexiconStorage`` — the CLI no longer
keeps a bespoke ``credentials.json`` helper set.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.lexicon.login import (
    _device_auth_flow,
    get_auth_config,
    get_lexicon_url,
    login,
    logout,
    whoami,
)
from chaoscypher_core.exceptions import ExternalServiceError
from chaoscypher_core.services.lexicon import AuthConfig, LexiconClientError
from chaoscypher_core.services.lexicon.client import DeviceCodeResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_creds_dir(tmp_path: Path) -> Path:
    """Return a tmp config dir; patch get_config_dir to point there."""
    d = tmp_path / "config"
    d.mkdir()
    return d


def _write_auth(config_dir: Path, **fields: object) -> Path:
    """Write an auth.json file with the given fields and return its path."""
    auth_file = config_dir / "auth.json"
    auth_file.write_text(json.dumps(fields), encoding="utf-8")
    return auth_file


# ---------------------------------------------------------------------------
# get_auth_config / get_lexicon_url (delegate to core FileLexiconStorage)
# ---------------------------------------------------------------------------


def test_get_auth_config_returns_none_when_no_creds(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = get_auth_config()
    assert result is None


def test_get_auth_config_returns_auth_when_creds_exist(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(
        config_dir,
        lexicon_url="https://hub.example.com",
        token="mytoken",
        refresh_token="myrefresh",
        username="bob",
    )
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        auth = get_auth_config()
    assert auth is not None
    # get_auth_config returns the client AuthConfig dataclass (plaintext token)
    assert isinstance(auth, AuthConfig)
    assert auth.token == "mytoken"
    assert auth.refresh_token == "myrefresh"
    assert auth.username == "bob"


def test_get_auth_config_returns_none_when_no_token(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(
        config_dir,
        lexicon_url="https://hub.example.com",
        token=None,
        username="bob",
    )
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = get_auth_config()
    assert result is None


def test_get_lexicon_url_returns_stored_url(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(config_dir, lexicon_url="https://custom.hub.com", token="t")
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        url = get_lexicon_url()
    assert url == "https://custom.hub.com"


def test_get_lexicon_url_falls_back_to_settings(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://default.lexicon.example.com"
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=mock_settings,
        ),
    ):
        url = get_lexicon_url()
    assert url == "https://default.lexicon.example.com"


# ---------------------------------------------------------------------------
# CLI: login command — token auth path
# ---------------------------------------------------------------------------


def test_login_token_auth_saves_credentials(tmp_path: Path) -> None:
    """--token skips device flow; saves credentials to auth.json and prints success."""
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
    ):
        result = runner.invoke(
            login,
            ["--token", "myapitoken", "--url", "https://lexicon.example.com"],
            input="alice\n",
        )

    assert result.exit_code == 0, result.output
    assert "alice" in result.output

    auth_file = config_dir / "auth.json"
    assert auth_file.exists()
    assert not (config_dir / "credentials.json").exists()
    saved = json.loads(auth_file.read_text())
    assert saved["token"] == "myapitoken"
    assert saved["username"] == "alice"


def test_login_token_auth_uses_default_url(tmp_path: Path) -> None:
    """With --token but no --url, falls back to settings URL."""
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://default.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
    ):
        result = runner.invoke(login, ["--token", "tok999"], input="bob\n")

    assert result.exit_code == 0, result.output
    saved = json.loads((config_dir / "auth.json").read_text())
    assert saved["lexicon_url"] == "https://default.example.com"


# ---------------------------------------------------------------------------
# CLI: login command — device auth flow (happy path)
# ---------------------------------------------------------------------------


def test_login_device_auth_happy_path(tmp_path: Path) -> None:
    """Device auth flow: asyncio.run returns AuthConfig, credentials saved to auth.json."""
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    returned_auth = AuthConfig(token="device_tok", username="carol", refresh_token="ref789")

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch("chaoscypher_cli.commands.lexicon.login.asyncio.run", return_value=returned_auth),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 0, result.output
    assert "carol" in result.output

    auth_file = config_dir / "auth.json"
    assert auth_file.exists()
    saved = json.loads(auth_file.read_text())
    assert saved["token"] == "device_tok"
    assert saved["username"] == "carol"


# ---------------------------------------------------------------------------
# CLI: login command — error branches
# ---------------------------------------------------------------------------


def test_login_device_auth_timeout_exits_nonzero(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=LexiconClientError(status_code=408, message="timed out"),
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "timed out" in result.output.lower() or "Authentication timed out" in result.output


def test_login_device_auth_denied_exits_nonzero(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=LexiconClientError(status_code=403, message="Access denied"),
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "denied" in result.output.lower()


def test_login_device_auth_expired_code_exits_nonzero(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=LexiconClientError(status_code=410, message="expired"),
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "expired" in result.output.lower()


def test_login_device_auth_generic_error_exits_nonzero(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=LexiconClientError(status_code=500, message="Internal error"),
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "Authentication failed" in result.output


def test_login_external_service_error_exits_nonzero(tmp_path: Path) -> None:
    """ExternalServiceError (hub unreachable) shows a helpful message."""
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=ExternalServiceError(service_name="Lexicon", reason="Connection refused"),
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "Cannot reach" in result.output or "Lexicon" in result.output


def test_login_keyboard_interrupt_exits_nonzero(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    mock_settings = MagicMock()
    mock_settings.lexicon.url = "https://lexicon.example.com"

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_core.app_config.get_settings", return_value=mock_settings),
        patch(
            "chaoscypher_cli.commands.lexicon.login.asyncio.run",
            side_effect=KeyboardInterrupt,
        ),
    ):
        result = runner.invoke(login, [])

    assert result.exit_code == 1
    assert "cancelled" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: logout command
# ---------------------------------------------------------------------------


def test_logout_when_logged_in(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(
        config_dir,
        lexicon_url="https://lexicon.example.com",
        token="tok",
        refresh_token=None,
        username="alice",
    )

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(logout)

    assert result.exit_code == 0, result.output
    assert "alice" in result.output
    assert not (config_dir / "auth.json").exists()


def test_logout_when_not_logged_in(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(logout)

    assert result.exit_code == 0, result.output
    assert "Not logged in" in result.output


def test_logout_unknown_username(tmp_path: Path) -> None:
    """Logout with no username field falls back to 'unknown'."""
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(config_dir, lexicon_url="https://lexicon.example.com", token="tok")

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(logout)

    assert result.exit_code == 0, result.output
    assert "unknown" in result.output


# ---------------------------------------------------------------------------
# CLI: whoami command
# ---------------------------------------------------------------------------


def test_whoami_when_logged_in(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(
        config_dir,
        lexicon_url="https://lexicon.example.com",
        token="tok",
        username="dave",
    )

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(whoami)

    assert result.exit_code == 0, result.output
    assert "dave" in result.output
    assert "lexicon.example.com" in result.output


def test_whoami_when_not_logged_in(tmp_path: Path) -> None:
    config_dir = _make_creds_dir(tmp_path)

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(whoami)

    assert result.exit_code == 0, result.output
    assert "Not logged in" in result.output
    assert "chaoscypher login" in result.output


# ---------------------------------------------------------------------------
# Stale credentials.json notice (pre-unification leftover)
# ---------------------------------------------------------------------------


def test_whoami_stale_credentials_notice(tmp_path: Path) -> None:
    """A leftover credentials.json (no auth.json) prints a re-login notice."""
    config_dir = _make_creds_dir(tmp_path)
    (config_dir / "credentials.json").write_text(
        json.dumps({"token": "stale", "username": "old"}), encoding="utf-8"
    )

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(whoami)

    assert result.exit_code == 0, result.output
    assert "credentials.json" in result.output
    assert "chaoscypher lexicon login" in result.output
    # The stale file is NOT read as a live session.
    assert "Not logged in" in result.output


def test_no_stale_notice_when_authenticated(tmp_path: Path) -> None:
    """With a valid auth.json present, no stale notice fires even if the old file exists."""
    config_dir = _make_creds_dir(tmp_path)
    _write_auth(config_dir, lexicon_url="https://lexicon.example.com", token="tok", username="dave")
    (config_dir / "credentials.json").write_text("{}", encoding="utf-8")

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir):
        result = runner.invoke(whoami)

    assert result.exit_code == 0, result.output
    assert "dave" in result.output
    assert "stale" not in result.output.lower()


# ---------------------------------------------------------------------------
# _device_auth_flow direct tests
# ---------------------------------------------------------------------------


def _make_device_response(
    *, complete_uri: str | None = "https://hub.example.com/auth?code=XYZ"
) -> DeviceCodeResponse:
    return DeviceCodeResponse(
        device_code="DEVCODE",
        user_code="USRCODE",
        verification_uri="https://hub.example.com/auth",
        verification_uri_complete=complete_uri,
        expires_in=300,
        interval=5,
    )


async def test_device_auth_flow_with_complete_uri_browser_opens() -> None:
    """_device_auth_flow: complete URI + browser confirm=True → webbrowser.open called."""
    device = _make_device_response()
    returned_auth = AuthConfig(token="flowtoken", username="eve")

    mock_client = AsyncMock()
    mock_client.request_device_code = AsyncMock(return_value=device)
    mock_client.poll_device_token = AsyncMock(return_value=returned_auth)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("chaoscypher_cli.commands.lexicon.login.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.login.Confirm.ask", return_value=True),
        patch("chaoscypher_cli.commands.lexicon.login.webbrowser.open") as mock_wb,
    ):
        result = await _device_auth_flow("https://hub.example.com", no_browser=False)

    assert result.token == "flowtoken"
    mock_wb.assert_called_once()


async def test_device_auth_flow_with_no_browser_flag() -> None:
    """_device_auth_flow: no_browser=True skips the Confirm prompt."""
    device = _make_device_response()
    returned_auth = AuthConfig(token="flowtoken2", username="frank")

    mock_client = AsyncMock()
    mock_client.request_device_code = AsyncMock(return_value=device)
    mock_client.poll_device_token = AsyncMock(return_value=returned_auth)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("chaoscypher_cli.commands.lexicon.login.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.login.webbrowser.open") as mock_wb,
    ):
        result = await _device_auth_flow("https://hub.example.com", no_browser=True)

    assert result.token == "flowtoken2"
    mock_wb.assert_not_called()


async def test_device_auth_flow_without_complete_uri_shows_user_code() -> None:
    """_device_auth_flow: no complete URI → shows user_code in panel."""
    device = _make_device_response(complete_uri=None)
    returned_auth = AuthConfig(token="flowtoken3", username="grace")

    mock_client = AsyncMock()
    mock_client.request_device_code = AsyncMock(return_value=device)
    mock_client.poll_device_token = AsyncMock(return_value=returned_auth)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("chaoscypher_cli.commands.lexicon.login.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.login.Confirm.ask", return_value=False),
    ):
        result = await _device_auth_flow("https://hub.example.com", no_browser=False)

    assert result.token == "flowtoken3"


async def test_device_auth_flow_confirm_raises_exception_caught() -> None:
    """_device_auth_flow: Confirm.ask raising Exception is caught gracefully."""
    device = _make_device_response()
    returned_auth = AuthConfig(token="flowtoken4", username="hank")

    mock_client = AsyncMock()
    mock_client.request_device_code = AsyncMock(return_value=device)
    mock_client.poll_device_token = AsyncMock(return_value=returned_auth)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("chaoscypher_cli.commands.lexicon.login.LexiconClient", return_value=mock_client),
        patch(
            "chaoscypher_cli.commands.lexicon.login.Confirm.ask",
            side_effect=Exception("terminal unavailable"),
        ),
    ):
        result = await _device_auth_flow("https://hub.example.com", no_browser=False)

    # Exception is swallowed; auth still returned from poll
    assert result.token == "flowtoken4"
