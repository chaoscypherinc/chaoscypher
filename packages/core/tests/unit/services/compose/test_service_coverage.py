# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for services/compose/service.py (ComposeService).

Covers the ``build`` orchestration (success, empty-resolution, ResolverError
and MergerError wrapping into ComposeError), ``up`` (build-needed / existing-DB
/ rebuild / server-failure), ``down`` / ``_stop_server`` (timeout-kill path),
``run`` (subprocess exit code), and ``_start_server`` port validation.

``PackageResolver`` and ``NamespaceMerger`` are patched at the service-module
source path; ``ComposeConfig`` / ``CompositionResult`` are real models built
against ``tmp_path`` so ``resolved_output_dir`` / ``to_yaml`` exercise real I/O.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.compose import service as service_mod
from chaoscypher_core.services.compose.merger import MergerError
from chaoscypher_core.services.compose.models import (
    ComposeConfig,
    CompositionResult,
)
from chaoscypher_core.services.compose.resolver import ResolverError
from chaoscypher_core.services.compose.service import ComposeError, ComposeService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(tmp_path: Path, packages: list[str] | None = None) -> ComposeConfig:
    """Build a ComposeConfig rooted at tmp_path."""
    return ComposeConfig.from_dict(
        {
            "name": "test-system",
            "version": "1.0.0",
            "packages": packages if packages is not None else ["alpha:1.0.0"],
            "settings": {"output_dir": str(tmp_path / "out"), "port": 8081},
        },
        base_path=tmp_path / "axiomatize.yaml",
    )


def _patch_resolver(resolved: list | Exception) -> MagicMock:
    """Patch PackageResolver; resolve_all returns `resolved` or raises it."""
    instance = MagicMock()
    if isinstance(resolved, Exception):
        instance.resolve_all = AsyncMock(side_effect=resolved)
    else:
        instance.resolve_all = AsyncMock(return_value=resolved)
    return MagicMock(return_value=instance)


def _patch_merger(result: CompositionResult | Exception) -> MagicMock:
    """Patch NamespaceMerger; merge returns `result` or raises it."""
    instance = MagicMock()
    if isinstance(result, Exception):
        instance.merge = AsyncMock(side_effect=result)
    else:
        instance.merge = AsyncMock(return_value=result)
    return MagicMock(return_value=instance)


# ---------------------------------------------------------------------------
# ComposeError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestComposeError:
    def test_records_stage(self) -> None:
        err = ComposeError("boom", stage="resolve", details={"k": "v"})
        assert err.code == "COMPOSE_ERROR"
        assert err.stage == "resolve"
        assert err.details["stage"] == "resolve"
        assert err.details["k"] == "v"

    def test_default_stage_unknown(self) -> None:
        err = ComposeError("boom")
        assert err.stage == "unknown"
        assert err.details["stage"] == "unknown"


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuild:
    @pytest.mark.asyncio
    async def test_success_writes_config_copy(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        resolved_pkg = SimpleNamespace(name="alpha")
        merge_result = CompositionResult(
            success=True,
            output_dir=output_dir,
            total_entities=5,
            total_relationships=3,
            packages_included=["alpha:1.0.0"],
        )

        with (
            patch.object(service_mod, "PackageResolver", _patch_resolver([resolved_pkg])),
            patch.object(service_mod, "NamespaceMerger", _patch_merger(merge_result)),
        ):
            service = ComposeService()
            result = await service.build(config)

        assert result.success is True
        assert result.total_entities == 5
        # config copy written next to output on success
        assert (output_dir / "axiomatize.yaml").exists()

    @pytest.mark.asyncio
    async def test_no_resolved_packages_returns_failure(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        with patch.object(service_mod, "PackageResolver", _patch_resolver([])):
            service = ComposeService()
            result = await service.build(config)

        assert result.success is False
        assert result.errors == ["No packages to compose"]

    @pytest.mark.asyncio
    async def test_resolver_error_wrapped(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        err = ResolverError("bad pkg", package="alpha", details={"hint": "x"})
        with patch.object(service_mod, "PackageResolver", _patch_resolver(err)):
            service = ComposeService()
            with pytest.raises(ComposeError) as exc_info:
                await service.build(config)

        wrapped = exc_info.value
        assert wrapped.stage == "resolve"
        assert wrapped.details["package"] == "alpha"
        assert wrapped.details["hint"] == "x"

    @pytest.mark.asyncio
    async def test_merger_error_wrapped(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        resolved_pkg = SimpleNamespace(name="alpha")
        err = MergerError("merge blew up", package="alpha")
        with (
            patch.object(service_mod, "PackageResolver", _patch_resolver([resolved_pkg])),
            patch.object(service_mod, "NamespaceMerger", _patch_merger(err)),
        ):
            service = ComposeService()
            with pytest.raises(ComposeError) as exc_info:
                await service.build(config)

        assert exc_info.value.stage == "merge"
        assert exc_info.value.details["package"] == "alpha"

    @pytest.mark.asyncio
    async def test_merge_failure_skips_config_copy(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        resolved_pkg = SimpleNamespace(name="alpha")
        merge_result = CompositionResult(
            success=False, output_dir=output_dir, errors=["merge failed"]
        )
        with (
            patch.object(service_mod, "PackageResolver", _patch_resolver([resolved_pkg])),
            patch.object(service_mod, "NamespaceMerger", _patch_merger(merge_result)),
        ):
            service = ComposeService()
            result = await service.build(config)

        assert result.success is False
        # No config copy on failed merge
        assert not (output_dir / "axiomatize.yaml").exists()


# ---------------------------------------------------------------------------
# up()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUp:
    @pytest.mark.asyncio
    async def test_builds_when_db_missing(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        success = CompositionResult(success=True, output_dir=output_dir)

        service = ComposeService()
        with (
            patch.object(service, "build", AsyncMock(return_value=success)) as build_mock,
            patch.object(service, "_start_server", AsyncMock()) as start_mock,
        ):
            result = await service.up(config)

        build_mock.assert_awaited_once()
        start_mock.assert_awaited_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_build_failure_returns_early_without_serving(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        failure = CompositionResult(success=False, output_dir=output_dir, errors=["x"])

        service = ComposeService()
        with (
            patch.object(service, "build", AsyncMock(return_value=failure)),
            patch.object(service, "_start_server", AsyncMock()) as start_mock,
        ):
            result = await service.up(config)

        assert result.success is False
        start_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_existing_database(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        # Create the existing-DB marker dir.
        (output_dir / "databases" / "default").mkdir(parents=True, exist_ok=True)

        service = ComposeService()
        with (
            patch.object(service, "build", AsyncMock()) as build_mock,
            patch.object(service, "_start_server", AsyncMock()) as start_mock,
        ):
            result = await service.up(config)

        build_mock.assert_not_awaited()
        start_mock.assert_awaited_once()
        assert result.success is True
        assert result.packages_included == []

    @pytest.mark.asyncio
    async def test_rebuild_forces_build_even_if_db_exists(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        (output_dir / "databases" / "default").mkdir(parents=True, exist_ok=True)
        success = CompositionResult(success=True, output_dir=output_dir)

        service = ComposeService()
        with (
            patch.object(service, "build", AsyncMock(return_value=success)) as build_mock,
            patch.object(service, "_start_server", AsyncMock()),
        ):
            await service.up(config, rebuild=True)

        build_mock.assert_awaited_once()
        # clean=rebuild passed through
        assert build_mock.await_args.kwargs.get("clean") is True

    @pytest.mark.asyncio
    async def test_server_failure_wrapped_in_compose_error(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        output_dir = config.resolved_output_dir
        success = CompositionResult(success=True, output_dir=output_dir)

        service = ComposeService()
        with (
            patch.object(service, "build", AsyncMock(return_value=success)),
            patch.object(service, "_start_server", AsyncMock(side_effect=OSError("port busy"))),
        ):
            with pytest.raises(ComposeError) as exc_info:
                await service.up(config)

        assert exc_info.value.stage == "serve"


# ---------------------------------------------------------------------------
# down() / _stop_server()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDownStopServer:
    @pytest.mark.asyncio
    async def test_down_with_no_process_is_noop(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()
        # No server process — should not raise.
        await service.down(config)
        assert service._server_process is None

    @pytest.mark.asyncio
    async def test_stop_server_graceful_terminate(self, tmp_path: Path) -> None:
        service = ComposeService()
        proc = MagicMock()
        proc.wait.return_value = 0
        service._server_process = proc

        await service._stop_server()

        proc.terminate.assert_called_once()
        proc.wait.assert_called_once()
        proc.kill.assert_not_called()
        assert service._server_process is None

    @pytest.mark.asyncio
    async def test_stop_server_kill_on_timeout(self, tmp_path: Path) -> None:
        import subprocess

        service = ComposeService()
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)
        service._server_process = proc

        await service._stop_server()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        assert service._server_process is None


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRun:
    @pytest.mark.asyncio
    async def test_returns_subprocess_exit_code(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"out", b"err"))
        proc.returncode = 7

        with patch.object(
            service_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ):
            code = await service.run(config, ["echo", "hi"])

        assert code == 7

    @pytest.mark.asyncio
    async def test_none_returncode_maps_to_zero(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = None

        with patch.object(
            service_mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
        ):
            code = await service.run(config, ["true"])

        assert code == 0


# ---------------------------------------------------------------------------
# _start_server() — port validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartServer:
    @pytest.mark.asyncio
    async def test_invalid_port_raises(self, tmp_path: Path) -> None:
        config = ComposeConfig.from_dict(
            {
                "name": "x",
                "packages": [],
                "settings": {"output_dir": str(tmp_path / "out"), "port": 70000},
            },
            base_path=tmp_path / "axiomatize.yaml",
        )
        service = ComposeService()
        with pytest.raises(ComposeError) as exc_info:
            await service._start_server(config)
        assert exc_info.value.stage == "serve"
        assert "Invalid port" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_detach_spawns_background_process(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        fake_proc = MagicMock()
        fake_proc.pid = 4242
        with patch.object(service_mod.subprocess, "Popen", return_value=fake_proc) as popen:
            await service._start_server(config, detach=True)

        popen.assert_called_once()
        assert service._server_process is fake_proc

    @pytest.mark.asyncio
    async def test_foreground_awaits_child(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        child = MagicMock()
        child.wait = AsyncMock(return_value=0)
        with (
            patch.object(
                service_mod.asyncio,
                "create_subprocess_exec",
                AsyncMock(return_value=child),
            ),
            patch.object(service_mod.signal, "signal"),
        ):
            await service._start_server(config, detach=False)

        child.wait.assert_awaited_once()
