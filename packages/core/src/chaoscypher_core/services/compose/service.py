# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compose Service - Main orchestrator for package composition.

Coordinates the complete composition workflow:
1. Load configuration from axiomatize.yaml
2. Resolve packages (hub + local + dependencies)
3. Merge knowledge into a unified database
4. Optionally start the server

Example:
    from chaoscypher_core.services.compose import ComposeService, ComposeConfig

    config = ComposeConfig.from_yaml("axiomatize.yaml")
    service = ComposeService()

    # Build the composed database
    result = await service.build(config)

    # Or build and serve
    await service.up(config, detach=False)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.services.compose.merger import (
    COMPOSED_DATABASE_NAME,
    MergerError,
    NamespaceMerger,
)
from chaoscypher_core.services.compose.models import (
    ComposeConfig,
    CompositionResult,
)
from chaoscypher_core.services.compose.resolver import PackageResolver, ResolverError


if TYPE_CHECKING:
    from chaoscypher_core.services.lexicon import AuthConfig


logger = structlog.get_logger(__name__)

# Written into the composition's output directory by ``compose up --detach``
# and read back by ``compose down`` — the only thing that survives between
# the two CLI invocations, since the Popen handle lives in the `up` process.
PID_FILENAME = "compose.pid"
RUNTIME_SETTINGS_FILENAME = "settings.yaml"
# The runtime settings `compose build` writes next to the composed database.
# A composition is a local, read-mostly knowledge server: it needs neither a
# queue backend (Cortex would otherwise spend a minute retrying `valkey:6379`
# before giving up) nor the first-run setup wizard (no chat/extraction model
# is involved — the MCP host's model asks the questions).
RUNTIME_SETTINGS = """\
# Written by `chaoscypher compose build`. Settings for the composed database
# in this directory; edit freely — `compose build` never overwrites this file.
setup_completed: true
queue:
  queue_host: 127.0.0.1
  connection_max_retries: 1
  connection_retry_delay: 0.1
"""
SERVER_LOG_FILENAME = "server.log"
# A composition is a local knowledge server; bind loopback, never 0.0.0.0.
SERVER_HOST = "127.0.0.1"
# How long `up --detach` gives the new server to start listening before
# giving up on it. Cortex takes a few seconds to bind; a port-in-use failure
# surfaces at bind time, so the wait has to reach past it.
STARTUP_TIMEOUT_SECONDS = 60.0
STARTUP_POLL_SECONDS = 0.25


class ComposeError(ChaosCypherException):
    """Error during composition.

    Attributes:
        stage: Composition stage where error occurred.
    """

    def __init__(
        self,
        message: str,
        stage: str = "unknown",
        details: dict | None = None,
    ) -> None:
        """Initialize compose error.

        Args:
            message: Error description.
            stage: Composition stage where error occurred.
            details: Additional error details.
        """
        error_details = details or {}
        error_details["stage"] = stage
        super().__init__(message=message, code="COMPOSE_ERROR", details=error_details)
        self.stage = stage


class ComposeService:
    """Main orchestrator for package composition.

    Coordinates the complete workflow of resolving, downloading,
    and merging packages into a unified runtime database.

    Attributes:
        auth: Optional Lexicon authentication config.
        lexicon_url: Lexicon API URL.

    Example:
        service = ComposeService(
            auth=AuthConfig(token="..."),
        )

        # Load and build composition
        config = ComposeConfig.from_yaml("axiomatize.yaml")
        result = await service.build(config)

        if result.success:
            print(f"Database ready at {result.output_dir}")
    """

    def __init__(
        self,
        auth: AuthConfig | None = None,
        lexicon_url: str | None = None,
    ) -> None:
        """Initialize compose service.

        Args:
            auth: Optional Lexicon authentication config.
            lexicon_url: Lexicon API URL.
        """
        self.auth = auth
        self.lexicon_url = lexicon_url
        self._server_process: subprocess.Popen | None = None

    async def build(
        self,
        config: ComposeConfig,
        clean: bool = True,
    ) -> CompositionResult:
        """Build a composed database from configuration.

        Resolves all packages, downloads hub packages,
        and merges them into a unified database.

        Args:
            config: Composition configuration.
            clean: Whether to clean output directory before building.

        Returns:
            CompositionResult with build statistics.

        Raises:
            ComposeError: If build fails.
        """
        logger.info(
            "compose_build_starting",
            name=config.name,
            package_count=len(config.packages),
            strategy=config.settings.merge_strategy.value,
        )

        output_dir = config.resolved_output_dir

        # A rebuild deletes the database a running detached server is
        # reading; refuse until it is stopped.
        live = self._read_pid_file(config)
        if (
            live is not None
            and self._pid_alive(live["pid"])
            and self._pid_is_compose_server(live["pid"])
        ):
            msg = (
                f"Composition '{config.name}' is running (pid {live['pid']}); "
                "run `chaoscypher compose down` before rebuilding"
            )
            raise ComposeError(msg, stage="resolve")

        try:
            # Stage 1: Resolve packages
            resolver = PackageResolver(
                cache_dir=output_dir / "cache",
                lexicon_url=self.lexicon_url,
                auth=self.auth,
            )

            resolved = await resolver.resolve_all(
                config.package_specs,
                include_dependencies=True,
            )

            if not resolved:
                return CompositionResult(
                    success=False,
                    output_dir=output_dir,
                    errors=["No packages to compose"],
                )

            # Stage 2: Merge packages
            merger = NamespaceMerger(
                output_dir=output_dir,
                strategy=config.settings.merge_strategy,
            )

            result = await merger.merge(resolved, clean=clean)

            # Write compose config to output for reference, plus the runtime
            # settings the composed server and `compose run` tools read.
            if result.success:
                config_copy = output_dir / "axiomatize.yaml"
                config.to_yaml(config_copy)
                self._write_runtime_settings(output_dir)

            logger.info(
                "compose_build_completed",
                success=result.success,
                entities=result.total_entities,
                relationships=result.total_relationships,
            )

            return result

        except ResolverError as e:
            logger.exception("compose_resolve_failed")
            raise ComposeError(
                e.message,
                stage="resolve",
                details={"package": e.package, **e.details},
            ) from e

        except MergerError as e:
            logger.exception("compose_merge_failed")
            raise ComposeError(
                e.message,
                stage="merge",
                details={"package": e.package},
            ) from e

    async def up(
        self,
        config: ComposeConfig,
        rebuild: bool = False,
        detach: bool = False,
    ) -> CompositionResult:
        """Build and start the composition.

        Builds the database if needed, then starts the server.

        Args:
            config: Composition configuration.
            rebuild: Force rebuild even if database exists.
            detach: Run server in background.

        Returns:
            CompositionResult with build statistics.

        Raises:
            ComposeError: If build or server start fails.
        """
        output_dir = config.resolved_output_dir
        db_exists = (output_dir / "databases" / COMPOSED_DATABASE_NAME).exists()

        # Build if needed
        if rebuild or not db_exists:
            result = await self.build(config, clean=rebuild)
            if not result.success:
                return result
        else:
            logger.info("compose_using_existing_database", path=str(output_dir))
            result = CompositionResult(
                success=True,
                output_dir=output_dir,
                packages_included=[],  # Unknown for existing DB
            )

        # Start the server
        try:
            await self._start_server(config, detach=detach)
        except Exception as e:
            logger.exception("compose_server_failed")
            msg = f"Failed to start server: {e}"
            raise ComposeError(msg, stage="serve") from e

        return result

    async def down(self, config: ComposeConfig) -> bool:
        """Stop the composition server.

        Stops the server this ``ComposeService`` started (foreground path),
        or — the normal ``compose down`` case, a fresh process — the detached
        server recorded in the output directory's ``compose.pid``. A stale
        record (process already gone) is cleaned up and reported as nothing
        running.

        Args:
            config: Composition configuration.

        Returns:
            ``True`` when a running server was stopped, ``False`` when there
            was nothing to stop.
        """
        if self._server_process is not None:
            await self._stop_server()
            self._remove_pid_file(config)
            logger.info("compose_stopped", name=config.name)
            return True

        record = self._read_pid_file(config)
        if record is None:
            logger.info("compose_down_nothing_recorded", name=config.name)
            return False

        pid = record["pid"]
        if not self._pid_alive(pid) or not self._pid_is_compose_server(pid):
            # Gone, or the pid has been reused by something that is not ours.
            logger.info("compose_down_stale_pid_file", name=config.name, pid=pid)
            self._remove_pid_file(config)
            return False

        from chaoscypher_core.settings import ComposeSettings

        terminate_timeout = ComposeSettings().process_terminate_timeout
        self._signal(pid, signal.SIGTERM)
        # Poll the foreign pid (no handle to wait on) until it exits or the
        # grace period lapses — bounded, so a hung server cannot hang `down`.
        poll_interval = 0.1
        for _ in range(int(terminate_timeout / poll_interval) + 1):
            if not self._pid_alive(pid):
                break
            await asyncio.sleep(poll_interval)
        if self._pid_alive(pid):
            logger.warning("compose_server_terminate_timeout_killing", pid=pid)
            self._signal(pid, signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM)
        self._remove_pid_file(config)
        logger.info("compose_stopped", name=config.name, pid=pid)
        return True

    # ------------------------------------------------------------------
    # Detached-server bookkeeping
    # ------------------------------------------------------------------

    @staticmethod
    async def _wait_until_listening(process: subprocess.Popen, port: int) -> str:
        """Poll until the server accepts a connection on ``port``.

        Returns ``"listening"``, ``"exited"`` when the process died first, or
        ``"timeout"`` when the deadline lapsed with the process still alive.
        """
        import socket

        deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT_SECONDS
        while True:
            if process.poll() is not None:
                return "exited"
            try:
                with socket.create_connection((SERVER_HOST, port), timeout=STARTUP_POLL_SECONDS):
                    return "listening"
            except OSError:
                pass
            if asyncio.get_running_loop().time() >= deadline:
                return "timeout"
            await asyncio.sleep(STARTUP_POLL_SECONDS)

    @staticmethod
    def _write_runtime_settings(output_dir: Path) -> None:
        """Write ``settings.yaml`` for the composed data dir, once.

        Never overwrites: the operator may tune the file after the first
        build (a different queue, an embedding model for semantic search).
        """
        path = output_dir / RUNTIME_SETTINGS_FILENAME
        if path.exists():
            return
        path.write_text(RUNTIME_SETTINGS, encoding="utf-8")
        logger.info("compose_runtime_settings_written", path=str(path))

    @staticmethod
    def _composition_env(config: ComposeConfig) -> dict[str, str]:
        """Environment that selects the composed database for a child process."""
        return {
            "CHAOSCYPHER_DATA_DIR": str(config.resolved_output_dir),
            "CHAOSCYPHER_DATABASE": COMPOSED_DATABASE_NAME,
            "CHAOSCYPHER_COMPOSE_NAME": config.name,
        }

    @staticmethod
    def _log_tail(log_path: Path, lines: int = 12) -> str:
        """Return the last ``lines`` of the server log, or ``""``."""
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(content.splitlines()[-lines:])

    @staticmethod
    def _pid_file(config: ComposeConfig) -> Path:
        """Path of the pid record for this composition."""
        return config.resolved_output_dir / PID_FILENAME

    def _write_pid_file(self, config: ComposeConfig, pid: int) -> None:
        """Record the detached server so a later ``down`` can find it."""
        path = self._pid_file(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": pid,
            "name": config.name,
            "port": int(config.settings.port),
            "started_at": datetime.now(UTC).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _read_pid_file(self, config: ComposeConfig) -> dict[str, Any] | None:
        """Return the pid record with an integer ``pid``, or ``None`` when absent/unreadable."""
        try:
            data = json.loads(self._pid_file(config).read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            data["pid"] = int(data["pid"])
        except KeyError, TypeError, ValueError:
            return None
        return data

    def _remove_pid_file(self, config: ComposeConfig) -> None:
        """Delete the pid record (best-effort)."""
        with contextlib.suppress(OSError):
            self._pid_file(config).unlink()

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return True when a process with ``pid`` exists.

        POSIX probes with signal 0. Windows cannot: ``os.kill(pid, 0)`` there
        is not a probe — any signal other than the two CTRL events calls
        ``TerminateProcess`` — so it opens a query-only handle instead.
        """
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes

            kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _pid_is_compose_server(pid: int) -> bool:
        """Best-effort check that ``pid`` still runs a Cortex server.

        Guards against pid reuse after a crash or reboot: on Linux the
        process's command line must mention the Cortex entrypoint. Where
        the command line cannot be read, the pid is trusted.
        """
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return True
        return b"chaoscypher_cortex" in cmdline

    @staticmethod
    def _signal(pid: int, sig: signal.Signals) -> None:
        """Deliver ``sig`` to the detached server.

        The server was started with ``start_new_session=True``, so on POSIX
        its pid is also its process-group id and ``killpg`` reaches any
        children it spawned. Windows has no process groups here; ``os.kill``
        terminates the recorded process.
        """
        try:
            if sys.platform == "win32":
                os.kill(pid, sig)
            else:
                os.killpg(pid, sig)
        except ProcessLookupError:
            logger.info("compose_server_already_gone", pid=pid)

    async def run(
        self,
        config: ComposeConfig,
        command: list[str],
    ) -> int:
        """Run a command in the composition context.

        Sets up environment variables and runs the specified command.

        Args:
            config: Composition configuration.
            command: Command and arguments to run.

        Returns:
            Command exit code.
        """
        output_dir = config.resolved_output_dir

        # Point the child at the composed database exactly the way `up` does:
        # data dir = the composition's output dir, database = "default".
        env = self._composition_env(config)

        logger.info(
            "compose_run_command",
            command=command,
            database=str(output_dir),
        )

        # Run the command
        import os

        full_env = os.environ.copy()
        full_env.update(env)

        proc = await asyncio.create_subprocess_exec(
            *command,
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if stdout:
            sys.stdout.buffer.write(stdout)
        if stderr:
            sys.stderr.buffer.write(stderr)

        return proc.returncode or 0

    async def _start_server(
        self,
        config: ComposeConfig,
        detach: bool = False,
    ) -> None:
        """Start the ChaosCypher server with the composed database.

        Args:
            config: Composition configuration.
            detach: Run in background.
        """
        output_dir = config.resolved_output_dir

        port = int(config.settings.port)
        if not (1 <= port <= 65535):
            raise ComposeError("Invalid port number", stage="serve")

        if not (output_dir / "databases" / COMPOSED_DATABASE_NAME).exists():
            msg = f"No composed database under {output_dir}; run `compose build` first"
            raise ComposeError(msg, stage="serve")

        # Launch Cortex the way `chaoscypher serve` does — the composed
        # database is selected through the environment, not CLI flags.
        cmd = [
            sys.executable,
            "-m",
            "chaoscypher_cortex.main",
            "start",
            "--host",
            SERVER_HOST,
            "--port",
            str(port),
        ]
        env = os.environ.copy()
        env.update(self._composition_env(config))

        logger.info(
            "compose_starting_server",
            port=config.settings.port,
            data_dir=str(output_dir),
            detach=detach,
        )

        if detach:
            # Refuse to stack a second detached server on the same composition.
            existing = self._read_pid_file(config)
            if (
                existing is not None
                and self._pid_alive(existing["pid"])
                and self._pid_is_compose_server(existing["pid"])
            ):
                msg = (
                    f"Composition '{config.name}' is already running "
                    f"(pid {existing['pid']}); run `chaoscypher compose down` first"
                )
                raise ComposeError(msg, stage="serve")

            # Background mode. The server's output goes to a log file in the
            # output directory so a crash is diagnosable after the fact.
            log_path = output_dir / SERVER_LOG_FILENAME
            with log_path.open("ab") as log_file:
                self._server_process = subprocess.Popen(  # noqa: S603
                    cmd,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            # A server that dies during startup — or never binds its port —
            # must not be reported as started. Wait until the port answers,
            # the process exits, or the timeout lapses.
            outcome = await self._wait_until_listening(self._server_process, port)
            if outcome != "listening":
                exit_code = self._server_process.poll()
                if exit_code is None:
                    # Alive but not listening: do not leave it running.
                    self._signal(self._server_process.pid, signal.SIGTERM)
                self._server_process = None
                tail = self._log_tail(log_path)
                what = (
                    f"exited during startup (exit code {exit_code})"
                    if exit_code is not None
                    else f"did not start listening on port {port} within "
                    f"{STARTUP_TIMEOUT_SECONDS:.0f}s"
                )
                msg = f"Composition server {what}; see {log_path}" + (f":\n{tail}" if tail else "")
                raise ComposeError(msg, stage="serve")
            # The Popen handle dies with this process; the pid record is
            # what lets a later `compose down` invocation stop the server.
            self._write_pid_file(config, self._server_process.pid)
            logger.info(
                "compose_server_started_background",
                pid=self._server_process.pid,
                pid_file=str(self._pid_file(config)),
                log=str(log_path),
            )
        else:
            # Foreground mode: cooperatively awaits until the child exits.
            _spawn = asyncio.create_subprocess_exec
            process = await _spawn(*cmd, env=env)

            def _shutdown() -> None:
                """Signal handler that terminates the foreground server child."""
                logger.info("compose_server_shutdown_signal")
                process.terminate()

            # loop.add_signal_handler is POSIX-only; on Windows fall back
            # to signal.signal.
            if sys.platform != "win32":
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, _shutdown)
            else:
                signal.signal(signal.SIGINT, lambda _sig, _frame: _shutdown())
                signal.signal(signal.SIGTERM, lambda _sig, _frame: _shutdown())

            await process.wait()

    async def _stop_server(self) -> None:
        """Stop the background server if running."""
        if self._server_process:
            from chaoscypher_core.settings import ComposeSettings

            terminate_timeout = ComposeSettings().process_terminate_timeout
            self._server_process.terminate()
            try:
                self._server_process.wait(timeout=terminate_timeout)
            except subprocess.TimeoutExpired:
                self._server_process.kill()
            self._server_process = None
            logger.info("compose_server_stopped")


__all__ = [
    "ComposeError",
    "ComposeService",
]
