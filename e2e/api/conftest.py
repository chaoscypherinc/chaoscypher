# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""API E2E test fixtures.

Provides an authenticated httpx client for testing against a running
Docker container. Auth strategy depends on E2E_PHASE:
- fresh: runs setup to create admin user
- resume: logs in with existing admin creds

The server uses cookie-based session auth (``cc_session``) rather than
bearer tokens. The cookie carries the ``Secure`` attribute, which
httpx normally refuses to send over plain HTTP — we work around that
by passing the cookie value explicitly via the ``cookies=`` param when
constructing the test client.
"""

import os
import time

import httpx
import pytest


# Admin credentials used across fresh and resume phases
ADMIN_USERNAME = "e2e_admin"
ADMIN_PASSWORD = "E2eTestPass123"
ADMIN_EMAIL = "admin@e2e-test.example.com"
SESSION_COOKIE = "cc_session"

# Tests in these files exercise paths gated by ``/api/v1/sources`` upload
# / extraction code which the cortex now blocks with HTTP 409
# LLM_NOT_VERIFIED when no LLM is configured. The e2e Docker stack ships
# without an LLM; rather than fail every one of these tests we mark them
# requires_llm and skip when the runtime says LLM is not verified.
_LLM_REQUIRED_FILES = frozenset({
    "test_sources.py",
    "test_source_url.py",
    "test_source_data.py",
    "test_source_extraction.py",
    "test_extraction_control.py",
    "test_journeys.py",       # source-upload journey only
    "test_negative_paths.py", # source-url negative paths only
})


def pytest_collection_modifyitems(config, items):
    """Auto-apply api marker + requires_llm marker (where applicable)."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/api/" in path:
            item.add_marker(pytest.mark.api)
            filename = path.rsplit("/", 1)[-1]
            if filename in _LLM_REQUIRED_FILES:
                item.add_marker(pytest.mark.requires_llm)


def pytest_configure(config):
    """Register custom markers used by this tier."""
    config.addinivalue_line(
        "markers",
        "requires_llm: test exercises code paths gated by /llm/health "
        "verified=True; auto-skipped when the running stack has no LLM.",
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL for the running app container."""
    return os.environ.get("E2E_BASE_URL", "http://localhost:8888")


@pytest.fixture(scope="session")
def session_cookie(base_url: str, e2e_phase: str) -> str:
    """Authenticate and return the ``cc_session`` cookie value.

    Fresh phase: runs first-time setup (falls back to login if the
    server already has an admin — 409 ``already initialized``).
    Resume phase: logs in with existing creds.

    Returns the raw cookie value (suitable for ``cookies=`` on a
    subsequent ``httpx.Client``). The cookie's ``Secure`` attribute is
    intentionally ignored: the test stack runs on plain HTTP, and
    passing the value through ``cookies=`` rather than letting the
    cookie jar enforce policy bypasses httpx's secure-cookie check.
    """
    def _post(c: httpx.Client, path: str, payload: dict) -> httpx.Response:
        """POST that retries past nginx/app auth-zone 429s.

        Successive ``pytest`` invocations against the same hot stack
        each re-run this fixture, consuming the 5-logins / 3-setups
        per-60s budget. Without this retry the first test in a
        rerun-within-60s session 429s and every subsequent test
        cascades the failure (the ``client`` fixture depends on
        ``session_cookie``, so a 429 here ERRORs every test).
        """
        for attempt in range(20):
            resp = c.post(path, json=payload)
            if resp.status_code != 429:
                return resp
            if attempt < 19:
                time.sleep(2.0)
        return resp

    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        if e2e_phase == "fresh":
            setup_resp = _post(
                c,
                "/api/v1/auth/setup",
                {
                    "username": ADMIN_USERNAME,
                    "password": ADMIN_PASSWORD,
                    "email": ADMIN_EMAIL,
                },
            )
            if setup_resp.status_code in (200, 201):
                cookie = setup_resp.cookies.get(SESSION_COOKIE)
                if cookie:
                    return cookie
                # Setup succeeded but didn't return cookie — fall through to login
            elif setup_resp.status_code not in (409,):
                setup_resp.raise_for_status()
            # 409 ``already initialized`` — admin exists; log in below

        login_resp = _post(
            c,
            "/api/v1/auth/login",
            {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        login_resp.raise_for_status()
        cookie = login_resp.cookies.get(SESSION_COOKIE)
        if not cookie:
            msg = (
                f"Login did not return a {SESSION_COOKIE} cookie. "
                f"Status={login_resp.status_code} body={login_resp.text[:200]!r}"
            )
            raise RuntimeError(msg)
        return cookie


@pytest.fixture(autouse=True, scope="session")
def _ensure_setup(session_cookie: str) -> None:
    """Ensure auth runs once before any test executes.

    Tests that use only ``base_url`` (not ``session_cookie`` or
    ``client``) would otherwise hit endpoints before setup → 401.
    """
    _ = session_cookie


@pytest.fixture(scope="session")
def client(base_url: str, session_cookie: str) -> httpx.Client:
    """Session-scoped authenticated httpx client (cookie auth)."""
    c = httpx.Client(
        base_url=base_url,
        cookies={SESSION_COOKIE: session_cookie},
        timeout=30.0,
    )
    yield c
    c.close()


@pytest.fixture(scope="session")
def llm_verified(client: httpx.Client) -> bool:
    """Return True if the running stack reports an LLM as verified.

    Hits ``/api/v1/settings/llm/health`` once per session and caches the
    result. Used by the autouse ``_skip_if_no_llm`` fixture below to
    skip tests marked ``requires_llm`` when no LLM is configured (the
    standard e2e Docker stack ships without one).
    """
    try:
        resp = client.get("/api/v1/settings/llm/health")
        if resp.status_code != 200:
            return False
        return bool(resp.json().get("verified", False))
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_if_no_llm(request: pytest.FixtureRequest, llm_verified: bool) -> None:
    """Skip ``requires_llm``-marked tests when the stack has no LLM."""
    if request.node.get_closest_marker("requires_llm") and not llm_verified:
        pytest.skip(
            "Skipped: stack has no LLM configured "
            "(GET /api/v1/settings/llm/health → verified=False). "
            "This test exercises source-upload / extraction paths that "
            "require LLM verification. Configure a stub LLM in "
            "packages/docker/e2e/docker-compose.yml to unblock."
        )


@pytest.fixture(scope="session")
def auth_tokens(session_cookie: str) -> dict:
    """Legacy shim. The server returned bearer tokens before the cookie
    migration; a few tests in ``test_auth.py`` still ask for
    ``refresh_token`` / ``access_token``. Those tests will fail loudly
    here rather than silently — the legacy token model is gone, and
    rewriting those tests is separate work from the cookie-auth fix.
    """
    return {
        "_session_cookie": session_cookie,
        "_note": "bearer-token model removed; use the client fixture instead",
    }


def poll_source_status(
    client: httpx.Client,
    source_id: str,
    target_status: str = "indexed",
    timeout: int = 60,
) -> dict:
    """Poll a source until it reaches the target status or times out."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/sources/{source_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("processing_status") or data.get("status", "")
        if status == target_status:
            return data
        if status in ("error", "failed"):
            msg = f"Source {source_id} failed: {data}"
            raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Source {source_id} did not reach '{target_status}' within {timeout}s"
    raise TimeoutError(msg)


def poll_task_complete(
    client: httpx.Client,
    task_id: str,
    timeout: int = 120,
) -> dict:
    """Poll a queue task until completion or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/queue/tasks/{task_id}")
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in ("complete", "completed", "success"):
                return data
            if status in ("failed", "error"):
                msg = f"Task {task_id} failed: {data}"
                raise RuntimeError(msg)
        time.sleep(1)
    msg = f"Task {task_id} did not complete within {timeout}s"
    raise TimeoutError(msg)
