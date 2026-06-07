# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Exception-contract tests for LexiconClient._request.

Pins the exception type raised at the defensive fallback site (client.py:471)
so that the Cortex error mapper can produce structured 503 envelopes instead
of generic 500s.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.exceptions import ChaosCypherException, ExternalServiceError
from chaoscypher_core.services.lexicon.client import AuthConfig, LexiconClient


# ---------------------------------------------------------------------------
# client.py:471 — defensive fallback when all retries exhaust without an error
#
# This path fires when the retry loop completes all attempts, last_error is
# still None, and no response was captured. In practice this should not
# happen (the loop either captures an error or returns a response), but the
# guard is there to avoid falling off the end of the loop silently.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestUnknownErrorFallback:
    """ExternalServiceError is raised when retries exhaust with no captured error."""

    @pytest.mark.asyncio
    async def test_raises_external_service_error_on_unknown_failure(self) -> None:
        # Patch make_request so the loop never captures an httpx.RequestError
        # and never returns a response — simulate a theoretically impossible
        # path where last_error stays None after all attempts.
        # We achieve this by making the inner request raise a non-httpx
        # exception that is caught by the outer try block but not assigned
        # to last_error (which only captures httpx.RequestError).  However,
        # the cleanest way is to patch the entire _request loop: set
        # max_retries=0 so the for-loop body never executes, then last_error
        # is None and the fallback fires.
        client2 = LexiconClient(auth=AuthConfig(), base_url="http://test.invalid", max_retries=0)

        with pytest.raises(ExternalServiceError) as exc_info:
            await client2._request("GET", "/test")

        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "EXTERNAL_SERVICE_ERROR"
        assert exc.details.get("service") == "Lexicon"

    @pytest.mark.asyncio
    async def test_external_service_error_message_contains_service_name(self) -> None:
        client = LexiconClient(auth=AuthConfig(), base_url="http://test.invalid", max_retries=0)

        with pytest.raises(ExternalServiceError) as exc_info:
            await client._request("GET", "/endpoint")

        assert "Lexicon" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_external_service_error_is_chaoscypher_exception(self) -> None:
        client = LexiconClient(auth=AuthConfig(), base_url="http://test.invalid", max_retries=0)

        with pytest.raises(ChaosCypherException):
            await client._request("GET", "/endpoint")


# ---------------------------------------------------------------------------
# Connect-error path — added 2026-05-22 after operator log review surfaced
# 3x HTTP 500 with full stack traces from /api/v1/lexicon/search when
# LEXICON_URL pointed at a non-running service. The retry loop also ate
# ~14s of wall-clock per request before failing.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequestConnectError:
    """httpx.ConnectError is wrapped + short-circuits the retry budget."""

    @pytest.mark.asyncio
    async def test_connect_error_raises_external_service_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cortex global handler maps ExternalServiceError → HTTP 503."""
        import httpx

        from chaoscypher_core.services.lexicon import client as client_mod

        async def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            raise httpx.ConnectError("All connection attempts failed")

        monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

        client = LexiconClient(
            auth=AuthConfig(),
            base_url="http://lexicon.invalid",
            max_retries=4,
            retry_backoff=(0.01, 0.01, 0.01, 0.01),
        )

        with pytest.raises(ExternalServiceError) as exc_info:
            await client._request("GET", "/search")

        exc = exc_info.value
        assert exc.code == "EXTERNAL_SERVICE_ERROR"
        assert "Lexicon" in exc.message
        assert "unreachable" in exc.message.lower()
        # __cause__ preserves the original httpx.ConnectError for debug
        assert isinstance(exc.__cause__, httpx.ConnectError)
        # Sanity that the constant is reachable from the module under test
        assert client_mod.LexiconClient is LexiconClient

    @pytest.mark.asyncio
    async def test_connect_error_short_circuits_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConnectError must NOT trigger the (2,4,8,16) exponential backoff."""
        import httpx

        call_count = {"n": 0}

        async def fake_request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            raise httpx.ConnectError("All connection attempts failed")

        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
        monkeypatch.setattr("chaoscypher_core.services.lexicon.client.asyncio.sleep", fake_sleep)

        client = LexiconClient(
            auth=AuthConfig(),
            base_url="http://lexicon.invalid",
            max_retries=4,  # would normally retry 4x with backoff
        )

        with pytest.raises(ExternalServiceError):
            await client._request("GET", "/search")

        assert call_count["n"] == 1, "ConnectError must exit on first failure, not retry"
        assert sleep_calls == [], "No backoff sleep should run for ConnectError"
