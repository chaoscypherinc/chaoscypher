# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Playwright browser E2E test fixtures.

Provides browser, context, and authenticated page fixtures for
testing the web UI against a running Docker container.

The app uses cookie-based session auth — the backend sets a
``cc_session`` httpOnly cookie via Set-Cookie on /auth/login or
/auth/setup, and the browser sends it on every subsequent request.
These fixtures match that model: we POST to /auth/login via httpx,
extract the cookie value, then inject it into each Playwright
``BrowserContext`` via ``context.add_cookies([...])``.
"""

import os
import time

import httpx
import pytest


try:
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)

ADMIN_USERNAME = "e2e_admin"
ADMIN_PASSWORD = "E2eTestPass123"
ADMIN_EMAIL = "admin@e2e-test.example.com"
SESSION_COOKIE = "cc_session"


def _post_with_retry(
    base_url: str,
    path: str,
    payload: dict,
    max_attempts: int = 20,
) -> httpx.Response:
    """POST that retries past auth-zone 429s (5 r/s burst 3 + 60 s window)."""
    for attempt in range(max_attempts):
        resp = httpx.post(f"{base_url}{path}", json=payload, timeout=30.0)
        if resp.status_code != 429:
            return resp
        if attempt < max_attempts - 1:
            time.sleep(2.0)
    return resp


@pytest.fixture(scope="session")
def browser_session_cookie(browser_base_url: str) -> str:
    """Authenticate once and return the ``cc_session`` cookie value.

    Tries first-time setup (fresh DB); falls back to login when the
    server reports 409 (already initialized). Mirrors the pattern in
    ``e2e/api/conftest.py::session_cookie`` with the same 20×2 s
    retry window so consecutive pytest invocations against the same
    hot stack don't cascade-fail when the auth rate-limit budget is
    consumed.
    """
    setup_resp = _post_with_retry(
        browser_base_url,
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
        # Setup succeeded but didn't return cookie — fall through to login.
    elif setup_resp.status_code not in (409,):
        setup_resp.raise_for_status()

    login_resp = _post_with_retry(
        browser_base_url,
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


def _inject_session(context: BrowserContext, base_url: str) -> None:
    """Authenticate the context via an in-browser /auth/login POST.

    Originally tried manual ``context.add_cookies(...)`` injection of
    the ``cc_session`` cookie obtained out-of-band via httpx. That
    works in theory but is fragile: the server emits the cookie with
    ``Secure`` set, which Chromium drops on plain-HTTP test stacks
    even when we manually override the attribute. Doing the login
    POST from inside the browser lets Chromium store the cookie with
    its own correct attribute handling for the current origin —
    matching how a real user's login flow works.
    """
    from urllib.parse import urlparse
    host = urlparse(base_url).hostname or "localhost"
    page = context.new_page()
    try:
        # Need to be on the target origin before fetch() can land
        # cookies on it. ``about:blank`` would leave the cookie
        # un-scoped; a real navigation to the app establishes the
        # origin for the subsequent same-origin fetch.
        page.goto(base_url + "/login")
        page.wait_for_load_state("domcontentloaded")
        result = page.evaluate(
            """async ({ username, password }) => {
                const resp = await fetch("/api/v1/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "include",
                    body: JSON.stringify({ username, password }),
                });
                return { status: resp.status, body: await resp.text() };
            }""",
            {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        if result.get("status") != 200:
            msg = (
                f"Browser-side login failed: status={result.get('status')} "
                f"body={result.get('body', '')[:200]!r}"
            )
            raise RuntimeError(msg)
        # Verify the cookie actually landed.
        names = {c["name"] for c in context.cookies(base_url)}
        if SESSION_COOKIE not in names:
            msg = (
                f"Browser did not retain {SESSION_COOKIE} cookie after "
                f"login (host={host}). Cookies present: {sorted(names)}."
            )
            raise RuntimeError(msg)
    finally:
        page.close()


# Tests in these files (or specific cases below) drive UI flows that
# the React app gates on ``LLMHealth.verified=True`` — the Add Source
# button is disabled, the chat input is read-only, etc. The e2e
# Docker stack ships without an LLM, so we auto-skip these rather
# than fail every run. Wiring a fake-ollama service unblocks them.
_LLM_REQUIRED_FILES = frozenset({
    "test_upload_source.py",
})


def pytest_collection_modifyitems(config, items):
    """Auto-apply ``browser`` + (where applicable) ``requires_llm`` markers."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/browser/" in path:
            item.add_marker(pytest.mark.browser)
            filename = path.rsplit("/", 1)[-1]
            if filename in _LLM_REQUIRED_FILES:
                item.add_marker(pytest.mark.requires_llm)


def pytest_configure(config):
    """Register custom markers used by this tier."""
    config.addinivalue_line(
        "markers",
        "requires_llm: browser test exercises UI gated by LLMHealth.verified=True;"
        " auto-skipped when the running stack has no LLM.",
    )
    config.addinivalue_line(
        "markers",
        "browser: marks tests that require Playwright + a running app stack.",
    )


@pytest.fixture(scope="session")
def browser_base_url() -> str:
    """Base URL for browser tests.

    In Docker runner: use http://app:80 (container network).
    Locally:           use http://localhost:8888 (host port).
    """
    return os.environ.get("E2E_BROWSER_URL") or os.environ.get(
        "E2E_BASE_URL", "http://localhost:8888"
    )


@pytest.fixture(scope="session")
def browser_llm_verified(browser_base_url: str, browser_session_cookie: str) -> bool:
    """Return True if the running stack reports an LLM as verified.

    Same idea as the api tier's ``llm_verified`` fixture — probe
    /api/v1/settings/llm/health once per session, cache, and use to
    auto-skip ``requires_llm``-marked browser tests.
    """
    try:
        with httpx.Client(
            base_url=browser_base_url,
            cookies={SESSION_COOKIE: browser_session_cookie},
            timeout=10.0,
        ) as c:
            resp = c.get("/api/v1/settings/llm/health")
            if resp.status_code != 200:
                return False
            return bool(resp.json().get("verified", False))
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_if_no_llm(
    request: pytest.FixtureRequest, browser_llm_verified: bool
) -> None:
    """Skip ``requires_llm``-marked browser tests when no LLM is configured."""
    if request.node.get_closest_marker("requires_llm") and not browser_llm_verified:
        pytest.skip(
            "Skipped: stack has no LLM configured "
            "(GET /api/v1/settings/llm/health → verified=False). "
            "This test drives a UI flow gated by LLM verification "
            "(disabled Add Source button, read-only chat input, etc.). "
            "Configure a stub LLM in packages/docker/e2e/docker-compose.yml "
            "to unblock."
        )


@pytest.fixture(scope="session")
def pw_browser():
    """Session-scoped Playwright browser.

    ``--disable-features=HttpsUpgrades,HttpsFirstBalancedMode`` keeps
    Chromium from auto-upgrading the runner-internal ``http://app:80``
    URL to HTTPS — single-label hostnames trigger HTTPS-First in
    recent Chromium and would otherwise produce ERR_SSL_PROTOCOL_ERROR
    on every page navigation.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-features=HttpsUpgrades,HttpsFirstBalancedMode"],
        )
        yield browser
        browser.close()


@pytest.fixture(scope="session", autouse=True)
def _prewarm_react_chunks(
    pw_browser: Browser,
    browser_base_url: str,
    browser_session_cookie: str,
) -> None:
    """Pre-warm React code chunks for pages that lazy-load.

    The React frontend uses code splitting. On the first visit to
    /settings (and potentially other chunked routes), the chunks need
    to download before React can render. This causes flaky tests where
    the first test to hit /settings sees an empty page. Pre-warming
    these routes once per session avoids the cold-start.

    ``browser_session_cookie`` is requested so the admin user is
    bootstrapped server-side before we even open a Playwright context.
    The login itself happens in-browser via ``_inject_session``.
    """
    _ = browser_session_cookie  # bootstrap admin user
    context = pw_browser.new_context(
        base_url=browser_base_url, viewport={"width": 1280, "height": 720}
    )
    _inject_session(context, browser_base_url)
    page = context.new_page()
    try:
        for path in ("/", "/settings", "/sources", "/nodes", "/graph"):
            page.goto(path)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
    finally:
        page.close()
        context.close()


@pytest.fixture
def page(pw_browser: Browser, browser_base_url: str) -> Page:
    """Fresh page for each test (no auth)."""
    context = pw_browser.new_context(
        base_url=browser_base_url,
        viewport={"width": 1280, "height": 720},
    )
    pg = context.new_page()
    yield pg
    pg.close()
    context.close()


@pytest.fixture
def authenticated_page(
    pw_browser: Browser,
    browser_base_url: str,
    browser_session_cookie: str,
) -> Page:
    """Page that has performed an in-browser login.

    ``_inject_session`` POSTs /api/v1/auth/login from inside the
    browser; the server's Set-Cookie response lands the cc_session
    cookie on the context with browser-native attribute handling.
    Subsequent /auth/me probes see an authenticated session and the
    React app renders the post-login layout.
    """
    _ = browser_session_cookie  # bootstrap admin user
    context = pw_browser.new_context(
        base_url=browser_base_url,
        viewport={"width": 1280, "height": 720},
    )
    _inject_session(context, browser_base_url)
    pg = context.new_page()
    pg.goto("/")
    pg.wait_for_load_state("networkidle", timeout=15000)

    yield pg
    pg.close()
    context.close()
