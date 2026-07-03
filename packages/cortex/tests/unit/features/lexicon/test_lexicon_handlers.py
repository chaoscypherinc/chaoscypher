# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for lexicon API handler logic.

Verifies that each handler calls the correct LexiconService method with the
correct arguments and handles LexiconClientError conversions correctly.
FastAPI DI is bypassed — the service mock is passed directly as a function
argument.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_core.services.lexicon import (
    LexiconClientError,
    LexiconDeviceCodeRequest,
    LexiconDeviceCodeResponse,
    LexiconLoginRequest,
    LexiconPollRequest,
    LexiconTokenRequest,
)
from chaoscypher_cortex.features.lexicon.api import (
    get_auth_status,
    get_package_info,
    login,
    logout,
    poll_device_token,
    request_device_code,
    search_packages,
    set_token,
    upload_package,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_response(success: bool = True) -> dict:
    """Return a minimal auth response dict."""
    return {
        "success": success,
        "username": "alice",
        "lexicon_url": "http://localhost:3001",
        "message": "Authenticated",
    }


def _package_info(owner: str = "alice", name: str = "medical") -> MagicMock:
    """Return a mock LexiconPackageInfo object."""
    info = MagicMock()
    info.owner = owner
    info.name = name
    return info


def _make_lexicon_error(status_code: int, message: str = "Error") -> LexiconClientError:
    """Construct a LexiconClientError with given status code."""
    return LexiconClientError(
        status_code=status_code,
        message=message,
        details={"extra": "info"},
    )


def _upload_settings(max_upload_bytes: int = 100 * 1024 * 1024) -> MagicMock:
    """Return a Settings stub exposing settings.batching.max_upload_bytes."""
    settings = MagicMock()
    settings.batching.max_upload_bytes = max_upload_bytes
    return settings


# ---------------------------------------------------------------------------
# TestRequestDeviceCode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestDeviceCode:
    """Tests for the request_device_code handler."""

    @pytest.mark.asyncio
    async def test_returns_device_code_response(self) -> None:
        """Handler awaits service.request_device_code and returns the result."""
        mock_service = AsyncMock()
        response = LexiconDeviceCodeResponse(
            device_code="dcode-123",
            user_code="ABC-XYZ",
            verification_uri="https://lexicon.io/activate",
            expires_in=900,
            interval=5,
        )
        mock_service.request_device_code.return_value = response

        request = LexiconDeviceCodeRequest()

        result = await request_device_code(_="test-user", request=request, service=mock_service)

        mock_service.request_device_code.assert_awaited_once_with(request)
        assert result.device_code == "dcode-123"
        assert result.user_code == "ABC-XYZ"

    @pytest.mark.asyncio
    async def test_converts_401_error_to_http_401(self) -> None:
        """Handler converts LexiconClientError(401) to HTTPException(401)."""
        mock_service = AsyncMock()
        mock_service.request_device_code.side_effect = _make_lexicon_error(401, "Unauthorized")

        with pytest.raises(HTTPException) as exc_info:
            await request_device_code(
                _="test-user",
                request=LexiconDeviceCodeRequest(),
                service=mock_service,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_converts_404_error_to_http_404(self) -> None:
        """Handler converts LexiconClientError(404) to HTTPException(404)."""
        mock_service = AsyncMock()
        mock_service.request_device_code.side_effect = _make_lexicon_error(404, "Not found")

        with pytest.raises(HTTPException) as exc_info:
            await request_device_code(
                _="test-user",
                request=LexiconDeviceCodeRequest(),
                service=mock_service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_converts_unknown_error_to_503(self) -> None:
        """Handler maps unknown status codes to HTTPException(503)."""
        mock_service = AsyncMock()
        mock_service.request_device_code.side_effect = _make_lexicon_error(500, "Server error")

        with pytest.raises(HTTPException) as exc_info:
            await request_device_code(
                _="test-user",
                request=LexiconDeviceCodeRequest(),
                service=mock_service,
            )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_converts_410_error_to_408(self) -> None:
        """Handler maps status 410 (Gone) to HTTPException(408) Request Timeout."""
        mock_service = AsyncMock()
        mock_service.request_device_code.side_effect = _make_lexicon_error(410, "Code expired")

        with pytest.raises(HTTPException) as exc_info:
            await request_device_code(
                _="test-user",
                request=LexiconDeviceCodeRequest(),
                service=mock_service,
            )

        assert exc_info.value.status_code == 408


# ---------------------------------------------------------------------------
# TestPollDeviceToken
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPollDeviceToken:
    """Tests for the poll_device_token handler."""

    @pytest.mark.asyncio
    async def test_returns_auth_response(self) -> None:
        """Handler awaits service.poll_device_token and returns the result."""
        mock_service = AsyncMock()
        mock_service.poll_device_token.return_value = MagicMock(**_auth_response())

        request = LexiconPollRequest(device_code="dcode-123")

        result = await poll_device_token(_="test-user", request=request, service=mock_service)

        mock_service.poll_device_token.assert_awaited_once_with(request)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_converts_408_error(self) -> None:
        """Handler converts LexiconClientError(408) to HTTPException(408)."""
        mock_service = AsyncMock()
        mock_service.poll_device_token.side_effect = _make_lexicon_error(408, "Timeout")

        with pytest.raises(HTTPException) as exc_info:
            await poll_device_token(
                _="test-user",
                request=LexiconPollRequest(device_code="expired"),
                service=mock_service,
            )

        assert exc_info.value.status_code == 408

    @pytest.mark.asyncio
    async def test_converts_403_error(self) -> None:
        """Handler converts LexiconClientError(403) to HTTPException(403)."""
        mock_service = AsyncMock()
        mock_service.poll_device_token.side_effect = _make_lexicon_error(403, "Forbidden")

        with pytest.raises(HTTPException) as exc_info:
            await poll_device_token(
                _="test-user",
                request=LexiconPollRequest(device_code="code"),
                service=mock_service,
            )

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# TestLogin
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogin:
    """Tests for the login handler."""

    @pytest.mark.asyncio
    async def test_returns_auth_response_on_success(self) -> None:
        """Handler awaits service.login and returns the auth response."""
        mock_service = AsyncMock()
        mock_service.login.return_value = MagicMock(**_auth_response())

        request = LexiconLoginRequest(username="alice", password="secret")

        result = await login(_="test-user", request=request, service=mock_service)

        mock_service.login.assert_awaited_once_with(request)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_converts_401_to_http_401(self) -> None:
        """Handler converts LexiconClientError(401) to HTTPException(401)."""
        mock_service = AsyncMock()
        mock_service.login.side_effect = _make_lexicon_error(401, "Bad credentials")

        with pytest.raises(HTTPException) as exc_info:
            await login(
                _="test-user",
                request=LexiconLoginRequest(username="bad", password="pass"),
                service=mock_service,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_includes_error_detail(self) -> None:
        """HTTPException detail uses the sanitized envelope (no upstream leakage)."""
        mock_service = AsyncMock()
        mock_service.login.side_effect = LexiconClientError(
            status_code=401,
            message="Invalid credentials",
            details={"hint": "Check your password"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await login(
                _="test-user",
                request=LexiconLoginRequest(username="x", password="y"),
                service=mock_service,
            )

        # Production sanitizes the body — upstream message/details must NOT leak.
        assert exc_info.value.detail["code"] == "LEXICON_UPSTREAM_ERROR"
        assert exc_info.value.detail["message"] == "Upstream lexicon request failed"
        assert "Invalid credentials" not in exc_info.value.detail["message"]


# ---------------------------------------------------------------------------
# TestSetToken
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetToken:
    """Tests for the set_token handler."""

    @pytest.mark.asyncio
    async def test_calls_sync_set_token_and_returns_result(self) -> None:
        """Handler calls synchronous service.set_token and returns the result."""
        mock_service = MagicMock()
        mock_service.set_token.return_value = MagicMock(**_auth_response())

        request = LexiconTokenRequest(token="jwt-abc-123")

        result = await set_token(_="test-user", request=request, service=mock_service)

        mock_service.set_token.assert_called_once_with(request)
        assert result.success is True


# ---------------------------------------------------------------------------
# TestLogout
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogout:
    """Tests for the logout handler."""

    @pytest.mark.asyncio
    async def test_calls_sync_logout_and_returns_result(self) -> None:
        """Handler calls synchronous service.logout and returns the result."""
        mock_service = MagicMock()
        logout_response = MagicMock()
        logout_response.success = True
        mock_service.logout.return_value = logout_response

        result = await logout(_="test-user", service=mock_service)

        mock_service.logout.assert_called_once_with()
        assert result.success is True


# ---------------------------------------------------------------------------
# TestGetAuthStatus
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAuthStatus:
    """Tests for the get_auth_status handler."""

    @pytest.mark.asyncio
    async def test_returns_auth_status(self) -> None:
        """Handler calls synchronous service.get_auth_status and returns the result."""
        mock_service = MagicMock()
        status_obj = MagicMock()
        status_obj.authenticated = True
        status_obj.username = "alice"
        mock_service.get_auth_status.return_value = status_obj

        result = await get_auth_status(_="test-user", service=mock_service)

        mock_service.get_auth_status.assert_called_once_with()
        assert result.authenticated is True
        assert result.username == "alice"


# ---------------------------------------------------------------------------
# TestSearchPackages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchPackages:
    """Tests for the search_packages handler."""

    @pytest.mark.asyncio
    async def test_builds_request_and_calls_service(self) -> None:
        """Handler builds LexiconSearchRequest from params and awaits service.search."""
        mock_service = AsyncMock()
        search_response = MagicMock()
        search_response.packages = []
        mock_service.search.return_value = search_response

        await search_packages(
            _="test-user",
            service=mock_service,
            limit=20,
            query="medical",
            page=1,
            sort_by="downloads",
            is_public=True,
            owner_id=None,
            conformance_class=None,
        )

        mock_service.search.assert_awaited_once()
        call_args = mock_service.search.call_args[0][0]
        assert call_args.query == "medical"
        assert call_args.limit == 20
        assert call_args.page == 1
        assert call_args.sort_by == "downloads"
        assert call_args.is_public is True

    @pytest.mark.asyncio
    async def test_passes_owner_id_and_conformance_class(self) -> None:
        """Handler forwards owner_id and conformance_class to the search request."""
        mock_service = AsyncMock()
        mock_service.search.return_value = MagicMock()

        await search_packages(
            _="test-user",
            service=mock_service,
            limit=10,
            query="",
            page=2,
            sort_by="newest",
            is_public=None,
            owner_id="alice-id",
            conformance_class="ccx-core",
        )

        call_args = mock_service.search.call_args[0][0]
        assert call_args.owner_id == "alice-id"
        assert call_args.conformance_class == "ccx-core"
        assert call_args.page == 2

    @pytest.mark.asyncio
    async def test_converts_error_to_503(self) -> None:
        """Handler converts unknown LexiconClientError to HTTPException(503)."""
        mock_service = AsyncMock()
        mock_service.search.side_effect = _make_lexicon_error(502, "Gateway error")

        with pytest.raises(HTTPException) as exc_info:
            await search_packages(
                _="test-user",
                service=mock_service,
                limit=10,
                query="test",
                page=1,
                sort_by="downloads",
                is_public=None,
                owner_id=None,
                conformance_class=None,
            )

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_converts_404_to_http_404(self) -> None:
        """Handler converts LexiconClientError(404) to HTTPException(404)."""
        mock_service = AsyncMock()
        mock_service.search.side_effect = _make_lexicon_error(404, "Not found")

        with pytest.raises(HTTPException) as exc_info:
            await search_packages(
                _="test-user",
                service=mock_service,
                limit=10,
                query="missing",
                page=1,
                sort_by="downloads",
                is_public=None,
                owner_id=None,
                conformance_class=None,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestGetPackageInfo
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPackageInfo:
    """Tests for the get_package_info handler."""

    @pytest.mark.asyncio
    async def test_returns_package_info(self) -> None:
        """Handler awaits service.get_package_info(owner, name) and returns result."""
        mock_service = AsyncMock()
        pkg = _package_info("alice", "medical-kg")
        mock_service.get_package_info.return_value = pkg

        result = await get_package_info(
            owner="alice",
            name="medical-kg",
            _="test-user",
            service=mock_service,
        )

        mock_service.get_package_info.assert_awaited_once_with("alice", "medical-kg")
        assert result.owner == "alice"
        assert result.name == "medical-kg"

    @pytest.mark.asyncio
    async def test_converts_404_to_http_404(self) -> None:
        """Handler converts LexiconClientError(404) to HTTPException(404)."""
        mock_service = AsyncMock()
        mock_service.get_package_info.side_effect = _make_lexicon_error(404, "Package not found")

        with pytest.raises(HTTPException) as exc_info:
            await get_package_info(
                owner="alice",
                name="nonexistent",
                _="test-user",
                service=mock_service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_converts_403_to_http_403(self) -> None:
        """Handler converts LexiconClientError(403) to HTTPException(403)."""
        mock_service = AsyncMock()
        mock_service.get_package_info.side_effect = _make_lexicon_error(403, "Forbidden")

        with pytest.raises(HTTPException) as exc_info:
            await get_package_info(
                owner="alice",
                name="private-pkg",
                _="test-user",
                service=mock_service,
            )

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# TestLexiconUrlSsrfGuard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLexiconUrlSsrfGuard:
    """device/poll/login reject SSRF-unsafe ``lexicon_url`` overrides.

    These endpoints build a ``LexiconClient`` against ``request.lexicon_url``
    and make outbound HTTP calls, so an attacker-supplied URL (cloud-metadata
    IP, loopback service, non-HTTP scheme) is an SSRF vector. The
    operator-configured default is trusted and must reach the service
    untouched (no DNS lookup).
    """

    @pytest.mark.asyncio
    async def test_device_code_rejects_metadata_ip(self) -> None:
        mock_service = AsyncMock()
        request = LexiconDeviceCodeRequest(lexicon_url="http://169.254.169.254/latest/meta-data/")

        with pytest.raises(HTTPException) as exc_info:
            await request_device_code(_="test-user", request=request, service=mock_service)

        assert exc_info.value.status_code == 400
        mock_service.request_device_code.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_poll_rejects_loopback(self) -> None:
        mock_service = AsyncMock()
        request = LexiconPollRequest(device_code="c", lexicon_url="http://127.0.0.1:8000")

        with pytest.raises(HTTPException) as exc_info:
            await poll_device_token(_="test-user", request=request, service=mock_service)

        assert exc_info.value.status_code == 400
        mock_service.poll_device_token.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_login_rejects_non_http_scheme(self) -> None:
        mock_service = AsyncMock()
        request = LexiconLoginRequest(username="a", password="b", lexicon_url="file:///etc/passwd")

        with pytest.raises(HTTPException) as exc_info:
            await login(_="test-user", request=request, service=mock_service)

        assert exc_info.value.status_code == 400
        mock_service.login.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trusted_default_url_reaches_service(self) -> None:
        """The configured default is trusted — not re-validated, no DNS lookup."""
        mock_service = AsyncMock()
        mock_service.request_device_code.return_value = LexiconDeviceCodeResponse(
            device_code="d",
            user_code="u",
            verification_uri="https://x",
            expires_in=900,
            interval=5,
        )

        # Default lexicon_url (operator config) — must reach the service.
        await request_device_code(
            _="test-user", request=LexiconDeviceCodeRequest(), service=mock_service
        )

        mock_service.request_device_code.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestUploadPackage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUploadPackage:
    """Tests for the upload_package handler."""

    @pytest.mark.asyncio
    async def test_reads_file_builds_request_and_calls_service(self) -> None:
        """Handler reads file content, builds LexiconUploadRequest, and calls service.upload."""
        mock_service = AsyncMock()
        pkg = _package_info("alice", "new-pkg")
        mock_service.upload.return_value = pkg

        mock_file = AsyncMock()
        mock_file.read.return_value = b"archive-content"
        mock_file.filename = "pkg.ccx"

        result = await upload_package(
            _="test-user",
            service=mock_service,
            settings=_upload_settings(),
            file=mock_file,
            public=True,
            message="First upload",
        )

        mock_file.read.assert_awaited_once()
        mock_service.upload.assert_awaited_once()

        call_args = mock_service.upload.call_args
        assert call_args[0][0] == b"archive-content"
        upload_request = call_args[0][1]
        assert upload_request.public is True
        assert upload_request.message == "First upload"

        assert result.owner == "alice"

    @pytest.mark.asyncio
    async def test_passes_public_false(self) -> None:
        """Handler respects public=False in the upload request."""
        mock_service = AsyncMock()
        mock_service.upload.return_value = _package_info()

        mock_file = AsyncMock()
        mock_file.read.return_value = b"data"
        mock_file.filename = "pkg.ccx"

        await upload_package(
            _="test-user",
            service=mock_service,
            settings=_upload_settings(),
            file=mock_file,
            public=False,
            message=None,
        )

        upload_request = mock_service.upload.call_args[0][1]
        assert upload_request.public is False
        assert upload_request.message is None

    @pytest.mark.asyncio
    async def test_converts_401_to_http_401(self) -> None:
        """Handler converts LexiconClientError(401) to HTTPException(401) on upload."""
        mock_service = AsyncMock()
        mock_service.upload.side_effect = _make_lexicon_error(401, "Not authenticated")

        mock_file = AsyncMock()
        mock_file.read.return_value = b"data"
        mock_file.filename = "pkg.ccx"

        with pytest.raises(HTTPException) as exc_info:
            await upload_package(
                _="test-user",
                service=mock_service,
                settings=_upload_settings(),
                file=mock_file,
                public=True,
                message=None,
            )

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_converts_unknown_error_to_503(self) -> None:
        """Handler converts unrecognized status code to HTTPException(503)."""
        mock_service = AsyncMock()
        mock_service.upload.side_effect = _make_lexicon_error(500, "Internal server error")

        mock_file = AsyncMock()
        mock_file.read.return_value = b"data"
        mock_file.filename = "pkg.ccx"

        with pytest.raises(HTTPException) as exc_info:
            await upload_package(
                _="test-user",
                service=mock_service,
                settings=_upload_settings(),
                file=mock_file,
                public=True,
                message=None,
            )

        assert exc_info.value.status_code == 503
