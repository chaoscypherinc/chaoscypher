# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker Configuration - Defaults with Optional User Override.

This module provides worker configuration with sensible defaults baked in.
Users can optionally create /data/workers.yaml to override any settings.

If the config file doesn't exist, defaults are used automatically (no file created).

NOTE: Default timeout and retry values are synchronized with backend/shared/config/__init__.py
      (TimeoutSettings and RetrySettings) to maintain consistency.

Config file format (/data/workers.yaml)::

    llm_worker:
      max_concurrent: 2
      timeout: 600

    operations_worker:
      max_concurrent: 16
      timeout: 7200
"""

import copy
import functools
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

from chaoscypher_core import policy
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS


logger = structlog.get_logger(__name__)


# ============================================================================
# Safety clamps for worker config — chosen to stay within Valkey + supervisor
# tolerances. Not operator-tunable (they encode infrastructure constraints).
# Expand only after verifying the higher bound has been load-tested.
# ============================================================================
_MAX_CONCURRENT_HARD_CAP = 64
_MAX_TIMEOUT_HARD_CAP_SECONDS = policy.SECONDS_PER_DAY  # 86400
_MAX_TRIES_HARD_CAP = 20
_MIN_TIMEOUT_FLOOR_SECONDS = 60


# ============================================================================
# Neuron-Specific Settings
# ============================================================================


class NeuronSettings(BaseModel):
    """Neuron-specific configuration values.

    Centralises constants that were previously hardcoded across the
    neuron package (settings sync, quality-score handler, etc.).
    """

    settings_sync_reconnect_delay: int = Field(
        default=5,
        ge=1,
        description="Initial reconnect delay (seconds) for the settings pub/sub listener",
    )
    settings_sync_max_reconnect_delay: int = Field(
        default=60,
        ge=1,
        description="Maximum reconnect delay (seconds) for the settings pub/sub listener",
    )
    max_quality_score_batch: int = Field(
        default=500,
        ge=1,
        description="Maximum source IDs accepted in a single quality-score recalculation request",
    )
    run_worker_max_consecutive_failures: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum consecutive run_worker() failures the circuit-breaker will absorb "
            "before re-raising and letting the container restart take over. Guards "
            "against a poison-pill crash producing a CPU-burning restart loop."
        ),
    )
    run_worker_initial_backoff_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "Initial backoff (seconds) the run_worker() circuit-breaker sleeps after "
            "the first failure. Doubles each subsequent failure up to "
            "run_worker_max_backoff_seconds."
        ),
    )
    run_worker_max_backoff_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description=(
            "Upper bound (seconds) on a single backoff sleep between run_worker() "
            "restart attempts. Caps the exponential growth so the breaker never "
            "stalls longer than this between attempts."
        ),
    )


@functools.cache
def get_neuron_settings() -> NeuronSettings:
    """Return the singleton NeuronSettings instance.

    Cached so import-time construction is deferred and subsequent calls
    are free.
    """
    return NeuronSettings()


# ============================================================================
# DEFAULT CONFIGURATION (Synchronized with Settings)
# ============================================================================


def _get_default_config() -> dict[str, Any]:
    """Get default worker configuration synchronized with centralized Settings.

    Returns defaults from TimeoutSettings and RetrySettings to prevent drift.
    Falls back to hardcoded values if Settings cannot be loaded.

    For LLM worker:
    - If multiple Ollama instances are configured, max_concurrent is set to the
      number of instances to allow parallel processing across instances.
    - The load balancer handles per-instance concurrency control.
    """
    try:
        # Import at function level to avoid circular imports
        from chaoscypher_core.app_config import (
            RetrySettings,
            TimeoutSettings,
            WorkerSettings,
            get_settings,
        )

        # Get defaults from centralized Settings
        timeouts = TimeoutSettings()
        retries = RetrySettings()
        workers = WorkerSettings()

        # Calculate LLM worker max_concurrent based on Ollama instances
        llm_max_concurrent = 1  # Default: single instance / non-Ollama
        try:
            settings = get_settings()
            workers = settings.workers
            if settings.llm.chat_provider == "ollama":
                instances = settings.llm.ollama_instances or []
                if instances:
                    enabled_count = sum(1 for i in instances if getattr(i, "enabled", True))
                    llm_max_concurrent = max(enabled_count, 1)
                    logger.info(
                        "llm_worker_max_concurrent_from_instances",
                        instance_count=len(instances),
                        enabled_count=enabled_count,
                        max_concurrent=llm_max_concurrent,
                    )
        except (ImportError, AttributeError, ValueError) as e:
            logger.debug(
                "could_not_determine_instance_count",
                error=str(e),
                fallback_max_concurrent=llm_max_concurrent,
            )

        return {
            "llm_worker": {
                "max_concurrent": llm_max_concurrent,
                "queue_name": QUEUE_LLM,
                "timeout": timeouts.llm_worker_default,  # 3600 = 1 hour
                "max_tries": retries.llm_worker_max_tries,  # 5
            },
            "operations_worker": {
                "max_concurrent": workers.operations_max_concurrent,
                "queue_name": QUEUE_OPERATIONS,
                "timeout": timeouts.operations_worker_default,  # 3600 = 1 hour
                "max_tries": retries.operations_worker_max_tries,  # 5
            },
        }
    except (ImportError, KeyError, ValueError, FileNotFoundError, OSError) as e:
        logger.warning(
            "settings_load_failed_using_fallbacks",
            error=str(e),
            fallback_source="hardcoded_defaults",
        )
        return {
            "llm_worker": {
                "max_concurrent": 1,
                "queue_name": QUEUE_LLM,
                "timeout": 3600,
                "max_tries": 5,
            },
            "operations_worker": {
                "max_concurrent": 8,  # Must match WorkerSettings.operations_max_concurrent default
                "queue_name": QUEUE_OPERATIONS,
                "timeout": 3600,
                "max_tries": 5,
            },
        }


@functools.cache  # Startup-only; not invalidated on hot-reload (safe: load_worker_config called once)
def _get_defaults() -> dict[str, Any]:
    """Return the default config, building it on first call.

    Uses functools.cache to lazy-build on first access, avoiding
    calls to get_settings() at import time (before structlog is configured).
    """
    return _get_default_config()


# ============================================================================
# Configuration Loading
# ============================================================================


def load_worker_config(worker_type: str) -> dict[str, Any]:
    """Load worker configuration for the specified worker type.

    Looks for /data/workers.yaml. If found, overlays user settings on
    top of defaults. If not found, uses defaults (no file created).

    Args:
        worker_type: Either "llm_worker" or "operations_worker"

    Returns:
        Configuration dictionary for the worker

    """
    defaults = _get_defaults()
    if worker_type not in defaults:
        msg = f"Unknown worker type: {worker_type}"
        raise ValueError(msg)

    # Start with defaults
    config = copy.deepcopy(defaults[worker_type])

    # Check for user override file
    try:
        from chaoscypher_core.app_config import PathSettings

        path_settings = PathSettings()
        user_config_path = Path(path_settings.data_dir) / path_settings.workers_config_filename
    except Exception:
        logger.debug("pathsettings_import_failed_using_fallback")
        from chaoscypher_core.settings import PathSettings as CorePathSettings

        _core_paths = CorePathSettings()
        user_config_path = Path(_core_paths.data_dir) / _core_paths.workers_config_filename

    if user_config_path.exists():
        logger.info(
            "user_config_found",
            config_path=str(user_config_path),
            worker_type=worker_type,
        )
        try:
            with open(user_config_path) as f:
                user_config = yaml.safe_load(f)

            if user_config and worker_type in user_config:
                user_overrides = user_config[worker_type]
                allowed_keys = {"max_concurrent", "timeout", "max_tries"}
                filtered = {k: v for k, v in user_overrides.items() if k in allowed_keys}
                rejected = set(user_overrides) - allowed_keys
                if rejected:
                    logger.warning(
                        "user_config_unknown_keys_ignored",
                        worker_type=worker_type,
                        rejected_keys=sorted(rejected),
                    )
                logger.info(
                    "user_config_overrides_applied",
                    worker_type=worker_type,
                    override_keys=list(filtered.keys()),
                )
                config.update(filtered)
        except Exception as e:
            logger.exception(
                "user_config_load_failed",
                worker_type=worker_type,
                error=str(e),
            )
            logger.warning(
                "using_default_configuration",
                worker_type=worker_type,
                reason="user_config_load_error",
            )
    else:
        logger.info(
            "user_config_not_found_using_defaults",
            config_path=str(user_config_path),
            worker_type=worker_type,
        )

    # Validate numeric types before clamping (YAML booleans silently coerce: True == 1)
    numeric_keys = ("max_concurrent", "timeout", "max_tries")
    for key in numeric_keys:
        val = config.get(key)
        if val is not None and (not isinstance(val, (int, float)) or isinstance(val, bool)):
            logger.warning("worker_config_invalid_type", key=key, value=val, expected="numeric")
            del config[key]  # Let it fall back to default from base config

    # Clamp values to safe ranges
    config["max_concurrent"] = max(
        1, min(config.get("max_concurrent", 1), _MAX_CONCURRENT_HARD_CAP)
    )
    config["timeout"] = max(
        _MIN_TIMEOUT_FLOOR_SECONDS, min(config.get("timeout", 3600), _MAX_TIMEOUT_HARD_CAP_SECONDS)
    )
    config["max_tries"] = max(1, min(config.get("max_tries", 5), _MAX_TRIES_HARD_CAP))

    logger.info(
        "worker_config_loaded",
        worker_type=worker_type,
        queue_name=config["queue_name"],
        max_concurrent=config["max_concurrent"],
        timeout_seconds=config["timeout"],
        max_tries=config["max_tries"],
    )

    return dict(config)
