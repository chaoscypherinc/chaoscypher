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
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.services.compose.merger import MergerError, NamespaceMerger
from chaoscypher_core.services.compose.models import (
    ComposeConfig,
    CompositionResult,
)
from chaoscypher_core.services.compose.resolver import PackageResolver, ResolverError


if TYPE_CHECKING:
    from chaoscypher_core.services.lexicon import AuthConfig


logger = structlog.get_logger(__name__)


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

            # Write compose config to output for reference
            if result.success:
                config_copy = output_dir / "axiomatize.yaml"
                config.to_yaml(config_copy)

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
        db_exists = (output_dir / "databases" / "default").exists()

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

    async def down(self, config: ComposeConfig) -> None:
        """Stop the composition server.

        Args:
            config: Composition configuration.
        """
        await self._stop_server()
        logger.info("compose_stopped", name=config.name)

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

        # Set up environment
        env = {
            "CHAOSCYPHER_DATABASE": str(output_dir / "databases" / "default"),
            "CHAOSCYPHER_COMPOSE_NAME": config.name,
        }

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

        # Build server command
        cmd = [
            sys.executable,
            "-m",
            "chaoscypher_cortex",
            "start",
            "--mode",
            "reader",
            "--port",
            str(port),
            "--data-dir",
            str(output_dir),
        ]

        logger.info(
            "compose_starting_server",
            port=config.settings.port,
            data_dir=str(output_dir),
            detach=detach,
        )

        if detach:
            # Background mode
            self._server_process = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(
                "compose_server_started_background",
                pid=self._server_process.pid,
            )
        else:
            # Foreground mode: cooperatively awaits until the child exits.
            _spawn = asyncio.create_subprocess_exec
            process = await _spawn(*cmd)

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
