# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Boot-time setup for the Cortex service.

Runs ``configure_logging()`` at module-load time so anything importing
cortex code (tests, the factory, middleware, lifespan helpers) gets
structlog configured with the project's processors — not the heavy
default traceback renderer.

Also exposes `APP_VERSION` and `_SCHEMA_ONLY`, split out of ``main.py``
so the factory can read them without a circular dependency on ``main``.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

import structlog

from chaoscypher_core.utils.logging.app_config import configure_logging


# Configure logging FIRST. Every cortex module that creates a structlog
# logger imports transitively from this module, so this runs before any
# ``logger.info`` / ``logger.warning`` call anywhere in the package.
_use_json_logging = os.getenv("USE_JSON_LOGGING", "false").lower() == "true"
_log_level = os.getenv("LOG_LEVEL", "INFO")
configure_logging(use_json=_use_json_logging, log_level=_log_level)


logger = structlog.get_logger(__name__)


try:
    APP_VERSION = version("chaoscypher-cortex")
except PackageNotFoundError:
    APP_VERSION = "dev"


# Module-level flag for schema-only mode (Dockerfile types-builder stage).
# When True, the factory returns an app suitable only for OpenAPI schema
# extraction — no disk writes for session secret, dummy values used instead.
_SCHEMA_ONLY = os.getenv("CHAOSCYPHER_SCHEMA_ONLY", "").lower() in ("1", "true", "yes")
