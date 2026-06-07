# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Service.

Aggregates system health checks from all subsystems into a single response.
Delegates to probe classes registered in a HealthRegistry, converting
ProbeResults to HealthCheckItems for backwards-compatible API responses.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.services.events.health.probes import (
    CloudProviderProbe,
    DatabaseProbe,
    DiskSpaceProbe,
    EmbeddingProbe,
    ErrorRateProbe,
    GraphProbe,
    ModelProbe,
    OllamaProbe,
    QueueProbe,
    SearchIndexProbe,
    WorkerProbe,
)
from chaoscypher_core.services.events.health.registry import HealthRegistry
from chaoscypher_cortex.features.health.models import (
    HealthCheckItem,
    HealthCheckResponse,
)


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_cortex.shared.health.probes import HealthProbe

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 5.0


class HealthService:
    """Aggregates health checks from all subsystems.

    Creates a HealthRegistry and registers probes based on the current
    provider configuration. Probes are framework-agnostic classes from
    core that accept plain callables for their dependencies.
    """

    def __init__(
        self,
        settings: Settings,
        queue_client: Any | None = None,
        search_service: Any | None = None,
        counts_service: Any | None = None,
        search_repository: Any | None = None,
        probes: list[HealthProbe] | None = None,
    ) -> None:
        """Initialize with dependencies and register probes.

        Args:
            settings: Application settings.
            queue_client: Async Valkey client (None if unavailable).
            search_service: Search service for index stats.
            counts_service: Counts service for graph stats.
            search_repository: SearchRepository for reindex status.
            probes: Injected sibling-feature probes (e.g. SearchHealthProbe).
                Results are folded into the response alongside inline probes.
        """
        self._settings = settings
        self._cache: tuple[float, HealthCheckResponse | None] = (0.0, None)
        self._registry = HealthRegistry()
        self._probes: list[HealthProbe] = probes or []

        self._register_probes(
            queue_client=queue_client,
            search_service=search_service,
            counts_service=counts_service,
            search_repository=search_repository,
        )

    @property
    def registry(self) -> HealthRegistry:
        """Expose the underlying HealthRegistry instance."""
        return self._registry

    def _register_probes(
        self,
        queue_client: Any | None,
        search_service: Any | None,
        counts_service: Any | None,
        search_repository: Any | None,
    ) -> None:
        """Create and register probes based on provider config.

        Args:
            queue_client: Async Valkey client (None if unavailable).
            search_service: Search service for index stats.
            counts_service: Counts service for graph stats.
            search_repository: SearchRepository for reindex status.
        """
        provider = self._settings.llm.chat_provider

        # Provider-specific probes
        if provider == "ollama":
            ollama_probe = OllamaProbe(
                base_url=self._settings.llm.primary_ollama_url,
                timeout=self._settings.timeouts.ollama_verify_timeout,
            )
            self._registry.register(ollama_probe)
            self._ollama_probe = ollama_probe

            # Wrap property access in a plain callable for ModelProbe
            def _get_installed() -> list[str]:
                return ollama_probe.installed_models

            # Chat model (always registered)
            self._registry.register(
                ModelProbe(
                    model_name=self._settings.llm.ollama_chat_model,
                    label="Chat model",
                    probe_name="chat_model",
                    installed_models_fn=_get_installed,
                )
            )

            # Extraction model (only if configured)
            extraction_model = self._settings.llm.ollama_extraction_model
            if extraction_model:
                self._registry.register(
                    ModelProbe(
                        model_name=extraction_model,
                        label="Extraction model",
                        probe_name="extraction_model",
                        installed_models_fn=_get_installed,
                    )
                )

            # Vision model (only if configured)
            vision_model = self._settings.llm.ollama_vision_model
            if vision_model:
                self._registry.register(
                    ModelProbe(
                        model_name=vision_model,
                        label="Vision model",
                        probe_name="vision_model",
                        installed_models_fn=_get_installed,
                    )
                )
        else:
            # Cloud providers (openai, anthropic, gemini)
            key_map = {
                "openai": "openai_api_key",
                "anthropic": "anthropic_api_key",
                "gemini": "gemini_api_key",
            }
            attr_name = key_map.get(provider)
            api_key = getattr(self._settings.llm, attr_name, None) if attr_name else None
            self._registry.register(CloudProviderProbe(provider=provider, api_key=api_key))

        # Embedding probe
        self._registry.register(EmbeddingProbe(health_check_fn=self._get_embedding_health))

        # Queue probe
        ping_fn = queue_client.ping if queue_client else None
        self._registry.register(QueueProbe(ping_fn=ping_fn))

        # Worker probes
        self._registry.register(
            WorkerProbe(
                queue_name="llm",
                probe_name="llm_worker",
                health_fn=(
                    self._make_worker_health_fn(queue_client, "llm") if queue_client else None
                ),
            )
        )
        self._registry.register(
            WorkerProbe(
                queue_name="operations",
                probe_name="ops_worker",
                health_fn=(
                    self._make_worker_health_fn(queue_client, "operations")
                    if queue_client
                    else None
                ),
            )
        )

        # Search index probe
        stats_fn = search_service.get_stats if search_service else None
        needs_reindex_fn = None
        if search_repository and getattr(search_repository, "needs_full_reindex", None) is not None:
            needs_reindex_fn = lambda: getattr(search_repository, "needs_full_reindex", False)  # noqa: E731
        self._registry.register(
            SearchIndexProbe(stats_fn=stats_fn, needs_reindex_fn=needs_reindex_fn)
        )

        # Graph probe
        counts_fn = None
        if counts_service:
            from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS

            counts_fn = lambda: counts_service.get_counts(  # noqa: E731
                system_template_ids=SYSTEM_TEMPLATE_IDS
            )
        self._registry.register(GraphProbe(counts_fn=counts_fn))

        # --- New probes (disk, error rate, database) ---

        # Disk space probe
        hm = self._settings.health_monitor
        self._registry.register(
            DiskSpaceProbe(
                path=str(self._settings.paths.data_dir),
                warn_bytes=hm.disk_warn_bytes,
                error_bytes=hm.disk_error_bytes,
            )
        )

        # Error rate probe (needs queue client for task stats)
        if queue_client:
            self._registry.register(
                ErrorRateProbe(
                    stats_fn=self._make_error_rate_fn(queue_client),
                    window_size=hm.error_rate_window,
                    warn_threshold=hm.error_rate_warn,
                    error_threshold=hm.error_rate_error,
                )
            )

        # Database probe
        self._registry.register(DatabaseProbe(adapter_fn=self._make_database_adapter_fn()))

    @staticmethod
    async def _get_embedding_health() -> Any:
        """Get embedding provider health via the shared singleton.

        Returns:
            Embedding health result object.
        """
        from chaoscypher_core.repo_factories.embedding_factory import (
            get_embedding_service,
        )

        provider = get_embedding_service()
        return await provider.check_health()

    @staticmethod
    def _make_worker_health_fn(queue_client: Any, queue_name: str) -> Any:
        """Create an async callable that reads a worker's health hash.

        Args:
            queue_client: Async Valkey client.
            queue_name: Queue name ("llm" or "operations").

        Returns:
            Async callable returning the health hash dict.
        """
        health_key = f"queue:{queue_name}:health"

        async def _fn() -> dict[str | bytes, Any]:
            """Fetch the worker health hash for this queue."""
            # redis-py stubs widen hgetall() to Awaitable | dict — narrow with cast.
            result: dict[str | bytes, Any] = await cast("Any", queue_client.hgetall(health_key))
            return result

        return _fn

    @staticmethod
    def _make_error_rate_fn(queue_client: Any) -> Any:
        """Create an async callable that reads recent task failure stats.

        Args:
            queue_client: Async Valkey client.

        Returns:
            Async callable returning {"total": int, "failed": int}.
        """

        async def _fn() -> dict[str, int]:
            """Sum the completed/failed counters across both worker queues."""
            # Read completed + failed counters from Valkey
            # These are maintained by the queue worker's atomic complete
            total = 0
            failed = 0
            for queue_name in ("llm", "operations"):
                completed_key = f"queue:{queue_name}:stats:completed"
                failed_key = f"queue:{queue_name}:stats:failed"
                c = await queue_client.get(completed_key)
                f = await queue_client.get(failed_key)
                total += int(c or 0) + int(f or 0)
                failed += int(f or 0)
            return {"total": total, "failed": failed}

        return _fn

    def _make_database_adapter_fn(self) -> Any:
        """Create a callable that returns a connected SQLite adapter.

        Returns:
            Zero-arg callable returning an adapter with quick_check().
        """

        def _fn() -> Any:
            from chaoscypher_core.database import get_sqlite_adapter

            return get_sqlite_adapter(database_name=self._settings.current_database)

        return _fn

    async def check_health(self, *, detailed: bool = True) -> HealthCheckResponse:
        """Run all health checks and return consolidated response.

        When ``detailed=False`` (unauthenticated callers), skips the full
        probe run and returns only ``{healthy, status}`` so that LAN
        scanners cannot fingerprint the deployed LLM stack, queue worker
        concurrency, or graph stats.

        Returns cached response if within TTL. Uses the registry to
        execute all probes, then converts ProbeResults to HealthCheckItems
        for backwards-compatible API responses.

        Args:
            detailed: When False, return a minimal ``{healthy, status}``
                payload.  When True (default), run all probes and return
                the full payload.

        Returns:
            HealthCheckResponse with all check results (detailed) or just
            the top-level status (minimal).
        """
        if not detailed:
            return await self._minimal_health()

        return await self._full_health()

    async def _minimal_health(self) -> HealthCheckResponse:
        """Return a lightweight response for unauthenticated callers.

        Uses the cached full response when available (avoids a redundant
        probe run), falling back to a static ``healthy=True`` placeholder
        when no cached result exists.  Either way, ``checks`` is omitted
        so no subsystem details are leaked.

        Returns:
            HealthCheckResponse with ``checks=None``.
        """
        _cached_time, cached_response = self._cache
        # No cache yet — fall back to a conservative healthy=True placeholder.
        # The full check will populate the cache on the next authed call.
        healthy = cached_response.healthy if cached_response is not None else True

        status = "ok" if healthy else "degraded"
        return HealthCheckResponse(healthy=healthy, status=status)

    async def _full_health(self) -> HealthCheckResponse:
        """Run all probes and return the complete health snapshot.

        Returns:
            HealthCheckResponse with all check results.
        """
        now = time.monotonic()
        cached_time, cached_response = self._cache
        if cached_response and (now - cached_time) < _CACHE_TTL_SECONDS:
            return cached_response

        provider = self._settings.llm.chat_provider

        # Ollama model probes depend on OllamaProbe running first to
        # populate installed_models. The registry runs probes in
        # registration order, so OllamaProbe was registered first.
        # However, since ModelProbes read installed_models via a callable
        # that reads the OllamaProbe's state, and check_all runs
        # sequentially, this works correctly.
        results = await self._registry.check_all()

        # Handle the special Ollama case: when Ollama is unreachable,
        # skip model checks (keep only the ollama probe error).
        # When Ollama is ok but extraction model is not configured,
        # inject a synthetic warning.
        checks: dict[str, HealthCheckItem] = {}

        for probe_name, result in results.items():
            # Skip model probes when Ollama is unreachable
            if provider == "ollama" and probe_name in (
                "chat_model",
                "extraction_model",
                "vision_model",
            ):
                ollama_result = results.get("ollama")
                if ollama_result and ollama_result.status != "ok":
                    continue

            checks[probe_name] = HealthCheckItem(
                status=result.status,
                message=result.message,
                details=result.details or None,
                category=result.category,
                auto_recoverable=result.auto_recoverable,
            )

        # Inject synthetic warning for unconfigured extraction model
        if provider == "ollama":
            ollama_result = results.get("ollama")
            extraction_model = self._settings.llm.ollama_extraction_model
            if ollama_result and ollama_result.status == "ok" and not extraction_model:
                checks["extraction_model"] = HealthCheckItem(
                    status="warning",
                    message="Not configured (using chat model)",
                    category="service",
                    auto_recoverable=True,
                )

        # Fold injected probe results into the response. Sibling-feature
        # probes (e.g. SearchHealthProbe) run here so HealthService itself
        # stays ignorant of their internals. Results override any placeholder
        # emitted by the inline registry for the same wire-contract key.
        for probe in self._probes:
            key, item = await self._run_injected_probe(probe)
            checks[key] = item

        # Overall health — only evaluate critical checks that are present
        if provider == "ollama":
            critical_keys = ["ollama", "chat_model", "queue", "llm_worker", "ops_worker"]
        else:
            critical_keys = ["provider", "queue", "llm_worker", "ops_worker"]
        healthy = all(checks[name].status != "error" for name in critical_keys if name in checks)

        status = "ok" if healthy else "degraded"
        response = HealthCheckResponse(healthy=healthy, status=status, checks=checks)
        self._cache = (now, response)
        return response

    async def _run_injected_probe(self, probe: HealthProbe) -> tuple[str, HealthCheckItem]:
        """Execute an injected probe and translate its HealthStatus.

        Translates sibling-feature ``HealthStatus`` results to the
        ``HealthCheckItem`` wire contract. Handles exceptions defensively so
        one failing probe doesn't break the whole response.

        Args:
            probe: The probe to execute.

        Returns:
            Tuple of (response key, HealthCheckItem). The response key is
            the probe's wire-contract key (see ``_PROBE_KEY_MAP``), defaulting
            to the probe's own ``name``.
        """
        key = _PROBE_KEY_MAP.get(probe.name, probe.name)
        try:
            status = await probe.check()
        except Exception:
            # Log the exception with trace, but surface a sanitized message
            # to the client — str(exc) can leak DB paths, connection strings,
            # and other internals that must not reach health endpoint callers.
            logger.warning("health_probe_exception", probe_name=probe.name, exc_info=True)
            return key, HealthCheckItem(
                status="error",
                message="probe failed",
                category="operational",
                auto_recoverable=True,
            )

        details = _translate_probe_metrics(probe.name, status.metrics)
        return key, HealthCheckItem(
            status="ok" if status.ok else "error",
            message=status.detail or ("ok" if status.ok else "probe failed"),
            details=details,
            category="operational",
            auto_recoverable=True,
        )


# Map injected probe names to the response keys consumed by clients.
# The frontend (``MiniSystemStatus/HealthSections.tsx``) reads ``search_index``
# from ``health.checks`` — SearchHealthProbe emits name "search" but we
# preserve the existing wire contract for consumers.
_PROBE_KEY_MAP: dict[str, str] = {
    "search": "search_index",
}


def _translate_probe_metrics(probe_name: str, metrics: dict | None) -> dict[str, Any] | None:
    """Translate probe metric keys to existing wire-contract detail keys.

    SearchHealthProbe emits ``fulltext_doc_count``/``vector_index_size``;
    the existing ``search_index`` wire contract uses
    ``fulltext_count``/``vector_count`` (consumed by the dashboard).

    Args:
        probe_name: Source probe name (drives the translation table).
        metrics: Raw metrics dict from the probe (may be None).

    Returns:
        Translated details dict, or None if metrics is None/empty.
    """
    if not metrics:
        return None

    if probe_name == "search":
        return {
            "fulltext_count": metrics.get("fulltext_doc_count", 0),
            "vector_count": metrics.get("vector_index_size", 0),
            "vector_dimension": metrics.get("vector_dimension", 0),
        }

    return dict(metrics)
