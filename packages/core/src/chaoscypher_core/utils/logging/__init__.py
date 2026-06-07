# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured Logging Configuration for ChaosCypher Engine.

Provides framework-agnostic structlog configuration that works in both:
- CLI environments (synchronous)
- Docker/FastAPI environments (asynchronous)

This module provides:
- configure_logging(): Setup structured logging with JSON or console output
- get_logger(): Get a structlog logger instance
- Correlation ID support via context variables

Usage:
    from chaoscypher_core.utils.logging import configure_logging, get_logger

    # Configure once at startup
    configure_logging(use_json=False, log_level="INFO")

    # Use throughout application
    logger = get_logger(__name__)
    logger.info("event_name", key=value, ...)
"""

from chaoscypher_core.utils.logging.config import (
    configure_logging,
    get_logger,
)


__all__ = [
    "configure_logging",
    "get_logger",
]
