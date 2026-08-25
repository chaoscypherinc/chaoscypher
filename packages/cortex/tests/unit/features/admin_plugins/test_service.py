# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Admin plugin reload service tests.

Unlike test_api.py (which mocks the service to test routing), these run
``reload_all_plugin_registries`` for real against the module-owned registry
caches, pinning the wiring the endpoint's usefulness depends on.
"""

from typing import Any, cast

from chaoscypher_core.services.sources.loaders.factory import (
    _registry_cache as loader_cache,
)
from chaoscypher_cortex.features.admin_plugins.service import (
    reload_all_plugin_registries,
)


def test_reload_clears_real_loader_registry_cache() -> None:
    loader_cache[id(object())] = cast("Any", object())
    try:
        result = reload_all_plugin_registries()
    finally:
        loader_cache.clear()

    assert "LoaderRegistry" in result["invalidated"]
    assert result["total"] >= 1
    assert not loader_cache
