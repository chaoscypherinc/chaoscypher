# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The app settings carry the graph section and hand it to the engine: the resolver is settable."""

from __future__ import annotations

from chaoscypher_core.app_config import Settings
from chaoscypher_core.app_config.engine_factory import build_engine_settings


def test_the_graph_section_reaches_the_engine_settings() -> None:
    settings = Settings()
    assert settings.graph.resolve_identities is True  # the default since 2026-10-06
    settings.graph.resolve_identities = False
    engine = build_engine_settings(settings)
    assert engine.graph.resolve_identities is False
    engine.graph.resolve_identities = True
    assert settings.graph.resolve_identities is False  # a copy, not the same object
