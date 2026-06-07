# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bundled static assets for the graph snapshot renderer (logo, fonts)."""

from __future__ import annotations

from pathlib import Path


__all__ = ["RESOURCES_DIR"]

RESOURCES_DIR: Path = Path(__file__).parent
