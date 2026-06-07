# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama Load Balancer - Distributes LLM requests across multiple instances.

Provides hot-reloadable load balancing with per-instance semaphores,
health monitoring, and automatic failover.

Architecture:
    Settings Change → reload_config() → Rebuild instance pool
    Request → acquire_instance() → Get available instance → Execute → release_instance()
"""

from __future__ import annotations

import asyncio
import random
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import LLMSettings

from chaoscypher_core.adapters.llm.limit import (
    PrioritySemaphore,
    update_llm_semaphore_config,
)


logger = structlog.get_logger(__name__)


class OllamaLoadBalancer:
    """Manages multiple Ollama instances with per-instance semaphores.

    Features:
    - Hot reload: Update instances without restart via reload_config()
    - Per-instance semaphores: Prevents overloading any single instance
    - Load balancing strategies: round_robin, least_loaded, random
    - Health monitoring: Auto-disables unhealthy instances
    - Graceful draining: Waits for in-flight requests before removing instances

    Example:
        >>> balancer = OllamaLoadBalancer()
        >>> await balancer.reload_config(settings.llm)
        >>> instance_id, provider = await balancer.acquire_instance(high_priority=True)
        >>> try:
        ...     result = await provider.chat(messages)
        ... finally:
        ...     balancer.release_instance(instance_id)
    """

    def __init__(self, drain_max_iterations: int | None = None) -> None:
        """Initialize empty load balancer (call reload_config to populate).

        Args:
            drain_max_iterations: Max poll iterations when draining an instance
                during shutdown. ``None`` falls back to the ``RetrySettings``
                class default. Callers that hold engine settings should inject
                ``settings.retries.ollama_drain_max_iterations`` here; the
                module-level ``get_ollama_load_balancer()`` accessor constructs
                with no args and therefore always uses the class default.

        """
        # Lazy import to avoid circular import during package init.
        from chaoscypher_core.settings import RetrySettings

        resolved_drain = (
            drain_max_iterations
            if drain_max_iterations is not None
            else RetrySettings().ollama_drain_max_iterations
        )

        self._instances: dict[str, dict[str, Any]] = {}  # id -> instance config
        self._providers: dict[str, Any] = {}  # id -> OllamaProvider instance
        self._semaphores: dict[str, PrioritySemaphore] = {}  # id -> semaphore
        self._strategy: str = "round_robin"
        self._round_robin_index: int = 0
        self._lock = asyncio.Lock()
        self._config_version: int = 0
        self._global_config: dict[str, Any] = {}  # Shared model config
        self._drain_max_wait: int = resolved_drain
        self._drain_check_interval: float = 1.0

    def configure_drain(
        self,
        max_wait: int,
        check_interval: float,
    ) -> None:
        """Set drain timeout parameters (call before reload_config if overriding defaults).

        Args:
            max_wait: Maximum seconds to wait when draining an instance.
            check_interval: Seconds between drain checks.

        """
        self._drain_max_wait = max_wait
        self._drain_check_interval = check_interval

    async def reload_config(self, llm_settings: LLMSettings) -> None:
        """Hot reload instances from updated settings.

        Gracefully handles:
        - Adding new instances
        - Removing old instances (waits for in-flight requests)
        - Updating strategy

        Args:
            llm_settings: LLMSettings object with ollama_instances list
        """
        async with self._lock:
            # ollama_instances is non-empty by default (LLMSettings seeds a
            # single "default" instance), so this should always have entries.
            instances_config = llm_settings.ollama_instances or []

            # Build new instance map (only enabled instances).
            # ``ollama_instances`` is typed ``list[OllamaInstance]``; settings
            # validation rejects bare dicts before they reach the load balancer.
            new_instances: dict[str, dict[str, Any]] = {}
            for inst in instances_config:
                inst_dict = inst.model_dump()
                if inst_dict.get("enabled", True):
                    new_instances[inst_dict["id"]] = inst_dict

            if not new_instances:
                logger.warning(
                    "load_balancer_no_enabled_instances",
                    configured=len(instances_config),
                )

            # Store shared config for provider creation
            # llm_max_concurrent is set to instance count to match available capacity
            instance_count = len(new_instances)
            self._global_config = {
                "ollama_chat_model": llm_settings.ollama_chat_model,
                "ollama_num_batch": llm_settings.ollama_num_batch,
                "ollama_num_ctx": llm_settings.ollama_num_ctx,
                "ollama_num_parallel": llm_settings.ollama_num_parallel,
                "ollama_num_thread": llm_settings.ollama_num_thread,
                "ai_temperature": llm_settings.ai_temperature,
                "ai_max_tokens": llm_settings.ai_max_tokens,
                "stream_chunk_timeout": llm_settings.stream_chunk_timeout,
                "ollama_health_check_timeout": llm_settings.ollama_health_check_timeout,
                "ollama_recovery_delay": llm_settings.ollama_recovery_delay,
                "llm_max_concurrent": instance_count,  # Match instance count
                "llm_reserved_interactive": llm_settings.llm_reserved_interactive,
                "llm_enable_priority": llm_settings.llm_enable_priority,
            }

            # Remove instances that are no longer in config
            removed_ids = set(self._providers.keys()) - set(new_instances.keys())
            for instance_id in removed_ids:
                await self._drain_and_remove_instance(instance_id)

            # Add new instances
            added_ids = set(new_instances.keys()) - set(self._providers.keys())
            for instance_id in added_ids:
                inst_config = new_instances[instance_id]
                self._add_instance(instance_id, inst_config)

            # Update instance configs (URL changes, etc.)
            for instance_id, inst_config in new_instances.items():
                if instance_id in self._instances:
                    old_url = self._instances[instance_id].get("base_url")
                    new_url = inst_config.get("base_url")
                    if old_url != new_url:
                        # URL changed - recreate provider
                        await self._drain_and_remove_instance(instance_id)
                        self._add_instance(instance_id, inst_config)
                    else:
                        # Just update config (name, etc.)
                        self._instances[instance_id] = inst_config

            self._instances = new_instances
            self._strategy = llm_settings.ollama_load_balancing
            self._config_version += 1

            # Update global semaphore to match instance count
            # This ensures concurrent requests match available instances
            if instance_count > 0:
                reserved = llm_settings.llm_reserved_interactive
                await update_llm_semaphore_config(
                    max_concurrent=instance_count,
                    reserved_high_priority=reserved,
                )
                logger.info(
                    "global_semaphore_updated_from_instances",
                    max_concurrent=instance_count,
                    reserved_high_priority=reserved,
                )

            logger.info(
                "load_balancer_config_reloaded",
                instances=len(new_instances),
                strategy=self._strategy,
                version=self._config_version,
                instance_ids=list(new_instances.keys()),
            )

    def _add_instance(self, instance_id: str, inst_config: dict[str, Any]) -> None:
        """Add a new instance with provider and semaphore."""
        # Build provider config — `base_url` is the per-instance URL the
        # OllamaProvider reads at __init__ time.
        provider_config = {
            **self._global_config,
            "base_url": inst_config["base_url"],
        }

        # Create provider and semaphore (lazy import to avoid loading SDK at startup)
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider

        self._providers[instance_id] = OllamaProvider(provider_config)
        self._semaphores[instance_id] = PrioritySemaphore(
            max_concurrent=1,  # One slot per instance
            reserved_high_priority=0,
        )
        self._instances[instance_id] = inst_config

        logger.info(
            "load_balancer_instance_added",
            instance_id=instance_id,
            name=inst_config.get("name"),
            base_url=inst_config.get("base_url"),
        )

    async def _drain_and_remove_instance(self, instance_id: str) -> None:
        """Wait for in-flight requests then remove instance."""
        if instance_id not in self._semaphores:
            return

        sem = self._semaphores[instance_id]

        # Wait for in-flight requests to complete (configurable timeout)
        for _ in range(self._drain_max_wait):
            if sem.active_count == 0:
                break
            logger.info(
                "load_balancer_draining_instance",
                instance_id=instance_id,
                active_count=sem.active_count,
            )
            await asyncio.sleep(self._drain_check_interval)

        # Remove instance
        self._providers.pop(instance_id, None)
        self._semaphores.pop(instance_id, None)
        self._instances.pop(instance_id, None)

        logger.info("load_balancer_instance_removed", instance_id=instance_id)

    @asynccontextmanager
    async def acquire_instance(self, high_priority: bool = False) -> Any:
        """Get an available instance based on load balancing strategy.

        Blocks until an instance slot is available. Use as async context manager.

        Args:
            high_priority: If True, jumps ahead in queue

        Yields:
            Tuple of (instance_id, provider)

        Raises:
            RuntimeError: If no instances are configured

        Example:
            >>> async with balancer.acquire_instance(high_priority=True) as (inst_id, provider):
            ...     result = await provider.chat(messages)
        """
        if not self._providers:
            raise RuntimeError("No Ollama instances configured")

        # Select instance based on strategy
        instance_id = await self._select_instance()
        sem = self._semaphores[instance_id]

        # Use the semaphore's context manager
        async with sem.acquire(high_priority=high_priority):
            logger.debug(
                "load_balancer_instance_acquired",
                instance_id=instance_id,
                strategy=self._strategy,
                high_priority=high_priority,
            )
            try:
                yield instance_id, self._providers[instance_id]
            except Exception as e:
                # Mark instance as unhealthy if connection error
                error_str = str(e).lower()
                if "connect" in error_str or "connection" in error_str or "timeout" in error_str:
                    await self.mark_instance_unhealthy(instance_id, str(e))
                raise
            finally:
                logger.debug("load_balancer_instance_released", instance_id=instance_id)

    async def _select_instance(self) -> str:
        """Select instance based on configured strategy.

        Holds ``self._lock`` for the full duration of the read/write
        sequence: reads from ``self._instances`` / ``self._providers`` /
        ``self._semaphores`` and the increment of ``self._round_robin_index``
        would otherwise race with ``reload_config`` (which can pop entries
        mid-select).
        """
        async with self._lock:
            # Get healthy instances only
            healthy_ids = [
                inst_id
                for inst_id, inst in self._instances.items()
                if inst.get("healthy", True) and inst_id in self._providers
            ]

            if not healthy_ids:
                # Fall back to all enabled instances if none are healthy
                healthy_ids = list(self._providers.keys())

            if not healthy_ids:
                raise RuntimeError("No Ollama instances available")

            if self._strategy == "round_robin":
                idx = self._round_robin_index % len(healthy_ids)
                self._round_robin_index += 1
                return healthy_ids[idx]

            if self._strategy == "least_loaded":
                # Pick instance with fewest active + waiting requests
                return min(
                    healthy_ids,
                    key=lambda i: (
                        self._semaphores[i].active_count
                        + self._semaphores[i].high_priority_waiters.qsize()
                        + self._semaphores[i].low_priority_waiters.qsize()
                    ),
                )

            if self._strategy == "random":
                return random.choice(healthy_ids)  # noqa: S311 - not cryptographic

            # Default to round robin for unknown strategies
            idx = self._round_robin_index % len(healthy_ids)
            self._round_robin_index += 1
            return healthy_ids[idx]

    def get_total_capacity(self) -> int:
        """Get total concurrent slots across all instances."""
        return len(self._providers)

    def get_stats(self) -> dict[str, Any]:
        """Get load balancer statistics."""
        instance_stats = {}
        for inst_id, sem in self._semaphores.items():
            instance_stats[inst_id] = {
                "name": self._instances.get(inst_id, {}).get("name", inst_id),
                "base_url": self._instances.get(inst_id, {}).get("base_url"),
                "healthy": self._instances.get(inst_id, {}).get("healthy", True),
                "active": sem.active_count,
                "waiting_high": sem.high_priority_waiters.qsize(),
                "waiting_low": sem.low_priority_waiters.qsize(),
            }

        return {
            "strategy": self._strategy,
            "config_version": self._config_version,
            "total_instances": len(self._providers),
            "total_capacity": self.get_total_capacity(),
            "instances": instance_stats,
        }

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all instances and update their status.

        Health check HTTP calls run without holding the lock to avoid blocking
        acquire_instance() during network I/O. Instance state mutations are
        then batched under the lock to prevent races with reload_config().

        Returns:
            Dict mapping instance_id to health status.
        """
        # Snapshot providers outside the lock — safe because dict iteration
        # on a stale reference only risks missing a just-added instance,
        # which will be picked up on the next health-check cycle.
        providers_snapshot = dict(self._providers)

        # Phase 1: Collect health results (no lock — network I/O)
        check_results: dict[str, tuple[bool, str | None]] = {}
        for inst_id, provider in providers_snapshot.items():
            try:
                is_healthy = await provider.check_health()
                check_results[inst_id] = (is_healthy, None)
                logger.info(
                    "load_balancer_health_check",
                    instance_id=inst_id,
                    healthy=is_healthy,
                )
            except Exception as e:
                check_results[inst_id] = (False, str(e))
                logger.warning(
                    "load_balancer_health_check_failed",
                    instance_id=inst_id,
                    error=str(e),
                )

        # Phase 2: Batch-update instance state under the lock
        now_iso = datetime.now(UTC).isoformat()
        async with self._lock:
            for inst_id, (is_healthy, error) in check_results.items():
                if inst_id not in self._instances:
                    continue  # Instance removed during health checks
                self._instances[inst_id]["healthy"] = is_healthy
                self._instances[inst_id]["last_health_check"] = now_iso
                if is_healthy:
                    self._instances[inst_id]["last_error"] = None
                else:
                    self._instances[inst_id]["last_error"] = error

        return {inst_id: healthy for inst_id, (healthy, _) in check_results.items()}

    async def mark_instance_unhealthy(self, instance_id: str, error: str) -> None:
        """Mark an instance as unhealthy after a failure.

        Holds ``self._lock`` so the write cannot race with ``reload_config``
        popping the instance dict.

        Args:
            instance_id: The instance that failed
            error: Error message
        """
        async with self._lock:
            if instance_id in self._instances:
                self._instances[instance_id]["healthy"] = False
                self._instances[instance_id]["last_error"] = error
                self._instances[instance_id]["last_health_check"] = datetime.now(UTC).isoformat()
                logger.warning(
                    "load_balancer_instance_marked_unhealthy",
                    instance_id=instance_id,
                    error=error,
                )


# Global singleton instance (double-checked locking guards first-init from
# concurrent callers like cortex startup + neuron worker boot)
_global_load_balancer: OllamaLoadBalancer | None = None
_global_load_balancer_lock = threading.Lock()


def get_ollama_load_balancer() -> OllamaLoadBalancer:
    """Get or create the global Ollama load balancer.

    Returns:
        Global OllamaLoadBalancer instance
    """
    global _global_load_balancer

    if _global_load_balancer is None:
        with _global_load_balancer_lock:
            if _global_load_balancer is None:
                _global_load_balancer = OllamaLoadBalancer()

    return _global_load_balancer


async def reload_load_balancer_config(
    llm_settings: LLMSettings,
    drain_max_wait: int,
    drain_check_interval: float,
) -> None:
    """Reload the global load balancer configuration.

    Args:
        llm_settings: LLMSettings object with updated configuration.
        drain_max_wait: Max seconds to wait when draining an instance.
        drain_check_interval: Seconds between drain checks.
    """
    balancer = get_ollama_load_balancer()
    balancer.configure_drain(max_wait=drain_max_wait, check_interval=drain_check_interval)
    await balancer.reload_config(llm_settings)
