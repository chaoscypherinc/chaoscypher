"""Tests for scripts/check_api_docs.py."""

from __future__ import annotations

import check_api_docs as mod
from check_api_docs import (
    Endpoint,
    diff_endpoints,
    extract_endpoints_from_markdown,
    extract_routes_from_features,
)


def test_extract_routes_from_features_finds_get_method():
    """Walks @router.* decorators and extracts (method, path)."""
    code = """
from fastapi import APIRouter
router = APIRouter(prefix="/api/v1/widgets")

@router.get("/{widget_id}")
async def get_widget(widget_id: str):
    return {}
"""
    routes = extract_routes_from_features(source_code=code)
    assert Endpoint(method="GET", path="/api/v1/widgets/{widget_id}") in routes


def test_extract_endpoints_from_markdown_parses_method_path_block():
    """Pulls (method, path) from `### GET /path` headers in reference/api markdown."""
    md = """# Widgets

### GET /api/v1/widgets/{widget_id}

Description here.
"""
    endpoints = extract_endpoints_from_markdown(text=md)
    assert Endpoint(method="GET", path="/api/v1/widgets/{widget_id}") in endpoints


def test_extract_endpoints_from_markdown_parses_code_block_format():
    """Pulls (method, path) from bare ``METHOD /api/v1/path`` lines in fenced code blocks.

    This is the predominant style used in this project — descriptive headings
    with the path in a plain code block below.
    """
    md = """# Widgets

### Get Widget

```
GET /api/v1/widgets/{widget_id}
```

Returns a widget by ID.

### Create Widget

```
POST /api/v1/widgets
```

Creates a new widget.
"""
    endpoints = extract_endpoints_from_markdown(text=md)
    assert Endpoint(method="GET", path="/api/v1/widgets/{widget_id}") in endpoints
    assert Endpoint(method="POST", path="/api/v1/widgets") in endpoints


def test_diff_endpoints_reports_undocumented_route():
    """Routes present in code but missing from docs surface as undocumented."""
    code_routes = {Endpoint("GET", "/api/v1/foo"), Endpoint("POST", "/api/v1/foo")}
    md_routes = {Endpoint("GET", "/api/v1/foo")}
    diff = diff_endpoints(code=code_routes, docs=md_routes)
    assert Endpoint("POST", "/api/v1/foo") in diff.undocumented
    assert not diff.unimplemented


def test_diff_endpoints_reports_unimplemented_doc_route():
    """Routes documented but not implemented surface as unimplemented."""
    code_routes = {Endpoint("GET", "/api/v1/foo")}
    md_routes = {Endpoint("GET", "/api/v1/foo"), Endpoint("DELETE", "/api/v1/foo")}
    diff = diff_endpoints(code=code_routes, docs=md_routes)
    assert Endpoint("DELETE", "/api/v1/foo") in diff.unimplemented
    assert not diff.undocumented


def test_extract_routes_from_features_handles_include_router_prefix(tmp_path):
    """Prefix applied at include_router time (router.py wiring) must be composed."""
    # Create a fake monorepo layout matching the real path structure so that
    # _build_wiring_map can resolve the dotted import to a file path.
    cortex_src = tmp_path / "packages" / "cortex" / "src"
    features_dir = cortex_src / "chaoscypher_cortex" / "features"
    api_dir = cortex_src / "chaoscypher_cortex" / "api" / "v1"
    widget_dir = features_dir / "widget"
    widget_dir.mkdir(parents=True)
    api_dir.mkdir(parents=True)

    (widget_dir / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.get('/{widget_id}')\n"
        "async def get_widget(widget_id: str): return {}\n"
    )
    (api_dir / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "from chaoscypher_cortex.features.widget.api import router as widget_router\n"
        "def create_api_router():\n"
        "    api = APIRouter(prefix='/api/v1')\n"
        "    api.include_router(widget_router, prefix='/widgets')\n"
        "    return api\n"
    )

    # Monkey-patch the module-level globals so extract_routes_from_features()
    # uses our fake layout rather than the real monorepo.
    original_features = mod.FEATURES_DIR
    original_router = mod.ROUTER_FILE
    mod.FEATURES_DIR = features_dir
    mod.ROUTER_FILE = api_dir / "router.py"
    try:
        routes = mod.extract_routes_from_features()
    finally:
        mod.FEATURES_DIR = original_features
        mod.ROUTER_FILE = original_router

    assert Endpoint(method="GET", path="/api/v1/widgets/{widget_id}") in routes


def test_extract_routes_two_routers_in_one_file(tmp_path):
    """Files that export two router variables must resolve each variable to its own wired prefix.

    For example, pause/api.py must not collapse to a shared first prefix.

    Regression test for the two-router-in-one-file fix: before the fix the
    wiring map only recorded one prefix per file, so system_router routes were
    incorrectly labelled as /sources/* routes.
    """
    cortex_src = tmp_path / "packages" / "cortex" / "src"
    features_dir = cortex_src / "chaoscypher_cortex" / "features"
    api_dir = cortex_src / "chaoscypher_cortex" / "api" / "v1"
    pause_dir = features_dir / "pause"
    pause_dir.mkdir(parents=True)
    api_dir.mkdir(parents=True)

    (pause_dir / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "sources_router = APIRouter()\n"
        "system_router = APIRouter()\n"
        "@sources_router.post('/pause')\n"
        "async def pause_sources(): return {}\n"
        "@system_router.get('/status')\n"
        "async def get_status(): return {}\n"
        "@system_router.delete('/events')\n"
        "async def clear_events(): return {}\n"
    )
    (api_dir / "router.py").write_text(
        "from fastapi import APIRouter\n"
        "from chaoscypher_cortex.features.pause.api import (\n"
        "    sources_router as pause_sources_router,\n"
        ")\n"
        "from chaoscypher_cortex.features.pause.api import (\n"
        "    system_router as pause_system_router,\n"
        ")\n"
        "def create_api_router():\n"
        "    api = APIRouter(prefix='/api/v1')\n"
        "    api.include_router(pause_sources_router, prefix='/sources')\n"
        "    api.include_router(pause_system_router, prefix='/system/processing')\n"
        "    return api\n"
    )

    original_features = mod.FEATURES_DIR
    original_router = mod.ROUTER_FILE
    mod.FEATURES_DIR = features_dir
    mod.ROUTER_FILE = api_dir / "router.py"
    try:
        routes = mod.extract_routes_from_features()
    finally:
        mod.FEATURES_DIR = original_features
        mod.ROUTER_FILE = original_router

    # sources_router route must get /sources prefix
    assert Endpoint(method="POST", path="/api/v1/sources/pause") in routes
    # system_router routes must get /system/processing prefix, not /sources
    assert Endpoint(method="GET", path="/api/v1/system/processing/status") in routes
    assert Endpoint(method="DELETE", path="/api/v1/system/processing/events") in routes
    # Confirm the wrong paths are not emitted
    assert Endpoint(method="GET", path="/api/v1/sources/status") not in routes
    assert Endpoint(method="DELETE", path="/api/v1/sources/events") not in routes
