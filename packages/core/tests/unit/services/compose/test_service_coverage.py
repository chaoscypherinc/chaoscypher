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

import json
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


def _composed_db(tmp_path: Path) -> Path:
    """Pretend `compose build` ran: create the composed database directory."""
    db_dir = tmp_path / "out" / "databases" / "default"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir


def _live_proc(pid: int = 4242) -> MagicMock:
    """A Popen stand-in that is still running after the startup grace period."""
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None
    return proc


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


@pytest.mark.unit
class TestRuntimeSettings:
    def test_build_writes_runtime_settings_once(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        ComposeService._write_runtime_settings(out)

        path = out / service_mod.RUNTIME_SETTINGS_FILENAME
        text = path.read_text(encoding="utf-8")
        assert "setup_completed: true" in text
        assert "connection_max_retries: 1" in text

        # A second build leaves an operator-edited file alone.
        path.write_text("setup_completed: true\nqueue:\n  queue_host: myvalkey\n", encoding="utf-8")
        ComposeService._write_runtime_settings(out)
        assert "myvalkey" in path.read_text(encoding="utf-8")

    def test_runtime_settings_parse_with_the_real_settings_loader(self, tmp_path: Path) -> None:
        import yaml

        data = yaml.safe_load(service_mod.RUNTIME_SETTINGS)
        assert data["setup_completed"] is True
        assert data["queue"]["queue_host"] == "127.0.0.1"
        assert data["queue"]["connection_max_retries"] == 1


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

    # -- detached server: pid record round-trip -----------------------------

    @pytest.mark.asyncio
    async def test_down_with_no_pid_file_returns_false(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        assert await service.down(config) is False

    @pytest.mark.asyncio
    async def test_down_stops_recorded_pid_from_a_fresh_service(self, tmp_path: Path) -> None:
        """The `compose down` case: a new process, no Popen handle, only compose.pid."""
        config = _config(tmp_path)
        ComposeService()._write_pid_file(config, 4242)
        service = ComposeService()

        # Alive for the pre-check and the first poll, gone after the signal.
        import itertools

        calls = itertools.count()
        with (
            patch.object(ComposeService, "_pid_alive", side_effect=lambda _pid: next(calls) < 2),
            patch.object(ComposeService, "_signal") as sig,
        ):
            stopped = await service.down(config)

        assert stopped is True
        sig.assert_called_once_with(4242, service_mod.signal.SIGTERM)
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_down_escalates_to_kill_when_server_ignores_terminate(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        ComposeService()._write_pid_file(config, 4242)
        service = ComposeService()

        with (
            patch.object(ComposeService, "_pid_alive", return_value=True),
            patch.object(ComposeService, "_pid_is_compose_server", return_value=True),
            patch.object(ComposeService, "_signal") as sig,
            patch(
                "chaoscypher_core.settings.ComposeSettings",
                return_value=SimpleNamespace(process_terminate_timeout=0.2),
            ),
        ):
            stopped = await service.down(config)

        assert stopped is True
        assert [c.args[1] for c in sig.call_args_list] == [
            service_mod.signal.SIGTERM,
            service_mod.signal.SIGKILL,
        ]
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_down_cleans_stale_pid_file(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        ComposeService()._write_pid_file(config, 4242)
        service = ComposeService()

        with (
            patch.object(ComposeService, "_pid_alive", return_value=False),
            patch.object(ComposeService, "_signal") as sig,
        ):
            stopped = await service.down(config)

        assert stopped is False
        sig.assert_not_called()
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_down_treats_a_reused_pid_as_stale(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        ComposeService()._write_pid_file(config, 4242)

        with (
            patch.object(ComposeService, "_pid_alive", return_value=True),
            patch.object(ComposeService, "_pid_is_compose_server", return_value=False),
            patch.object(ComposeService, "_signal") as sig,
        ):
            stopped = await ComposeService().down(config)

        assert stopped is False
        sig.assert_not_called()
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_down_ignores_non_integer_pid(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        pid_file = tmp_path / "out" / service_mod.PID_FILENAME
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text('{"pid": "not-a-number"}', encoding="utf-8")

        assert await ComposeService().down(config) is False

    @pytest.mark.asyncio
    async def test_build_refuses_under_a_running_server(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()
        service._write_pid_file(config, 4242)

        with (
            patch.object(ComposeService, "_pid_alive", return_value=True),
            patch.object(ComposeService, "_pid_is_compose_server", return_value=True),
            pytest.raises(ComposeError) as exc_info,
        ):
            await service.build(config)

        assert "compose down" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_down_ignores_corrupt_pid_file(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        pid_file = tmp_path / "out" / service_mod.PID_FILENAME
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text("{not json", encoding="utf-8")

        assert await ComposeService().down(config) is False

    def test_pid_alive_probe(self) -> None:
        assert ComposeService._pid_alive(0) is False
        assert ComposeService._pid_alive(-1) is False
        import os

        assert ComposeService._pid_alive(os.getpid()) is True


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
    async def test_start_refuses_without_a_composed_database(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        service = ComposeService()

        with (
            patch.object(service_mod.subprocess, "Popen") as popen,
            pytest.raises(ComposeError) as exc_info,
        ):
            await service._start_server(config, detach=True)

        popen.assert_not_called()
        assert exc_info.value.stage == "serve"
        assert "compose build" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_detach_spawns_background_process(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _composed_db(tmp_path)
        service = ComposeService()

        fake_proc = _live_proc()
        with (
            patch.object(service_mod.subprocess, "Popen", return_value=fake_proc) as popen,
            patch.object(
                ComposeService, "_wait_until_listening", AsyncMock(return_value="listening")
            ),
        ):
            await service._start_server(config, detach=True)

        popen.assert_called_once()
        assert service._server_process is fake_proc
        # Launched like `chaoscypher serve`: Cortex's real entrypoint, loopback
        # host, the port from the config — and the composed database selected
        # through the environment, not through flags Cortex does not have.
        cmd = popen.call_args.args[0]
        assert cmd[1:4] == ["-m", "chaoscypher_cortex.main", "start"]
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
        assert cmd[cmd.index("--port") + 1] == "8081"
        assert "--mode" not in cmd
        env = popen.call_args.kwargs["env"]
        assert env["CHAOSCYPHER_DATA_DIR"] == str(tmp_path / "out")
        assert env["CHAOSCYPHER_DATABASE"] == "default"
        assert env["CHAOSCYPHER_COMPOSE_NAME"] == "test-system"
        assert popen.call_args.kwargs["start_new_session"] is True

    @pytest.mark.asyncio
    async def test_detach_reports_a_server_that_dies_during_startup(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _composed_db(tmp_path)
        (tmp_path / "out" / service_mod.SERVER_LOG_FILENAME).write_text(
            "boom: no such option --mode\n", encoding="utf-8"
        )
        service = ComposeService()

        dead_proc = MagicMock()
        dead_proc.pid = 4242
        dead_proc.poll.return_value = 2
        with (
            patch.object(service_mod.subprocess, "Popen", return_value=dead_proc),
            patch.object(ComposeService, "_wait_until_listening", AsyncMock(return_value="exited")),
            pytest.raises(ComposeError) as exc_info,
        ):
            await service._start_server(config, detach=True)

        assert exc_info.value.stage == "serve"
        assert "exit code 2" in exc_info.value.message
        assert "no such option --mode" in exc_info.value.message
        assert service._server_process is None
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_detach_records_pid_for_a_later_down(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _composed_db(tmp_path)
        service = ComposeService()

        fake_proc = _live_proc()
        with (
            patch.object(service_mod.subprocess, "Popen", return_value=fake_proc),
            patch.object(
                ComposeService, "_wait_until_listening", AsyncMock(return_value="listening")
            ),
        ):
            await service._start_server(config, detach=True)

        record = json.loads(
            (tmp_path / "out" / service_mod.PID_FILENAME).read_text(encoding="utf-8")
        )
        assert record["pid"] == 4242
        assert record["name"] == "test-system"
        assert record["port"] == 8081

    @pytest.mark.asyncio
    async def test_detach_reports_a_server_that_never_listens(self, tmp_path: Path) -> None:
        """Alive but not bound within the timeout (e.g. port in use): fail and stop it."""
        config = _config(tmp_path)
        _composed_db(tmp_path)
        service = ComposeService()

        hung = _live_proc()
        with (
            patch.object(service_mod.subprocess, "Popen", return_value=hung),
            patch.object(
                ComposeService, "_wait_until_listening", AsyncMock(return_value="timeout")
            ),
            patch.object(ComposeService, "_signal") as sig,
            pytest.raises(ComposeError) as exc_info,
        ):
            await service._start_server(config, detach=True)

        assert "did not start listening" in exc_info.value.message
        sig.assert_called_once_with(4242, service_mod.signal.SIGTERM)
        assert not (tmp_path / "out" / service_mod.PID_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_detach_refuses_while_recorded_server_is_alive(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _composed_db(tmp_path)
        service = ComposeService()
        service._write_pid_file(config, 4242)

        with (
            patch.object(ComposeService, "_pid_alive", return_value=True),
            patch.object(ComposeService, "_pid_is_compose_server", return_value=True),
            patch.object(service_mod.subprocess, "Popen") as popen,
            pytest.raises(ComposeError) as exc_info,
        ):
            await service._start_server(config, detach=True)

        popen.assert_not_called()
        assert exc_info.value.stage == "serve"
        assert "already running" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_foreground_awaits_child(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        _composed_db(tmp_path)
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


# ---------------------------------------------------------------------------
# ComposeConfig YAML round-trip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compose_config_to_yaml_round_trips_through_from_yaml(tmp_path: Path) -> None:
    """to_yaml output must be readable by from_yaml.

    A python-mode model_dump left merge_strategy as an enum member, which
    yaml.dump serialized as a !!python/object/apply tag that safe_load
    rejects — the reference axiomatize.yaml the build writes was unloadable.
    """
    config = _config(tmp_path)
    out = tmp_path / "roundtrip.yaml"
    config.to_yaml(out)

    loaded = ComposeConfig.from_yaml(out)

    assert loaded.name == config.name
    assert loaded.settings.merge_strategy == config.settings.merge_strategy
    assert loaded.packages == config.packages
