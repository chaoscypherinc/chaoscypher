# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Lenses retired in ADR-0001; no LensSourceProtocol should remain."""

from __future__ import annotations

import importlib


def test_lens_source_protocol_does_not_exist():
    module = importlib.import_module("chaoscypher_core.ports.source_file")
    assert not hasattr(module, "LensSourceProtocol"), (
        "LensSourceProtocol was retired in ADR-0001 and must not be re-exported."
    )


def test_ports_init_does_not_export_lens_protocol():
    ports = importlib.import_module("chaoscypher_core.ports")
    assert "LensSourceProtocol" not in getattr(ports, "__all__", [])
