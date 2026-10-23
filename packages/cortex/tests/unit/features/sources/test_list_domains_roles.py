# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources/domains names each domain's role: the floor every source has, or a pack."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.registry import DomainRegistry
from chaoscypher_cortex.features.sources import api as sources_api


@pytest.mark.asyncio
async def test_the_floor_is_role_floor_and_every_other_domain_a_pack() -> None:
    registry = DomainRegistry(database_name="default")
    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=registry,
        ),
        patch("chaoscypher_core.app_config.engine_factory.build_engine_settings"),
    ):
        response = await sources_api.list_domains(_="user", settings=MagicMock())

    roles = {d.name: d.role for d in response.domains}
    assert roles.pop("generic") == "floor"
    assert roles
    assert set(roles.values()) == {"pack"}
