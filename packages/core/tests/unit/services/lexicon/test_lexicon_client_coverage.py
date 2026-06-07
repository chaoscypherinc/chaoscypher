# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage tests for ``services/lexicon/client.py``.

Covers AuthConfig persistence round-trips, URL normalization, header
construction, the ``_request`` retry/error matrix, the device-authorization
flow, and the package search/download/upload helpers.

All HTTP is faked: no live network is touched. The httpx ``AsyncClient``
cached on the client instance is replaced with an ``AsyncMock`` whose
``.request`` / ``.get`` / ``.post`` coroutines return ``MagicMock`` objects
shaped like ``httpx.Response`` (``.status_code``, ``.json()``, ``.content``,
``.reason_phrase``, ``.headers``, ``.text``).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from chaoscypher_core.exceptions import ExternalServiceError
from chaoscypher_core.services.lexicon.client import (
    AuthConfig,
    DeviceCodeResponse,
    LexiconClient,
    LexiconClientError,
    PackageInfo,
)


# ---------------------------------------------------------------------------
# Helpers (copied locally — no sibling test imports allowed)
# ---------------------------------------------------------------------------


def make_response(
    *,
    status_code: int = 200,
    json_data: Any = None,
    content: bytes | None = None,
    reason_phrase: str = "OK",
    headers: dict[str, str] | None = None,
    text: str | None = None,
    raise_json: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock shaped like an ``httpx.Response``.

    Args:
        status_code: HTTP status code.
        json_data: Value returned by ``.json()`` (ignored if ``raise_json``).
        content: Raw body bytes. Defaults to a non-empty sentinel unless a
            status of 204 is requested (then empty).
        reason_phrase: HTTP reason phrase.
        headers: Response headers mapping.
        text: ``.text`` value. Defaults to the JSON serialization.
        raise_json: When set, ``.json()`` raises this instead of returning.
    """
    resp = MagicMock(name="Response")
    resp.status_code = status_code
    resp.reason_phrase = reason_phrase
    resp.headers = headers or {"content-type": "application/json"}

    if content is None:
        content = b"" if status_code == 204 else b'{"ok": true}'
    resp.content = content

    if text is None:
        text = (
            json.dumps(json_data) if json_data is not None else content.decode("utf-8", "replace")
        )
    resp.text = text

    if raise_json is not None:
        resp.json.side_effect = raise_json
    else:
        resp.json.return_value = json_data if json_data is not None else {}

    resp.raise_for_status = MagicMock()
    return resp


def install_fake_client(client: LexiconClient) -> AsyncMock:
    """Inject an AsyncMock as the cached general httpx client.

    Returns the mock so tests can stub ``.request`` / set side_effects.
    """
    fake = AsyncMock(name="httpx.AsyncClient")
    client._client = fake
    return fake


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthConfig:
    def test_save_load_clear_round_trip(self, tmp_path) -> None:
        auth_file = tmp_path / "nested" / "auth.json"
        cfg = AuthConfig(
            token="jwt-123",
            refresh_token="refresh-456",
            expires_at="2030-01-01T00:00:00Z",
            username="alice",
        )

        # save creates parent dirs and writes JSON
        cfg.save(auth_file)
        assert auth_file.exists()
        on_disk = json.loads(auth_file.read_text())
        assert on_disk["token"] == "jwt-123"
        assert on_disk["username"] == "alice"

        # load reconstitutes every field
        loaded = AuthConfig.load(auth_file)
        assert loaded.token == "jwt-123"
        assert loaded.refresh_token == "refresh-456"
        assert loaded.expires_at == "2030-01-01T00:00:00Z"
        assert loaded.username == "alice"

        # clear removes the file and resets in-memory fields
        cfg.clear(auth_file)
        assert not auth_file.exists()
        assert cfg.token is None
        assert cfg.refresh_token is None
        assert cfg.expires_at is None
        assert cfg.username is None

    def test_load_missing_file_returns_empty(self, tmp_path) -> None:
        missing = tmp_path / "does-not-exist.json"
        cfg = AuthConfig.load(missing)
        assert cfg.token is None
        assert cfg.username is None
        assert cfg.is_authenticated is False

    def test_load_corrupt_json_returns_empty(self, tmp_path) -> None:
        bad = tmp_path / "auth.json"
        bad.write_text("{ this is not valid json ")
        # The except (JSONDecodeError, OSError) tuple guard swallows the error.
        cfg = AuthConfig.load(bad)
        assert cfg.token is None
        assert cfg.is_authenticated is False

    def test_clear_when_file_absent_still_resets_fields(self, tmp_path) -> None:
        missing = tmp_path / "auth.json"
        cfg = AuthConfig(token="x", username="bob")
        cfg.clear(missing)  # must not raise even though file is absent
        assert cfg.token is None
        assert cfg.username is None

    def test_is_authenticated_true_and_false(self) -> None:
        assert AuthConfig(token="abc").is_authenticated is True
        assert AuthConfig().is_authenticated is False
        assert AuthConfig(token=None).is_authenticated is False


# ---------------------------------------------------------------------------
# URL normalization & headers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUrlNormalizationAndHeaders:
    def test_appends_api_path_when_missing(self) -> None:
        client = LexiconClient(base_url="https://lexicon.example.com")
        assert client.base_url == "https://lexicon.example.com/api/v1"

    def test_strips_trailing_slash_then_appends(self) -> None:
        client = LexiconClient(base_url="https://lexicon.example.com/")
        assert client.base_url == "https://lexicon.example.com/api/v1"

    def test_idempotent_when_api_path_already_present(self) -> None:
        client = LexiconClient(base_url="https://lexicon.example.com/api/v1")
        assert client.base_url == "https://lexicon.example.com/api/v1"

    def test_headers_without_token_has_no_authorization(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig())
        headers = client._get_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"
        assert headers["User-Agent"] == "chaoscypher/1.0"

    def test_headers_with_token_adds_bearer(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="tok-9"))
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer tok-9"


# ---------------------------------------------------------------------------
# _request matrix
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequest:
    @pytest.mark.asyncio
    async def test_happy_path_returns_json(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(json_data={"hello": "world"})

        result = await client._request("GET", "/ping")
        assert result == {"hello": "world"}
        # URL is base_url + endpoint
        called_url = fake.request.call_args.args[1]
        assert called_url == "http://x/api/v1/ping"

    @pytest.mark.asyncio
    async def test_http_400_raises_lexicon_client_error_with_message(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=400,
            json_data={"message": "bad request", "details": {"field": "q"}},
            reason_phrase="Bad Request",
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client._request("GET", "/boom")
        exc = exc_info.value
        assert exc.status_code == 400
        assert exc.message == "bad request"
        assert exc.details.get("field") == "q"

    @pytest.mark.asyncio
    async def test_http_error_falls_back_to_reason_phrase_when_json_fails(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=500,
            raise_json=ValueError("no json"),
            reason_phrase="Internal Server Error",
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client._request("GET", "/boom")
        assert exc_info.value.message == "Internal Server Error"
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_204_returns_empty_dict(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(status_code=204, content=b"")

        result = await client._request("DELETE", "/thing")
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_body_raises_lexicon_client_error(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(status_code=200, content=b"")

        with pytest.raises(LexiconClientError) as exc_info:
            await client._request("GET", "/thing")
        assert "Empty response" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_invalid_json_raises_lexicon_client_error(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=200,
            content=b"<html>not json</html>",
            raise_json=ValueError("Expecting value"),
            text="<html>not json</html>",
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client._request("GET", "/thing")
        assert "Invalid JSON" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_connect_error_wrapped_as_external_service_503(self) -> None:
        client = LexiconClient(base_url="http://x", max_retries=4)
        fake = install_fake_client(client)
        fake.request.side_effect = httpx.ConnectError("refused")

        with pytest.raises(ExternalServiceError) as exc_info:
            await client._request("GET", "/thing")
        assert "unreachable" in exc_info.value.message.lower()
        assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
        # ConnectError short-circuits — only one attempt
        assert fake.request.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self, monkeypatch) -> None:
        client = LexiconClient(
            base_url="http://x",
            max_retries=4,
            retry_backoff=(0.01, 0.01, 0.01, 0.01),
        )
        fake = install_fake_client(client)
        # First two attempts: transient read timeout; third: success.
        fake.request.side_effect = [
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            make_response(json_data={"recovered": True}),
        ]

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("chaoscypher_core.services.lexicon.client.asyncio.sleep", fake_sleep)

        result = await client._request("GET", "/thing")
        assert result == {"recovered": True}
        assert fake.request.await_count == 3
        assert sleeps == [0.01, 0.01]  # backoff slept before attempts 2 and 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_external_service_error(self, monkeypatch) -> None:
        client = LexiconClient(
            base_url="http://x",
            max_retries=2,
            retry_backoff=(0.01, 0.01),
        )
        fake = install_fake_client(client)
        fake.request.side_effect = httpx.ReadTimeout("always slow")

        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr("chaoscypher_core.services.lexicon.client.asyncio.sleep", fake_sleep)

        with pytest.raises(ExternalServiceError) as exc_info:
            await client._request("GET", "/thing")
        assert "Lexicon unreachable" in exc_info.value.message
        assert fake.request.await_count == 2

    @pytest.mark.asyncio
    async def test_get_general_client_lazily_created_when_no_context(self) -> None:
        # No __aenter__ used → _client is None; make_request must lazily build one.
        client = LexiconClient(base_url="http://x")
        assert client._client is None
        # Patch the AsyncClient class so no real socket is created.
        built = AsyncMock(name="lazy-client")
        built.request.return_value = make_response(json_data={"lazy": 1})
        import chaoscypher_core.services.lexicon.client as mod

        original = mod.httpx.AsyncClient
        mod.httpx.AsyncClient = MagicMock(return_value=built)  # type: ignore[assignment]
        try:
            result = await client._request("GET", "/thing")
        finally:
            mod.httpx.AsyncClient = original  # type: ignore[assignment]
        assert result == {"lazy": 1}
        assert client._client is built


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit_lifecycle(self, monkeypatch) -> None:
        import chaoscypher_core.services.lexicon.client as mod

        fake = AsyncMock(name="ctx-client")
        monkeypatch.setattr(mod.httpx, "AsyncClient", MagicMock(return_value=fake))

        client = LexiconClient(base_url="http://x")
        async with client as entered:
            assert entered is client
            assert client._client is fake
        # On exit the client is closed and cleared.
        fake.aclose.assert_awaited_once()
        assert client._client is None


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogin:
    @pytest.mark.asyncio
    async def test_login_populates_auth(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            json_data={
                "token": "jwt-login",
                "refresh_token": "refresh-login",
                "expires_at": "2030-01-01",
            }
        )

        auth = await client.login("alice", "hunter2")
        assert auth.token == "jwt-login"
        assert auth.refresh_token == "refresh-login"
        assert auth.username == "alice"
        assert client.auth.token == "jwt-login"

    def test_logout_clears_auth(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="t"))
        client.logout()
        assert client.auth.is_authenticated is False


# ---------------------------------------------------------------------------
# Device authorization flow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeviceFlow:
    @pytest.mark.asyncio
    async def test_request_device_code(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            json_data={
                "device_code": "dev-1",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://lexicon.example.com/device",
                "verification_uri_complete": "https://lexicon.example.com/device?code=WXYZ-1234",
                "expires_in": 600,
                "interval": 3,
            }
        )

        dc = await client.request_device_code()
        assert isinstance(dc, DeviceCodeResponse)
        assert dc.device_code == "dev-1"
        assert dc.user_code == "WXYZ-1234"
        assert dc.expires_in == 600
        assert dc.interval == 3

    @pytest.mark.asyncio
    async def test_poll_pending_then_slow_down_then_success(self, monkeypatch) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)

        pending = make_response(
            status_code=400,
            json_data={"message": "pending", "details": {"error": "authorization_pending"}},
        )
        slow = make_response(
            status_code=400,
            json_data={"message": "slow", "details": {"error": "slow_down"}},
        )
        success = make_response(
            json_data={
                "access_token": "access-final",
                "refresh_token": "refresh-final",
                "expires_at": "2030-01-01",
                "username": "carol",
            }
        )
        fake.request.side_effect = [pending, slow, success]

        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("chaoscypher_core.services.lexicon.client.asyncio.sleep", fake_sleep)

        pending_calls = {"n": 0}

        def on_pending() -> None:
            pending_calls["n"] += 1

        auth = await client.poll_device_token("dev-1", interval=5.0, on_pending=on_pending)
        assert auth.token == "access-final"
        assert auth.username == "carol"
        assert pending_calls["n"] == 1
        # interval bumped by 5 for slow_down
        assert sleeps == [5.0, 10.0]

    @pytest.mark.asyncio
    async def test_poll_expired_token_raises_410(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=400,
            json_data={"message": "expired", "details": {"error": "expired_token"}},
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client.poll_device_token("dev-1")
        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_poll_access_denied_raises_403(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=400,
            json_data={"message": "denied", "details": {"error": "access_denied"}},
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client.poll_device_token("dev-1")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_poll_timeout_raises_408(self, monkeypatch) -> None:
        client = LexiconClient(base_url="http://x")
        # Drive time forward past max_time immediately.
        times = iter([1000.0, 1000.0 + 99999.0])
        monkeypatch.setattr("time.time", lambda: next(times))

        with pytest.raises(LexiconClientError) as exc_info:
            await client.poll_device_token("dev-1", timeout=1.0)
        assert exc_info.value.status_code == 408

    @pytest.mark.asyncio
    async def test_poll_unknown_error_reraises(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            status_code=400,
            json_data={"message": "weird", "details": {"error": "unknown_thing"}},
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client.poll_device_token("dev-1")
        # Original 400 is re-raised unchanged.
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearch:
    @pytest.mark.asyncio
    async def test_search_builds_params_and_parses_packages(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            json_data={
                "data": {
                    "hits": [
                        {
                            "id": "r1",
                            "name": "medical",
                            "ownerUsername": "alice",
                            "ownerName": "Alice A",
                            "starCount": 5,
                            "downloadCount": 100,
                        }
                    ],
                    "total": 1,
                }
            }
        )

        packages, total = await client.search(
            "medical",
            page=2,
            limit=50,
            sort_by="stars",
            is_public=True,
            owner_id="owner-9",
            package_type="TEMPLATES",
        )

        assert total == 1
        assert len(packages) == 1
        pkg = packages[0]
        assert isinstance(pkg, PackageInfo)
        assert pkg.name == "medical"
        assert pkg.owner_username == "alice"
        assert pkg.full_name == "alice/medical"
        assert pkg.star_count == 5

        # Verify the optional params were threaded into the query.
        sent_params = fake.request.call_args.kwargs["params"]
        assert sent_params["q"] == "medical"
        assert sent_params["type"] == "repositories"
        assert sent_params["page"] == 2
        assert sent_params["limit"] == 50
        assert sent_params["sort"] == "stars"
        assert sent_params["isPublic"] == "true"
        assert sent_params["ownerId"] == "owner-9"
        assert sent_params["packageType"] == "TEMPLATES"

    @pytest.mark.asyncio
    async def test_search_defaults_total_to_hit_count_and_omits_optional_params(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            json_data={"data": {"hits": [{"id": "a", "name": "x", "ownerUsername": "o"}]}}
        )

        packages, total = await client.search()
        assert total == 1  # defaulted to len(hits)
        sent_params = fake.request.call_args.kwargs["params"]
        assert "isPublic" not in sent_params
        assert "ownerId" not in sent_params
        assert "packageType" not in sent_params

    @pytest.mark.asyncio
    async def test_get_package_info_exact_match(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(
            json_data={
                "data": {
                    "hits": [
                        {"id": "1", "name": "other", "ownerUsername": "alice"},
                        {"id": "2", "name": "medical", "ownerUsername": "alice"},
                    ],
                    "total": 2,
                }
            }
        )

        pkg = await client.get_package_info("alice", "medical")
        assert pkg.id == "2"
        assert pkg.full_name == "alice/medical"

    @pytest.mark.asyncio
    async def test_get_package_info_not_found_raises_404(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = install_fake_client(client)
        fake.request.return_value = make_response(json_data={"data": {"hits": [], "total": 0}})

        with pytest.raises(LexiconClientError) as exc_info:
            await client.get_package_info("alice", "ghost")
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDownload:
    @pytest.mark.asyncio
    async def test_download_success_returns_bytes(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="tok"))
        fake = AsyncMock(name="download-client")
        client._download_client = fake
        fake.get.return_value = make_response(status_code=200, content=b"CCXARCHIVE")

        data = await client.download("alice", "medical", "1.0.0")
        assert data == b"CCXARCHIVE"
        # URL is base_url/packages/owner/repo/version
        called_url = fake.get.call_args.args[0]
        assert called_url == "http://x/api/v1/packages/alice/medical/1.0.0"
        # Auth header attached when authenticated
        sent_headers = fake.get.call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_download_unauthenticated_omits_auth_header(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig())
        fake = AsyncMock(name="download-client")
        client._download_client = fake
        fake.get.return_value = make_response(status_code=200, content=b"DATA")

        await client.download("alice", "medical")
        sent_headers = fake.get.call_args.kwargs["headers"]
        assert "Authorization" not in sent_headers

    @pytest.mark.asyncio
    async def test_download_404_raises(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = AsyncMock(name="download-client")
        client._download_client = fake
        fake.get.return_value = make_response(status_code=404, content=b"")

        with pytest.raises(LexiconClientError) as exc_info:
            await client.download("alice", "ghost")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_download_429_raises_rate_limit(self) -> None:
        client = LexiconClient(base_url="http://x")
        fake = AsyncMock(name="download-client")
        client._download_client = fake
        fake.get.return_value = make_response(status_code=429, content=b"")

        with pytest.raises(LexiconClientError) as exc_info:
            await client.download("alice", "busy")
        assert exc_info.value.status_code == 429
        assert "Rate limit" in exc_info.value.message


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpload:
    @pytest.mark.asyncio
    async def test_upload_requires_auth(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig())
        with pytest.raises(LexiconClientError) as exc_info:
            await client.upload(b"data")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_upload_success_returns_package_info(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="tok"))
        fake = AsyncMock(name="upload-client")
        client._upload_client = fake
        fake.post.return_value = make_response(
            status_code=201,
            json_data={"id": "new-1", "name": "uploaded", "ownerUsername": "alice"},
        )

        pkg = await client.upload(b"archive-bytes", public=False, message="initial")
        assert isinstance(pkg, PackageInfo)
        assert pkg.id == "new-1"
        assert pkg.name == "uploaded"

        # multipart fields and the dropped Content-Type header
        kwargs = fake.post.call_args.kwargs
        assert "package" in kwargs["files"]
        assert kwargs["data"]["public"] == "false"
        assert kwargs["data"]["message"] == "initial"
        assert "Content-Type" not in kwargs["headers"]
        assert kwargs["headers"]["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_upload_error_response_raises(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="tok"))
        fake = AsyncMock(name="upload-client")
        client._upload_client = fake
        fake.post.return_value = make_response(
            status_code=422,
            json_data={"message": "validation failed", "details": {"field": "package"}},
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client.upload(b"bad")
        assert exc_info.value.status_code == 422
        assert exc_info.value.message == "validation failed"

    @pytest.mark.asyncio
    async def test_upload_error_response_unparseable_json(self) -> None:
        client = LexiconClient(base_url="http://x", auth=AuthConfig(token="tok"))
        fake = AsyncMock(name="upload-client")
        client._upload_client = fake
        fake.post.return_value = make_response(
            status_code=500,
            raise_json=ValueError("no json"),
        )

        with pytest.raises(LexiconClientError) as exc_info:
            await client.upload(b"bad")
        assert exc_info.value.status_code == 500
        assert exc_info.value.message == "Upload failed"
