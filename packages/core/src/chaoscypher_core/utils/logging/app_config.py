# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backend Logging Configuration - Re-exports from Core.

This module re-exports the logging configuration from the core package
for convenience. Uses the Barrel Pattern for clean imports.

The actual implementation lives in chaoscypher_core.utils.logging.config.
"""

from chaoscypher_core.utils.logging.config import (
    configure_logging,
    get_logger,
)


__all__ = [
    "configure_logging",
    "get_logger",
]
