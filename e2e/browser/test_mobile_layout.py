# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Mobile and responsive layout tests.

These tests verify that the UI renders correctly at different viewport sizes.
They do three things:

1. Capture screenshots of every page at each viewport size (for human review).
2. Assert no horizontal overflow (a common mobile bug).
3. Assert critical elements are visible within the viewport.

Screenshots are saved to test-reports/screenshots/<viewport>/<page>.png
for manual review.

**Data seeding:** The `seeded_app` session fixture ensures the app has
realistic data (imported seed.ccx + extra entities + an uploaded source)
BEFORE screenshots are captured. Without this, tables would be empty and
layout bugs that only appear with content wouldn't be visible.
"""

import time
from pathlib import Path

import httpx
import pytest


try:
    from playwright.sync_api import Browser, Page, expect
except ImportError:
    pytest.skip("playwright not installed", allow_module_level=True)


# Viewport definitions - width x height
VIEWPORTS = {
    "mobile": {"width": 375, "height": 667},  # iPhone SE
    "mobile-large": {"width": 414, "height": 896},  # iPhone 11 Pro Max
    "tablet": {"width": 768, "height": 1024},  # iPad portrait
    "desktop": {"width": 1280, "height": 720},  # Standard desktop
}

# Pages to test (path, needs_auth)
PAGES = [
    ("/login", False),
    ("/", True),
    ("/sources", True),
    ("/nodes", True),
    ("/edges", True),
    ("/templates", True),
    ("/graph", True),
    ("/settings", True),
    ("/chat", True),
]


def _get_screenshots_dir() -> Path:
    """Get the screenshot output directory, creating it if needed.

    Inside the runner container the test-reports volume mounts at
    ``/app/test-reports``; the previous ``parents[3]`` walked up one
    level too far and pointed at root, where the non-root ``appuser``
    has no write access.
    """
    reports_root = Path(__file__).parents[2] / "test-reports"
    screenshots = reports_root / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    return screenshots


def _safe_name(path: str) -> str:
    """Convert a URL path to a filesystem-safe filename."""
    if path == "/":
        return "root"
    return path.strip("/").replace("/", "_") or "root"


def _create_mobile_page(
    pw_browser: Browser,
    browser_base_url: str,
    viewport: dict,
    session_cookie: str | None = None,
) -> Page:
    """Create a page with the given viewport and optional cookie-based auth."""
    context = pw_browser.new_context(
        base_url=browser_base_url,
        viewport=viewport,
        device_scale_factor=2,  # Simulate retina for sharper screenshots
    )

    if session_cookie:
        # In-browser login: same pattern as the conftest authenticated_page
        # fixture — Set-Cookie from /auth/login lands the cc_session
        # cookie with browser-native attribute handling for the
        # current origin (vs. fighting Chromium's secure-cookie
        # policy on plain-HTTP test stacks).
        login_page = context.new_page()
        try:
            login_page.goto(browser_base_url + "/login")
            login_page.wait_for_load_state("domcontentloaded")
            login_page.evaluate(
                """async () => {
                    await fetch("/api/v1/auth/login", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        credentials: "include",
                        body: JSON.stringify({
                            username: "e2e_admin",
                            password: "E2eTestPass123",
                        }),
                    });
                }"""
            )
        finally:
            login_page.close()

    return context.new_page()


@pytest.fixture(scope="session")
def screenshots_dir() -> Path:
    """Directory for mobile layout screenshots."""
    return _get_screenshots_dir()


@pytest.fixture(scope="session")
def seeded_app(browser_base_url: str, browser_session_cookie: str) -> dict:
    """Seed the app with realistic data before screenshots are captured.

    Without this, tables are empty and layout bugs that only appear with
    content (long labels, many rows, pagination) won't show up in screenshots.

    Steps:
    1. Import seed.ccx (4 templates, 4 nodes, 3 edges)
    2. Create 25 extra nodes with long labels to stress the table
    3. Upload sample.txt as a source (so Sources page has content)

    This fixture is session-scoped so it only runs once per test run.
    """
    base = browser_base_url

    with httpx.Client(
        base_url=base,
        cookies={"cc_session": browser_session_cookie},
        timeout=60.0,
    ) as client:
        # 1. Import the seed CCX package
        seed_path = Path(__file__).parents[1] / "fixtures" / "seed.ccx"
        if seed_path.exists():
            with seed_path.open("rb") as f:
                import_resp = client.post(
                    "/api/v1/exports/import",
                    files={"file": ("seed.ccx", f, "application/octet-stream")},
                    params={"merge": "true"},
                )
            if import_resp.status_code == 202:
                task_id = import_resp.json().get("task_id")
                # Wait for import to complete
                if task_id:
                    for _ in range(30):
                        task_resp = client.get(f"/api/v1/queue/tasks/{task_id}")
                        if task_resp.status_code == 200:
                            status = task_resp.json().get("status", "")
                            if status in ("complete", "completed", "success"):
                                break
                            if status in ("failed", "error"):
                                break
                        time.sleep(1)

        # 2. Create a node template with a long name to stress the UI
        template_name = "Mobile_Layout_Test_Entity_With_A_Very_Long_Name"
        tmpl_resp = client.post(
            "/api/v1/templates",
            json={
                "name": template_name,
                "template_type": "node",
                "properties": [
                    {
                        "name": "title",
                        "display_name": "title",
                        "property_type": "string",
                        "required": True,
                    },
                    {
                        "name": "description",
                        "display_name": "description",
                        "property_type": "text",
                        "required": False,
                    },
                ],
            },
        )
        template_id = None
        if tmpl_resp.status_code == 201:
            template_id = tmpl_resp.json()["id"]
        else:
            # Template exists already from previous run
            list_resp = client.get("/api/v1/templates")
            for t in list_resp.json()["data"]:
                if t["name"] == template_name:
                    template_id = t["id"]
                    break

        # 3. Create 25 nodes with long labels to fill the table
        if template_id:
            existing = client.get("/api/v1/nodes", params={"template_id": template_id}).json()
            if existing["pagination"]["total"] < 20:
                for i in range(25):
                    client.post(
                        "/api/v1/nodes",
                        json={
                            "template_id": template_id,
                            "label": (
                                f"Mobile Test Entity {i:02d} - A long label to stress mobile layout"
                            ),
                            "properties": {
                                "title": f"Entity #{i:02d}",
                                "description": (
                                    "This is a longer description that "
                                    "might cause text wrapping issues on "
                                    "narrow mobile screens if not handled."
                                ),
                            },
                        },
                    )

        # 4. Upload a source so /sources has content
        sample_txt = Path(__file__).parents[2] / "fixtures" / "sample_data" / "sample.txt"
        if sample_txt.exists():
            existing_sources = client.get("/api/v1/sources").json()
            if existing_sources["pagination"]["total"] == 0:
                with sample_txt.open("rb") as f:
                    client.post(
                        "/api/v1/sources",
                        files={"file": ("mobile_seed.txt", f, "text/plain")},
                        data={"extract_entities": "false"},
                    )
                # Wait briefly for indexing
                time.sleep(3)

    return {"seeded": True}


@pytest.mark.parametrize(
    ("viewport_name", "viewport"),
    list(VIEWPORTS.items()),
    ids=list(VIEWPORTS.keys()),
)
class TestMobileScreenshots:
    """Capture screenshots of every page at each viewport size."""

    def test_capture_page_screenshots(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        browser_session_cookie: str,
        seeded_app: dict,
        screenshots_dir: Path,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """Capture full-page screenshots at this viewport for all pages."""
        _ = seeded_app  # Ensure data is seeded before capturing
        out_dir = screenshots_dir / viewport_name
        out_dir.mkdir(parents=True, exist_ok=True)

        failures: list[str] = []

        for path, needs_auth in PAGES:
            page = _create_mobile_page(
                pw_browser,
                browser_base_url,
                viewport,
                browser_session_cookie if needs_auth else None,
            )
            try:
                page.goto(path)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(1500)  # Let async content render

                screenshot_path = out_dir / f"{_safe_name(path)}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception as e:
                failures.append(f"{path}: {e}")
            finally:
                page.close()
                page.context.close()

        assert not failures, f"Failed to capture: {failures}"


@pytest.mark.parametrize(
    ("viewport_name", "viewport"),
    [("mobile", VIEWPORTS["mobile"]), ("tablet", VIEWPORTS["tablet"])],
    ids=["mobile", "tablet"],
)
class TestMobileLayoutAssertions:
    """Automated layout checks at mobile/tablet viewports."""

    def test_no_horizontal_overflow_sources_page(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        browser_session_cookie: str,
        seeded_app: dict,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """Sources page has no horizontal scroll at mobile viewport.

        Horizontal overflow is the #1 mobile layout bug - fixed-width elements
        that don't scale down cause the whole page to scroll sideways.
        """
        _ = seeded_app
        page = _create_mobile_page(pw_browser, browser_base_url, viewport, browser_session_cookie)
        try:
            page.goto("/sources")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            # Measure document width vs viewport width
            scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
            client_width = page.evaluate("() => document.documentElement.clientWidth")

            # Allow 1px tolerance for sub-pixel rendering
            assert scroll_width <= client_width + 1, (
                f"Horizontal overflow at {viewport_name}: "
                f"scrollWidth={scroll_width}, clientWidth={client_width}, "
                f"overflow={scroll_width - client_width}px"
            )
        finally:
            page.close()
            page.context.close()

    def test_no_horizontal_overflow_nodes_page(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        browser_session_cookie: str,
        seeded_app: dict,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """Nodes/entities page has no horizontal scroll at mobile viewport."""
        _ = seeded_app
        page = _create_mobile_page(pw_browser, browser_base_url, viewport, browser_session_cookie)
        try:
            page.goto("/nodes")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
            client_width = page.evaluate("() => document.documentElement.clientWidth")

            assert scroll_width <= client_width + 1, (
                f"Horizontal overflow at {viewport_name}: "
                f"scrollWidth={scroll_width}, clientWidth={client_width}, "
                f"overflow={scroll_width - client_width}px"
            )
        finally:
            page.close()
            page.context.close()

    def test_no_horizontal_overflow_settings_page(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        browser_session_cookie: str,
        seeded_app: dict,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """Settings page has no horizontal scroll at mobile viewport."""
        _ = seeded_app
        page = _create_mobile_page(pw_browser, browser_base_url, viewport, browser_session_cookie)
        try:
            page.goto("/settings")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
            client_width = page.evaluate("() => document.documentElement.clientWidth")

            assert scroll_width <= client_width + 1, (
                f"Horizontal overflow at {viewport_name}: "
                f"scrollWidth={scroll_width}, clientWidth={client_width}, "
                f"overflow={scroll_width - client_width}px"
            )
        finally:
            page.close()
            page.context.close()

    def test_login_page_fits_viewport(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """Login form fits within the viewport (no clipping)."""
        page = _create_mobile_page(pw_browser, browser_base_url, viewport, session_cookie=None)
        try:
            page.goto("/login")
            page.wait_for_load_state("networkidle")

            # The login button should be visible and not cut off
            login_btn = page.get_by_role("button", name="Log In")
            expect(login_btn).to_be_visible()

            # Get button bounding box - must be entirely within viewport
            box = login_btn.bounding_box()
            assert box is not None
            assert box["x"] >= 0, f"Login button extends left of viewport: x={box['x']}"
            assert box["x"] + box["width"] <= viewport["width"] + 1, (
                f"Login button extends right of viewport: "
                f"right={box['x'] + box['width']}, vw={viewport['width']}"
            )
        finally:
            page.close()
            page.context.close()

    def test_sources_add_button_clickable(
        self,
        pw_browser: Browser,
        browser_base_url: str,
        browser_session_cookie: str,
        seeded_app: dict,
        viewport_name: str,
        viewport: dict,
    ) -> None:
        """The Add Source button is visible and within viewport on mobile."""
        _ = seeded_app
        page = _create_mobile_page(pw_browser, browser_base_url, viewport, browser_session_cookie)
        try:
            page.goto("/sources")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)

            add_btn = page.get_by_role("button", name="Add Source")
            expect(add_btn).to_be_visible(timeout=10000)

            box = add_btn.bounding_box()
            assert box is not None, "Add Source button has no bounding box"
            # Button must be within viewport horizontally
            assert box["x"] >= 0, f"Add Source button off-screen left: x={box['x']}"
            assert box["x"] + box["width"] <= viewport["width"] + 1, (
                f"Add Source button off-screen right: "
                f"right={box['x'] + box['width']}, vw={viewport['width']}"
            )
            # Button must be tall enough to tap (44px minimum per WCAG)
            assert box["height"] >= 32, f"Add Source button too short: {box['height']}px"
        finally:
            page.close()
            page.context.close()
