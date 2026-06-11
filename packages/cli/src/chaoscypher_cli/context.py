# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI Context - Manages adapters, repositories, and services.

Provides a unified context for all CLI commands to access core library
services with proper initialization and configuration.

The context handles:
- Database path resolution
- Engine bootstrap via ``Engine()``
- Settings management
- LLM provider lazy initialization

Usage:
    from chaoscypher_cli.context import get_context

    ctx = get_context()  # Uses default database
    ctx = get_context(database_name="my-project")  # Specific database

    # Access services
    nodes = ctx.node_service.list_nodes()
    edges = ctx.edge_service.list_edges()
    templates = ctx.template_service.list_templates()

Note:
    Heavy imports from chaoscypher_core are deferred until connect() is called.
    This improves CLI startup time significantly for commands that don't need
    the full core library (e.g., login, logout, help).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core import EngineSettings
    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.services.graph.management.edge import EdgeService
    from chaoscypher_core.services.graph.management.node import NodeService
    from chaoscypher_core.services.graph.management.template import TemplateService
    from chaoscypher_core.services.workflows.management import WorkflowService


def get_database_name(override: str | None = None) -> str:
    """Resolve database name from override, env, config, or default.

    Resolution order:
    1. Explicit override (from --database flag)
    2. CHAOSCYPHER_DATABASE environment variable
    3. settings.yaml current_database
    4. Fallback to "default"

    Args:
        override: Explicit database name from command flag

    Returns:
        Resolved database name

    """
    # 1. Explicit override takes priority (but not "default" which is Click's default)
    # This allows env/config to override when user doesn't specify --database
    if override and override != "default":
        return override

    # 2. Environment variable
    env_db = os.environ.get("CHAOSCYPHER_DATABASE")
    if env_db:
        return env_db

    # 3. settings.yaml current_database (engine config moved out of cli.yaml
    #    in the 2026-06 config unification; cheap raw peek, no Dynaconf)
    try:
        from chaoscypher_cli.engine_config import read_current_database

        current = read_current_database()
        if current and current != "default":
            return current
    except Exception:
        pass  # Best-effort UX helper; fall through to the default.

    # 4. Default
    return "default"


class CLIContext:
    """Context manager for CLI operations.

    Manages adapters, repositories, and services for CLI commands.
    Delegates to ``Engine`` from ``chaoscypher_core.bootstrap`` for
    service wiring — the CLI only adds LLM provider initialization.

    Attributes:
        database_name: Name of the current database
        data_dir: Root data directory (XDG-compliant)
        database_dir: Directory for this specific database
        settings: Engine settings instance
        storage_adapter: SQLite storage adapter
        graph_repository: RDF graph repository
        search_repository: Search/vector repository
        node_service: Node CRUD service
        edge_service: Edge CRUD service
        template_service: Template CRUD service
        workflow_service: Workflow CRUD service
        llm_provider: Optional LLM provider for extraction (lazy-initialized)
        has_llm: Whether LLM provider is available

    Example:
        ctx = CLIContext(database_name="my-project")
        ctx.connect()

        # Use services
        nodes = ctx.node_service.list_nodes()

        ctx.disconnect()

    """

    def __init__(
        self,
        database_name: str = "default",
        data_dir: str | Path | None = None,
    ):
        """Initialize CLI context.

        Args:
            database_name: Name of the database to use
            data_dir: Override data directory (default: XDG data dir)

        """
        self.database_name = database_name

        # Resolve data directory
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            # Use platformdirs for cross-platform path or environment override
            import platformdirs

            self.data_dir = Path(
                os.getenv(
                    "CHAOSCYPHER_DATA_DIR",
                    platformdirs.user_data_dir("chaoscypher", appauthor=False),
                )
            )

        # Database-specific directory
        self.database_dir = self.data_dir / "databases" / database_name

        # Settings (initialized lazily in connect)
        self._settings: EngineSettings | None = None

        # Engine (initialized on connect, holds all adapters/repos/services)
        self._engine: Engine | None = None

        # LLM provider (lazy-initialized on first access)
        self._llm_provider: LLMProvider | None = None
        self._llm_checked = False

        # Embedding provider (lazy-initialized on first access)
        self._embedding_provider: EmbeddingProviderProtocol | None = None

        self._connected = False

    def connect(self) -> None:
        """Initialize the engine with all adapters, repositories, and services.

        Creates necessary directories, initializes database tables, and
        bootstraps the full service layer via ``Engine()``.

        Note:
            Heavy chaoscypher_core imports happen here, not at module load.
            This keeps CLI startup fast for commands that don't need the core.

        """
        if self._connected:
            return

        from chaoscypher_core.utils.logging import configure_logging

        # Route structlog through stdlib logging so log level filtering works.
        # Default to WARNING for CLI — keeps output clean for end users.
        # Override with LOG_LEVEL=INFO or LOG_LEVEL=DEBUG for troubleshooting.
        configure_logging(log_level=os.getenv("LOG_LEVEL", "WARNING"))

        # Initialize settings from settings.yaml via the app_config pipeline.
        from chaoscypher_core.exceptions import ConfigError

        try:
            self._settings = self._create_engine_settings()
        except ConfigError as exc:
            # Strict YAML mode rejected settings.yaml (unknown key, bad type).
            # Surface the did-you-mean message without a traceback.
            import click

            click.echo(click.style(f"Configuration error: {exc}", fg="red"), err=True)
            raise SystemExit(2) from exc

        from chaoscypher_core.bootstrap import Engine

        self._engine = Engine(
            data_dir=self.database_dir,
            settings=self._settings,
            initialize_db=True,
        )
        # Engine() may adjust settings (paths.data_dir, current_database)
        self._settings = self._engine.settings

        self._connected = True

        import structlog

        logger = structlog.get_logger(__name__)
        logger.info(
            "cli_context_connected",
            database_name=self.database_name,
            database_dir=str(self.database_dir),
        )

    def disconnect(self) -> None:
        """Clean up and disconnect all adapters."""
        if not self._connected:
            return

        if self._engine:
            self._engine.close()
            self._engine = None

        self._settings = None
        self._llm_provider = None
        self._llm_checked = False
        self._embedding_provider = None
        self._connected = False

        import structlog

        logger = structlog.get_logger(__name__)
        logger.info("cli_context_disconnected", database_name=self.database_name)

    # ========================================================================
    # Private helpers
    # ========================================================================

    def _ensure_connected(self) -> None:
        """Raise RuntimeError if not connected."""
        if not self._connected or self._engine is None:
            msg = "Not connected. Call connect() first."
            raise RuntimeError(msg)

    # ========================================================================
    # Properties for accessing services (delegated to Engine)
    # ========================================================================

    @property
    def settings(self) -> EngineSettings:
        """Get the engine settings (raises if not connected)."""
        self._ensure_connected()
        return self._engine.settings  # type: ignore[union-attr]

    @property
    def storage_adapter(self) -> SqliteAdapter:
        """Get the storage adapter (raises if not connected)."""
        self._ensure_connected()
        return self._engine.storage_adapter  # type: ignore[union-attr]

    @property
    def graph_repository(self) -> GraphRepository:
        """Get the graph repository (raises if not connected)."""
        self._ensure_connected()
        return self._engine.graph_repository  # type: ignore[union-attr]

    @property
    def search_repository(self) -> SearchRepository:
        """Get the search repository (raises if not connected)."""
        self._ensure_connected()
        return self._engine.search_repository  # type: ignore[union-attr]

    @property
    def node_service(self) -> NodeService:
        """Get the node service (raises if not connected)."""
        self._ensure_connected()
        return self._engine.node_service  # type: ignore[union-attr]

    @property
    def edge_service(self) -> EdgeService:
        """Get the edge service (raises if not connected)."""
        self._ensure_connected()
        return self._engine.edge_service  # type: ignore[union-attr]

    @property
    def template_service(self) -> TemplateService:
        """Get the template service (raises if not connected)."""
        self._ensure_connected()
        return self._engine.template_service  # type: ignore[union-attr]

    @property
    def workflow_service(self) -> WorkflowService:
        """Get the workflow service (raises if not connected)."""
        self._ensure_connected()
        return self._engine.workflow_service  # type: ignore[union-attr]

    # ========================================================================
    # LLM Provider (Lazy-Initialized)
    # ========================================================================

    @property
    def has_llm(self) -> bool:
        """Check if LLM provider is available.

        Attempts to initialize the LLM provider on first check.
        Returns True if LLM is configured and accessible.
        """
        if not self._llm_checked:
            # Try to initialize LLM provider
            _ = self.llm_provider
        return self._llm_provider is not None

    def refresh_llm(self) -> None:
        """Forget the cached LLM probe and re-read settings from disk.

        Called after the in-command setup wizard writes settings.yaml: this
        context (and its negative has_llm probe) was built before the wizard
        ran, so without a refresh the very operation that triggered the
        wizard still sees "no LLM configured". Only the settings object is
        rebuilt — the engine keeps its storage wiring (the wizard writes
        LLM/embedding settings, not paths), and the engine-adjusted fields
        are carried over.
        """
        self._llm_checked = False
        self._llm_provider = None
        if self._connected and self._settings is not None:
            fresh = self._create_engine_settings()
            # Preserve the adjustments Engine() made at connect time (see
            # connect(): paths.data_dir and current_database).
            fresh.paths.data_dir = self._settings.paths.data_dir
            fresh.current_database = self._settings.current_database
            self._settings = fresh

    @property
    def llm_provider(self) -> LLMProvider | None:
        """Get the LLM provider (lazy-initialized).

        Returns None if LLM is not configured or unavailable.
        Does not raise - caller should check has_llm first or handle None.
        """
        if self._llm_checked:
            return self._llm_provider

        self._llm_checked = True
        self._ensure_connected()

        provider_name = self.settings.llm.chat_provider
        if not provider_name:
            return None

        # Validate provider availability (network/API key checks)
        if not self._validate_llm_available(provider_name):
            return None

        try:
            from chaoscypher_core.adapters.llm.provider import LLMProvider as LLMProviderClass

            self._llm_provider = LLMProviderClass(
                settings=self.settings,
                managers={},
            )

            import structlog

            logger = structlog.get_logger(__name__)
            logger.info(
                "llm_provider_initialized",
                provider=provider_name,
            )

        except Exception as e:
            import structlog

            logger = structlog.get_logger(__name__)
            logger.warning(
                "llm_provider_initialization_failed",
                error=str(e),
            )
            self._llm_provider = None

        return self._llm_provider

    @property
    def embedding_service(self) -> EmbeddingProviderProtocol:
        """Get embedding provider (lazy-initialized via factory).

        Returns:
            EmbeddingProviderProtocol instance configured from engine settings.
        """
        if self._embedding_provider is None:
            from chaoscypher_core.adapters.embedding import create_embedding_provider

            self._embedding_provider = create_embedding_provider(self.settings)
        return self._embedding_provider

    def _validate_llm_available(self, provider: str) -> bool:  # noqa: PLR0911
        """Check if the configured LLM provider is reachable.

        For Ollama, also checks that the configured chat model is installed
        and offers to pull it if it is missing.

        Args:
            provider: Provider name (ollama, openai, anthropic, gemini)

        Returns:
            True if the provider appears available
        """
        if provider == "ollama":
            try:
                import json
                import urllib.error
                import urllib.request

                base_url = self.settings.llm.primary_ollama_url
                url = f"{base_url}/api/tags"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(
                    req, timeout=self.settings.cli.ollama_connect_timeout
                ) as resp:
                    data = json.loads(resp.read().decode())

                # Connectivity confirmed — check model availability
                installed: list[str] = [m.get("name", "") for m in data.get("models", [])]
                chat_model = self.settings.llm.ollama_chat_model
                if chat_model and chat_model not in installed:
                    from rich.prompt import Confirm

                    pull = Confirm.ask(
                        f"Model '{chat_model}' is not installed. Pull it now?",
                        default=True,
                    )
                    if pull:
                        return self._pull_ollama_model(chat_model, base_url)

                    import structlog

                    structlog.get_logger(__name__).warning(
                        "ollama_model_not_installed",
                        model=chat_model,
                    )
                    return False

                return True

            except (urllib.error.URLError, TimeoutError, OSError):  # fmt: skip
                return False

        if provider == "openai":
            return bool(self.settings.llm.openai_api_key)

        if provider == "anthropic":
            return bool(self.settings.llm.anthropic_api_key)

        if provider == "gemini":
            return bool(self.settings.llm.gemini_api_key)

        return False

    def _pull_ollama_model(self, model: str, base_url: str) -> bool:
        """Pull an Ollama model with a progress display.

        Sends a streaming POST to the Ollama pull endpoint and shows a
        ``rich.progress.Progress`` bar while the download completes.

        Args:
            model: Model name to pull (e.g. ``qwen3:30b``).
            base_url: Ollama base URL (e.g. ``http://localhost:11434``).

        Returns:
            True if the model was pulled successfully, False otherwise.
        """
        import json
        import urllib.error
        import urllib.request

        from rich.console import Console
        from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn

        console = Console()
        console.print(f"  Pulling [bold]{model}[/bold] from Ollama...")

        payload = json.dumps({"name": model, "stream": True}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/pull",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with Progress(
                TextColumn("  [progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=True,
            ) as progress:
                task_id = progress.add_task("Downloading...", total=None)

                with urllib.request.urlopen(
                    req, timeout=self.settings.cli.ollama_pull_timeout
                ) as resp:
                    for raw_line in resp:
                        line = raw_line.decode().strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        status = event.get("status", "")
                        total = event.get("total")
                        completed = event.get("completed")

                        if total and completed is not None:
                            progress.update(
                                task_id,
                                total=total,
                                completed=completed,
                                description=status or "Downloading...",
                            )
                        else:
                            progress.update(task_id, description=status or "Working...")

                        if status == "success":
                            progress.update(task_id, completed=progress.tasks[0].total or 1)
                            break

            console.print(f"  [green]✓[/green] Model [bold]{model}[/bold] ready.")
            return True

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            import structlog

            structlog.get_logger(__name__).error(
                "ollama_model_pull_failed",
                model=model,
                error=str(exc),
            )
            console.print(f"  [red]✗[/red] Failed to pull model [bold]{model}[/bold]: {exc}")
            return False

    def _create_engine_settings(self) -> EngineSettings:
        """Build EngineSettings from data_dir/settings.yaml (single source of truth).

        Uses the same app_config pipeline Cortex uses (Dynaconf load, strict
        YAML mode, CHAOSCYPHER_* env precedence) and the shared converter, so
        a CLI-launched engine and every direct ``app_config.get_settings()``
        call site in core read identical config. cli.yaml no longer carries
        engine-level settings (2026-06 config unification).

        Returns:
            EngineSettings derived from settings.yaml, with the resolved
            database name (--database flag / CHAOSCYPHER_DATABASE env /
            settings.yaml current_database) applied on top.
        """
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings

        engine_settings = build_engine_settings(get_settings())
        # self.database_name was resolved by get_database_name(), which gives
        # the flag and env var precedence over the persisted current_database.
        engine_settings.current_database = self.database_name
        return engine_settings

    # ========================================================================
    # Statistics and Info
    # ========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get database statistics.

        Returns:
            Dictionary with node, edge, template counts and database info.

        """
        self._ensure_connected()

        return {
            "database_name": self.database_name,
            "database_dir": str(self.database_dir),
            "nodes": self.graph_repository.count_nodes(),
            "edges": self.graph_repository.count_edges(),
            "templates": self.graph_repository.count_templates(database_name=self.database_name),
        }


# ============================================================================
# Context Factory
# ============================================================================


_context_instance: CLIContext | None = None


def get_context(
    database_name: str | None = None,
    data_dir: str | Path | None = None,
    auto_connect: bool = True,
) -> CLIContext:
    """Get or create the CLI context.

    Args:
        database_name: Database name override (default: resolved from config)
        data_dir: Override data directory
        auto_connect: Automatically connect on creation

    Returns:
        CLIContext instance

    """
    global _context_instance

    # Resolve database name using priority chain
    resolved_db = get_database_name(database_name)

    # Use existing context if same database
    if _context_instance is not None:
        if resolved_db == _context_instance.database_name:
            return _context_instance

        # Different database - disconnect and create new
        _context_instance.disconnect()

    # Create new context
    _context_instance = CLIContext(
        database_name=resolved_db,
        data_dir=data_dir,
    )

    if auto_connect:
        _context_instance.connect()

    return _context_instance


def _make_pass_context() -> Any:
    """Create a click decorator that passes the CLI context to commands.

    Returns:
        A click decorator that provides CLIContext to commands.
    """
    import functools
    from collections.abc import Callable

    import click

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @click.pass_context
        @functools.wraps(f)
        def wrapper(click_ctx: click.Context, *args: Any, **kwargs: Any) -> Any:
            ctx = get_context()
            return click_ctx.invoke(f, ctx, *args, **kwargs)

        return wrapper

    return decorator


# Create the pass_context decorator
pass_context = _make_pass_context()


def refresh_llm_state() -> None:
    """Refresh the cached context's LLM probe after settings changed on disk.

    No-op when no context exists yet (the next ``get_context()`` builds
    fresh and sees the new settings anyway).
    """
    if _context_instance is not None:
        _context_instance.refresh_llm()


def reset_context() -> None:
    """Tear down the cached CLI context (test-isolation helper).

    Disconnects the live ``CLIContext`` if one exists and clears the
    module-level singleton so the next ``get_context()`` call rebuilds
    from current environment + settings. Tests that exercise the CLI
    against different ``CHAOSCYPHER_DATA_DIR`` values must call this
    between invocations — otherwise the cached Engine keeps pointing at
    the first test's tmp_path.
    """
    global _context_instance
    if _context_instance is not None:
        _context_instance.disconnect()
        _context_instance = None


__all__ = [
    "CLIContext",
    "get_context",
    "get_database_name",
    "pass_context",
    "refresh_llm_state",
    "reset_context",
]
