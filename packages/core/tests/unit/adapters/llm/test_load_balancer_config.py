# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for OllamaLoadBalancer config reload, selection, and health.

Exercises the previously-untested bulk of
``adapters/llm/load_balancer.py``:

* ``reload_config`` — seeding providers/semaphores from fake LLMSettings,
  skipping disabled instances, draining removed ones, recreating on URL change,
  storing strategy, bumping the config version, and updating the global semaphore.
* ``_select_instance`` — round_robin advancement, least_loaded minimisation,
  random selection, unknown-strategy fallback, unhealthy filtering with
  fallback-to-all, and the empty-pool RuntimeError.
* ``acquire_instance`` — yields (id, provider), errors with no providers, and
  marks an instance unhealthy on connection errors.
* ``_drain_and_remove_instance`` — not-present early return and the
  active-count drain loop.
* ``get_total_capacity`` / ``get_stats`` / ``health_check_all``.
* module-level ``get_ollama_load_balancer`` singleton + ``reload_load_balancer_config``.

The balancer is assembled with ``__new__`` + manual attribute wiring (mirroring
``test_load_balancer_locking.py``) so no Ollama SDK is loaded.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import chaoscypher_core.adapters.llm.load_balancer as lb_mod
from chaoscypher_core.adapters.llm.load_balancer import (
    OllamaLoadBalancer,
    get_ollama_load_balancer,
    reload_load_balancer_config,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _bare_balancer() -> OllamaLoadBalancer:
    """Assemble an OllamaLoadBalancer without running __init__ (no settings I/O)."""
    bal = OllamaLoadBalancer.__new__(OllamaLoadBalancer)
    bal._instances = {}
    bal._providers = {}
    bal._semaphores = {}
    bal._strategy = "round_robin"
    bal._round_robin_index = 0
    bal._lock = asyncio.Lock()
    bal._config_version = 0
    bal._global_config = {}
    bal._drain_max_wait = 3
    bal._drain_check_interval = 0.0
    return bal


def _fake_semaphore(active: int = 0, high: int = 0, low: int = 0) -> MagicMock:
    """A PrioritySemaphore stand-in exposing the attributes the balancer reads."""
    sem = MagicMock()
    sem.active_count = active
    sem.high_priority_waiters = MagicMock()
    sem.high_priority_waiters.qsize.return_value = high
    sem.low_priority_waiters = MagicMock()
    sem.low_priority_waiters.qsize.return_value = low
    return sem


def _instance_obj(
    *,
    id: str,  # noqa: A002 - mirrors model field
    base_url: str,
    enabled: bool = True,
    name: str | None = None,
) -> SimpleNamespace:
    """Fake OllamaInstance with a ``model_dump()`` like the real pydantic model."""
    data = {"id": id, "base_url": base_url, "enabled": enabled, "name": name or id}
    return SimpleNamespace(model_dump=lambda d=data: dict(d))


def _fake_llm_settings(
    instances: list[SimpleNamespace], strategy: str = "round_robin"
) -> SimpleNamespace:
    """Minimal LLMSettings stand-in for reload_config."""
    return SimpleNamespace(
        ollama_instances=instances,
        ollama_chat_model="qwen3:0.6b",
        ollama_num_batch=None,
        ollama_num_ctx=None,
        ollama_num_parallel=None,
        ollama_num_thread=None,
        ai_temperature=None,
        ai_max_tokens=None,
        stream_chunk_timeout=30.0,
        ollama_health_check_timeout=5.0,
        ollama_recovery_delay=0.0,
        llm_reserved_interactive=0,
        llm_enable_priority=False,
        ollama_load_balancing=strategy,
    )


# ---------------------------------------------------------------------------
# reload_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_config_seeds_providers_and_skips_disabled() -> None:
    """Enabled instances get providers + semaphores; disabled ones are skipped,
    the strategy is stored, the version bumps, and the global semaphore updates.
    """
    bal = _bare_balancer()
    settings = _fake_llm_settings(
        instances=[
            _instance_obj(id="a", base_url="http://a:11434"),
            _instance_obj(id="b", base_url="http://b:11434"),
            _instance_obj(id="c", base_url="http://c:11434", enabled=False),
        ],
        strategy="least_loaded",
    )

    update_sem = AsyncMock()
    with (
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.OllamaProvider",
            side_effect=lambda cfg: MagicMock(name="provider"),
        ),
        patch.object(lb_mod, "update_llm_semaphore_config", update_sem),
        patch.object(lb_mod, "PrioritySemaphore", side_effect=lambda **kw: _fake_semaphore()),
    ):
        await bal.reload_config(settings)

    assert set(bal._providers.keys()) == {"a", "b"}  # disabled "c" skipped
    assert set(bal._semaphores.keys()) == {"a", "b"}
    assert bal._strategy == "least_loaded"
    assert bal._config_version == 1
    # Global semaphore sized to the number of enabled instances.
    update_sem.assert_awaited_once_with(max_concurrent=2, reserved_high_priority=0)


@pytest.mark.asyncio
async def test_reload_config_removes_missing_instance() -> None:
    """An instance dropped from config is drained and removed on reload."""
    bal = _bare_balancer()

    first = _fake_llm_settings(
        instances=[
            _instance_obj(id="a", base_url="http://a:11434"),
            _instance_obj(id="b", base_url="http://b:11434"),
        ]
    )
    second = _fake_llm_settings(instances=[_instance_obj(id="a", base_url="http://a:11434")])

    with (
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.OllamaProvider",
            side_effect=lambda cfg: MagicMock(),
        ),
        patch.object(lb_mod, "update_llm_semaphore_config", AsyncMock()),
        patch.object(lb_mod, "PrioritySemaphore", side_effect=lambda **kw: _fake_semaphore()),
    ):
        await bal.reload_config(first)
        assert set(bal._providers) == {"a", "b"}
        await bal.reload_config(second)

    assert set(bal._providers) == {"a"}
    assert "b" not in bal._semaphores
    assert bal._config_version == 2


@pytest.mark.asyncio
async def test_reload_config_url_change_recreates_provider() -> None:
    """Changing an instance's base_url drains and recreates its provider."""
    bal = _bare_balancer()

    first = _fake_llm_settings(instances=[_instance_obj(id="a", base_url="http://old:11434")])
    second = _fake_llm_settings(instances=[_instance_obj(id="a", base_url="http://new:11434")])

    created_urls: list[str] = []

    def _make_provider(cfg: dict[str, Any]) -> MagicMock:
        created_urls.append(cfg["base_url"])
        return MagicMock()

    with (
        patch(
            "chaoscypher_core.adapters.llm.providers.ollama_provider.OllamaProvider",
            side_effect=_make_provider,
        ),
        patch.object(lb_mod, "update_llm_semaphore_config", AsyncMock()),
        patch.object(lb_mod, "PrioritySemaphore", side_effect=lambda **kw: _fake_semaphore()),
    ):
        await bal.reload_config(first)
        await bal.reload_config(second)

    # Provider rebuilt with the new URL.
    assert created_urls == ["http://old:11434", "http://new:11434"]
    assert bal._instances["a"]["base_url"] == "http://new:11434"


# ---------------------------------------------------------------------------
# _select_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_round_robin_advances() -> None:
    """round_robin walks the healthy instances in order, wrapping around."""
    bal = _bare_balancer()
    bal._strategy = "round_robin"
    bal._providers = {"a": object(), "b": object()}
    bal._instances = {"a": {"healthy": True}, "b": {"healthy": True}}

    picks = [await bal._select_instance() for _ in range(4)]
    assert picks == ["a", "b", "a", "b"]


@pytest.mark.asyncio
async def test_select_least_loaded_picks_minimum() -> None:
    """least_loaded picks the instance with fewest active + waiting requests."""
    bal = _bare_balancer()
    bal._strategy = "least_loaded"
    bal._providers = {"a": object(), "b": object()}
    bal._instances = {"a": {"healthy": True}, "b": {"healthy": True}}
    bal._semaphores = {
        "a": _fake_semaphore(active=3, high=1, low=0),
        "b": _fake_semaphore(active=0, high=0, low=1),
    }

    assert await bal._select_instance() == "b"


@pytest.mark.asyncio
async def test_select_random_uses_random_choice() -> None:
    """Random strategy defers to random.choice over the healthy ids."""
    bal = _bare_balancer()
    bal._strategy = "random"
    bal._providers = {"a": object(), "b": object()}
    bal._instances = {"a": {"healthy": True}, "b": {"healthy": True}}

    with patch.object(lb_mod.random, "choice", return_value="b") as choice:
        result = await bal._select_instance()

    assert result == "b"
    choice.assert_called_once()


@pytest.mark.asyncio
async def test_select_unknown_strategy_falls_back_to_round_robin() -> None:
    """An unrecognized strategy uses the round-robin fallback path."""
    bal = _bare_balancer()
    bal._strategy = "magic-strategy"
    bal._providers = {"a": object(), "b": object()}
    bal._instances = {"a": {"healthy": True}, "b": {"healthy": True}}

    picks = [await bal._select_instance() for _ in range(3)]
    assert picks == ["a", "b", "a"]


@pytest.mark.asyncio
async def test_select_filters_unhealthy_then_falls_back_to_all() -> None:
    """Unhealthy instances are filtered; if none are healthy, all are eligible."""
    bal = _bare_balancer()
    bal._strategy = "round_robin"
    bal._providers = {"a": object(), "b": object()}

    # "a" unhealthy, "b" healthy → only "b" eligible.
    bal._instances = {"a": {"healthy": False}, "b": {"healthy": True}}
    assert await bal._select_instance() == "b"
    assert await bal._select_instance() == "b"

    # Both unhealthy → fall back to all providers.
    bal._round_robin_index = 0
    bal._instances = {"a": {"healthy": False}, "b": {"healthy": False}}
    fallback = await bal._select_instance()
    assert fallback in {"a", "b"}


@pytest.mark.asyncio
async def test_select_empty_pool_raises_runtime_error() -> None:
    """With no providers at all, selection raises RuntimeError."""
    bal = _bare_balancer()
    bal._providers = {}
    bal._instances = {}

    with pytest.raises(RuntimeError, match="No Ollama instances available"):
        await bal._select_instance()


# ---------------------------------------------------------------------------
# acquire_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_instance_yields_id_and_provider() -> None:
    """acquire_instance yields (instance_id, provider) under the semaphore."""
    bal = _bare_balancer()
    bal._strategy = "round_robin"
    provider = MagicMock(name="provider")
    bal._providers = {"a": provider}
    bal._instances = {"a": {"healthy": True}}

    sem = MagicMock()
    sem.acquire = MagicMock(return_value=_async_cm())
    bal._semaphores = {"a": sem}

    async with bal.acquire_instance() as (inst_id, got_provider):
        assert inst_id == "a"
        assert got_provider is provider


@pytest.mark.asyncio
async def test_acquire_instance_no_providers_raises() -> None:
    """acquire_instance with an empty pool raises RuntimeError before selection."""
    bal = _bare_balancer()
    bal._providers = {}

    with pytest.raises(RuntimeError, match="No Ollama instances configured"):
        async with bal.acquire_instance():
            pass


@pytest.mark.asyncio
async def test_acquire_instance_marks_unhealthy_on_connection_error() -> None:
    """A connection error in the body marks the instance unhealthy and re-raises."""
    bal = _bare_balancer()
    bal._strategy = "round_robin"
    bal._providers = {"a": MagicMock()}
    bal._instances = {"a": {"healthy": True}}
    sem = MagicMock()
    sem.acquire = MagicMock(return_value=_async_cm())
    bal._semaphores = {"a": sem}

    with pytest.raises(ConnectionError):
        async with bal.acquire_instance() as (_inst_id, _provider):
            raise ConnectionError("connection refused")

    assert bal._instances["a"]["healthy"] is False
    assert "connection refused" in bal._instances["a"]["last_error"]


# ---------------------------------------------------------------------------
# _drain_and_remove_instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_not_present_returns_early() -> None:
    """Draining an unknown instance is a no-op (no KeyError)."""
    bal = _bare_balancer()
    # Should simply return without raising.
    await bal._drain_and_remove_instance("missing")


@pytest.mark.asyncio
async def test_drain_waits_until_active_zero_then_removes() -> None:
    """The drain loop sleeps while active_count > 0, then removes the instance."""
    bal = _bare_balancer()
    bal._drain_max_wait = 5

    sem = MagicMock()
    # active_count: 2, 1, 0 — drains over two iterations then removes.
    type(sem).active_count = property(lambda self, vals=iter([2, 1, 0, 0]): next(vals))
    bal._semaphores = {"a": sem}
    bal._providers = {"a": MagicMock()}
    bal._instances = {"a": {"healthy": True}}

    sleeps: list[float] = []

    async def _record_sleep(s: float) -> None:
        sleeps.append(s)

    with patch.object(lb_mod.asyncio, "sleep", side_effect=_record_sleep):
        await bal._drain_and_remove_instance("a")

    assert "a" not in bal._providers
    assert "a" not in bal._semaphores
    assert "a" not in bal._instances
    assert len(sleeps) >= 1  # waited at least one drain cycle


# ---------------------------------------------------------------------------
# get_total_capacity / get_stats
# ---------------------------------------------------------------------------


def test_get_total_capacity_counts_providers() -> None:
    """Capacity equals the number of registered providers."""
    bal = _bare_balancer()
    bal._providers = {"a": object(), "b": object(), "c": object()}
    assert bal.get_total_capacity() == 3


def test_get_stats_reports_per_instance() -> None:
    """get_stats summarises strategy, version, and per-instance counts."""
    bal = _bare_balancer()
    bal._strategy = "round_robin"
    bal._config_version = 7
    bal._providers = {"a": object()}
    bal._instances = {"a": {"name": "Alpha", "base_url": "http://a", "healthy": True}}
    bal._semaphores = {"a": _fake_semaphore(active=1, high=2, low=3)}

    stats = bal.get_stats()
    assert stats["strategy"] == "round_robin"
    assert stats["config_version"] == 7
    assert stats["total_instances"] == 1
    assert stats["total_capacity"] == 1
    inst = stats["instances"]["a"]
    assert inst["name"] == "Alpha"
    assert inst["active"] == 1
    assert inst["waiting_high"] == 2
    assert inst["waiting_low"] == 3


# ---------------------------------------------------------------------------
# health_check_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_all_aggregates_and_updates_state() -> None:
    """Health results are aggregated and written back into instance state."""
    bal = _bare_balancer()

    healthy_provider = MagicMock()
    healthy_provider.check_health = AsyncMock(return_value=True)
    unhealthy_provider = MagicMock()
    unhealthy_provider.check_health = AsyncMock(return_value=False)

    bal._providers = {"a": healthy_provider, "b": unhealthy_provider}
    bal._instances = {"a": {"healthy": False}, "b": {"healthy": True}}

    results = await bal.health_check_all()

    assert results == {"a": True, "b": False}
    assert bal._instances["a"]["healthy"] is True
    assert bal._instances["a"]["last_error"] is None
    assert bal._instances["b"]["healthy"] is False
    assert "last_health_check" in bal._instances["a"]


@pytest.mark.asyncio
async def test_health_check_all_provider_exception_marks_unhealthy() -> None:
    """A provider whose check_health raises is recorded as unhealthy with the error."""
    bal = _bare_balancer()
    boom_provider = MagicMock()
    boom_provider.check_health = AsyncMock(side_effect=RuntimeError("boom"))
    bal._providers = {"a": boom_provider}
    bal._instances = {"a": {"healthy": True}}

    results = await bal.health_check_all()

    assert results == {"a": False}
    assert bal._instances["a"]["healthy"] is False
    assert bal._instances["a"]["last_error"] == "boom"


@pytest.mark.asyncio
async def test_health_check_all_skips_instance_removed_mid_check() -> None:
    """If an instance disappears during the check phase, its state write is skipped."""
    bal = _bare_balancer()
    provider = MagicMock()
    provider.check_health = AsyncMock(return_value=True)
    bal._providers = {"a": provider}
    # Instance metadata is absent (e.g. removed by a concurrent reload).
    bal._instances = {}

    results = await bal.health_check_all()

    # Result still reported, but no state write occurred (no KeyError).
    assert results == {"a": True}
    assert "a" not in bal._instances


# ---------------------------------------------------------------------------
# Module-level singleton + reload helper
# ---------------------------------------------------------------------------


def test_get_ollama_load_balancer_is_singleton() -> None:
    """get_ollama_load_balancer returns the same instance across calls."""
    with patch.object(lb_mod, "_global_load_balancer", None):
        first = get_ollama_load_balancer()
        second = get_ollama_load_balancer()
    assert first is second


@pytest.mark.asyncio
async def test_reload_load_balancer_config_configures_and_reloads() -> None:
    """The module helper configures drain params then delegates to reload_config."""
    fake_balancer = MagicMock()
    fake_balancer.configure_drain = MagicMock()
    fake_balancer.reload_config = AsyncMock()

    settings = object()
    with patch.object(lb_mod, "get_ollama_load_balancer", return_value=fake_balancer):
        await reload_load_balancer_config(
            settings,  # type: ignore[arg-type]
            drain_max_wait=9,
            drain_check_interval=2.5,
        )

    fake_balancer.configure_drain.assert_called_once_with(max_wait=9, check_interval=2.5)
    fake_balancer.reload_config.assert_awaited_once_with(settings)


# ---------------------------------------------------------------------------
# Small async-context-manager helper for semaphore.acquire()
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Minimal async context manager that yields None."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _async_cm() -> _AsyncCM:
    return _AsyncCM()
