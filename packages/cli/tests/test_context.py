# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ``chaoscypher_cli.context``.

Exercises the CLI context object directly with heavy ``chaoscypher_core``
dependencies (the ``Engine`` bootstrap, the ``LLMProvider`` factory, the
embedding factory) mocked at their import boundary so nothing touches the
real filesystem, network, or LLM. Covers:

- ``get_database_name`` resolution chain (override / env / config / default).
- ``CLIContext.__init__`` data-dir resolution (explicit, env override,
  platformdirs default) and database-dir composition.
- ``connect`` / ``disconnect`` lifecycle + idempotency, and the
  ``_ensure_connected`` guard on every delegated property.
- ``has_llm`` / ``llm_provider`` lazy init in the configured, not-configured,
  validation-fails, and factory-raises branches, plus result caching.
- ``_validate_llm_available`` for each provider (key-based + ollama reachable
  / unreachable / model-missing) and the unknown-provider fallthrough.
- ``embedding_service`` lazy init + caching.
- ``_create_engine_settings`` provider-configured / no-provider / config-
  unavailable branches.
- ``get_stats`` aggregation and the ``get_context`` / ``reset_context``
  module singleton.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cli import context as ctx_mod
from chaoscypher_cli.context import (
    CLIContext,
    get_context,
    get_database_name,
    reset_context,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env_and_singleton() -> Iterator[None]:
    """Clear the module singleton and the env vars the context reads.

    The context caches a process-wide ``_context_instance`` and reads
    ``CHAOSCYPHER_DATABASE`` / ``CHAOSCYPHER_DATA_DIR``; strip both so each
    test starts from a known state and leaves nothing behind.
    """
    saved = {
        key: os.environ.get(key)
        for key in ("CHAOSCYPHER_DATABASE", "CHAOSCYPHER_DATA_DIR", "LOG_LEVEL")
    }
    for key in saved:
        os.environ.pop(key, None)
    # Drop any singleton an earlier test may have left around.
    ctx_mod._context_instance = None
    try:
        yield
    finally:
        ctx_mod._context_instance = None
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _connected_context(database_name: str = "default") -> tuple[CLIContext, MagicMock]:
    """Return a CLIContext whose ``_engine`` is a MagicMock (no real connect).

    Bypasses ``connect()`` so property delegation and the LLM/embedding lazy
    paths can be tested without a real Engine. Returns ``(ctx, engine_mock)``.
    """
    ctx = CLIContext(database_name=database_name, data_dir="/tmp/whatever")
    engine = MagicMock()
    ctx._engine = engine
    ctx._connected = True
    return ctx, engine


# ---------------------------------------------------------------------------
# get_database_name
# ---------------------------------------------------------------------------


class TestGetDatabaseName:
    """The four-step resolution chain for the active database name."""

    def test_explicit_override_wins(self) -> None:
        os.environ["CHAOSCYPHER_DATABASE"] = "from_env"
        assert get_database_name("my_project") == "my_project"

    def test_literal_default_override_is_treated_as_no_override(self) -> None:
        os.environ["CHAOSCYPHER_DATABASE"] = "from_env"
        # "default" is Click's literal default -> ignored, env wins.
        assert get_database_name("default") == "from_env"

    def test_env_var_used_when_no_override(self) -> None:
        os.environ["CHAOSCYPHER_DATABASE"] = "env_db"
        assert get_database_name() == "env_db"

    def test_settings_yaml_current_database_used_when_no_override_or_env(
        self, isolated_settings: Path
    ) -> None:
        # Engine config moved out of cli.yaml: step 3 reads settings.yaml's
        # current_database via the cheap engine_config peek.
        _write_settings(isolated_settings, {"current_database": "config_db"})
        assert get_database_name() == "config_db"

    def test_settings_yaml_default_falls_through_to_final_default(
        self, isolated_settings: Path
    ) -> None:
        _write_settings(isolated_settings, {"current_database": "default"})
        assert get_database_name() == "default"

    def test_missing_settings_yaml_falls_back_to_default(self, isolated_settings: Path) -> None:
        # No settings.yaml written -> the peek returns {} and we fall through.
        assert get_database_name() == "default"

    def test_corrupt_settings_yaml_falls_back_to_default(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text("{not: valid: yaml", encoding="utf-8")
        assert get_database_name() == "default"


# ---------------------------------------------------------------------------
# CLIContext.__init__ — data dir resolution
# ---------------------------------------------------------------------------


class TestInitDataDirResolution:
    """``__init__`` resolves data_dir from arg, env, or platformdirs."""

    def test_explicit_data_dir_used(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        assert ctx.data_dir == tmp_path
        assert ctx.database_name == "proj"

    def test_database_dir_is_composed_under_databases(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        assert ctx.database_dir == tmp_path / "databases" / "proj"

    def test_env_override_used_when_no_explicit_dir(self, tmp_path: Path) -> None:
        os.environ["CHAOSCYPHER_DATA_DIR"] = str(tmp_path)
        ctx = CLIContext(database_name="proj")
        assert ctx.data_dir == tmp_path
        assert ctx.database_dir == tmp_path / "databases" / "proj"

    def test_platformdirs_default_used_when_no_dir_and_no_env(self, tmp_path: Path) -> None:
        target = tmp_path / "platform-data"
        with patch("platformdirs.user_data_dir", return_value=str(target)) as mock_pd:
            ctx = CLIContext(database_name="proj")
        mock_pd.assert_called_once_with("chaoscypher", appauthor=False)
        assert ctx.data_dir == target

    def test_not_connected_after_init(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        assert ctx._connected is False
        assert ctx._engine is None
        assert ctx._settings is None


# ---------------------------------------------------------------------------
# connect / disconnect lifecycle
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """connect bootstraps the Engine; disconnect tears it down."""

    def test_connect_builds_engine_with_database_dir(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)

        engine = MagicMock()
        engine.settings = MagicMock(name="engine_settings")
        fake_settings = MagicMock(name="cli_settings")

        with (
            patch("chaoscypher_core.utils.logging.configure_logging") as mock_log,
            patch("chaoscypher_core.bootstrap.Engine", return_value=engine) as mock_engine,
            patch.object(CLIContext, "_create_engine_settings", return_value=fake_settings),
        ):
            ctx.connect()

        mock_log.assert_called_once()
        mock_engine.assert_called_once_with(
            data_dir=ctx.database_dir,
            settings=fake_settings,
            initialize_db=True,
        )
        # Engine() may rewrite settings; the context adopts the engine's copy.
        assert ctx._settings is engine.settings
        assert ctx._engine is engine
        assert ctx._connected is True

    def test_connect_honours_log_level_env(self, tmp_path: Path) -> None:
        os.environ["LOG_LEVEL"] = "DEBUG"
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        with (
            patch("chaoscypher_core.utils.logging.configure_logging") as mock_log,
            patch("chaoscypher_core.bootstrap.Engine", return_value=MagicMock()),
            patch.object(CLIContext, "_create_engine_settings", return_value=MagicMock()),
        ):
            ctx.connect()
        mock_log.assert_called_once_with(log_level="DEBUG")

    def test_connect_is_idempotent(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        with (
            patch("chaoscypher_core.utils.logging.configure_logging"),
            patch("chaoscypher_core.bootstrap.Engine", return_value=MagicMock()) as mock_engine,
            patch.object(CLIContext, "_create_engine_settings", return_value=MagicMock()),
        ):
            ctx.connect()
            ctx.connect()  # second call short-circuits
        mock_engine.assert_called_once()

    def test_disconnect_closes_engine_and_resets_state(self) -> None:
        ctx, engine = _connected_context()
        ctx._llm_provider = MagicMock()
        ctx._llm_checked = True
        ctx._embedding_provider = MagicMock()

        ctx.disconnect()

        engine.close.assert_called_once()
        assert ctx._engine is None
        assert ctx._settings is None
        assert ctx._llm_provider is None
        assert ctx._llm_checked is False
        assert ctx._embedding_provider is None
        assert ctx._connected is False

    def test_disconnect_when_never_connected_is_noop(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        # Should not raise even though there is no engine.
        ctx.disconnect()
        assert ctx._connected is False


# ---------------------------------------------------------------------------
# _ensure_connected guard + property delegation
# ---------------------------------------------------------------------------


class TestEnsureConnectedGuard:
    """Every delegated property raises RuntimeError before connect()."""

    @pytest.mark.parametrize(
        "prop",
        [
            "settings",
            "storage_adapter",
            "graph_repository",
            "search_repository",
            "node_service",
            "edge_service",
            "template_service",
            "workflow_service",
        ],
    )
    def test_property_raises_when_not_connected(self, prop: str, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Not connected"):
            getattr(ctx, prop)

    @pytest.mark.parametrize(
        "prop",
        [
            "settings",
            "storage_adapter",
            "graph_repository",
            "search_repository",
            "node_service",
            "edge_service",
            "template_service",
            "workflow_service",
        ],
    )
    def test_property_delegates_to_engine_when_connected(self, prop: str) -> None:
        ctx, engine = _connected_context()
        sentinel = getattr(engine, prop)
        assert getattr(ctx, prop) is sentinel


# ---------------------------------------------------------------------------
# llm_provider / has_llm lazy initialization
# ---------------------------------------------------------------------------


class TestLLMProviderLazyInit:
    """The LLM provider is built once, only when configured and reachable."""

    def test_returns_none_when_no_provider_configured(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = ""  # not configured

        assert ctx.llm_provider is None
        assert ctx.has_llm is False
        # The empty-provider short-circuit still flips the checked flag.
        assert ctx._llm_checked is True

    def test_builds_provider_when_configured_and_available(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = "openai"

        provider_instance = MagicMock(name="provider")
        provider_cls = MagicMock(return_value=provider_instance)

        with (
            patch.object(CLIContext, "_validate_llm_available", return_value=True),
            patch(
                "chaoscypher_core.adapters.llm.provider.LLMProvider",
                provider_cls,
            ),
        ):
            result = ctx.llm_provider

        assert result is provider_instance
        provider_cls.assert_called_once_with(settings=ctx.settings, managers={})
        assert ctx.has_llm is True

    def test_returns_none_when_validation_fails(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = "openai"

        with patch.object(CLIContext, "_validate_llm_available", return_value=False):
            assert ctx.llm_provider is None
            assert ctx.has_llm is False

    def test_returns_none_when_factory_raises(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = "anthropic"

        with (
            patch.object(CLIContext, "_validate_llm_available", return_value=True),
            patch(
                "chaoscypher_core.adapters.llm.provider.LLMProvider",
                side_effect=RuntimeError("init blew up"),
            ),
        ):
            assert ctx.llm_provider is None
            assert ctx.has_llm is False

    def test_provider_is_cached_after_first_access(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = "openai"

        provider_instance = MagicMock(name="provider")
        provider_cls = MagicMock(return_value=provider_instance)

        with (
            patch.object(CLIContext, "_validate_llm_available", return_value=True) as mock_validate,
            patch(
                "chaoscypher_core.adapters.llm.provider.LLMProvider",
                provider_cls,
            ),
        ):
            first = ctx.llm_provider
            second = ctx.llm_provider

        assert first is second
        # Built and validated exactly once despite two accesses.
        provider_cls.assert_called_once()
        mock_validate.assert_called_once()

    def test_has_llm_triggers_lazy_init_once(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.chat_provider = "openai"

        provider_cls = MagicMock(return_value=MagicMock())
        with (
            patch.object(CLIContext, "_validate_llm_available", return_value=True),
            patch(
                "chaoscypher_core.adapters.llm.provider.LLMProvider",
                provider_cls,
            ),
        ):
            # has_llm should drive the same lazy init path as llm_provider.
            assert ctx.has_llm is True
            assert ctx.has_llm is True
        provider_cls.assert_called_once()

    def test_llm_provider_raises_if_not_connected(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = ctx.llm_provider


# ---------------------------------------------------------------------------
# _validate_llm_available
# ---------------------------------------------------------------------------


class TestValidateLLMAvailable:
    """Per-provider availability checks (key-based and ollama network)."""

    @pytest.mark.parametrize(
        ("provider", "key_attr", "key_value", "expected"),
        [
            ("openai", "openai_api_key", "sk-123", True),
            ("openai", "openai_api_key", "", False),
            ("anthropic", "anthropic_api_key", "sk-ant", True),
            ("anthropic", "anthropic_api_key", None, False),
            ("gemini", "gemini_api_key", "g-key", True),
            ("gemini", "gemini_api_key", "", False),
        ],
    )
    def test_key_based_providers(
        self, provider: str, key_attr: str, key_value: Any, expected: bool
    ) -> None:
        ctx, engine = _connected_context()
        setattr(engine.settings.llm, key_attr, key_value)
        assert ctx._validate_llm_available(provider) is expected

    def test_unknown_provider_returns_false(self) -> None:
        ctx, _ = _connected_context()
        assert ctx._validate_llm_available("totally-made-up") is False

    def test_ollama_reachable_with_installed_model(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.primary_ollama_url = "http://localhost:11434"
        engine.settings.llm.ollama_chat_model = "qwen3"
        engine.settings.cli.ollama_connect_timeout = 2

        resp = MagicMock()
        resp.read.return_value = b'{"models": [{"name": "qwen3"}]}'
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            assert ctx._validate_llm_available("ollama") is True

    def test_ollama_unreachable_returns_false(self) -> None:
        import urllib.error

        ctx, engine = _connected_context()
        engine.settings.llm.primary_ollama_url = "http://localhost:11434"
        engine.settings.cli.ollama_connect_timeout = 2

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert ctx._validate_llm_available("ollama") is False

    def test_ollama_model_missing_user_declines_pull(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.primary_ollama_url = "http://localhost:11434"
        engine.settings.llm.ollama_chat_model = "not-installed"
        engine.settings.cli.ollama_connect_timeout = 2

        resp = MagicMock()
        resp.read.return_value = b'{"models": [{"name": "other-model"}]}'
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("urllib.request.urlopen", return_value=resp),
            patch("rich.prompt.Confirm.ask", return_value=False),
        ):
            assert ctx._validate_llm_available("ollama") is False

    def test_ollama_model_missing_user_accepts_pull(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.llm.primary_ollama_url = "http://localhost:11434"
        engine.settings.llm.ollama_chat_model = "not-installed"
        engine.settings.cli.ollama_connect_timeout = 2

        resp = MagicMock()
        resp.read.return_value = b'{"models": []}'
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("urllib.request.urlopen", return_value=resp),
            patch("rich.prompt.Confirm.ask", return_value=True),
            patch.object(CLIContext, "_pull_ollama_model", return_value=True) as mock_pull,
        ):
            assert ctx._validate_llm_available("ollama") is True
        mock_pull.assert_called_once_with("not-installed", "http://localhost:11434")


# ---------------------------------------------------------------------------
# _pull_ollama_model
# ---------------------------------------------------------------------------


class TestPullOllamaModel:
    """Streaming pull with a rich progress bar; success and failure paths."""

    def _settings(self) -> MagicMock:
        engine_settings = MagicMock()
        engine_settings.cli.ollama_pull_timeout = 60
        return engine_settings

    def test_pull_success_returns_true(self) -> None:
        ctx, engine = _connected_context()
        engine.settings.cli.ollama_pull_timeout = 60

        # Streaming response yields progress lines then a success event.
        lines = [
            b'{"status": "pulling manifest"}\n',
            b'{"status": "downloading", "total": 100, "completed": 50}\n',
            b"\n",  # blank line -> skipped
            b"not-json\n",  # JSON decode error -> skipped
            b'{"status": "success"}\n',
        ]
        resp = MagicMock()
        resp.__iter__ = lambda s: iter(lines)
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            result = ctx._pull_ollama_model("qwen3", "http://localhost:11434")

        assert result is True

    def test_pull_failure_returns_false(self) -> None:
        import urllib.error

        ctx, engine = _connected_context()
        engine.settings.cli.ollama_pull_timeout = 60

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = ctx._pull_ollama_model("qwen3", "http://localhost:11434")

        assert result is False


# ---------------------------------------------------------------------------
# pass_context decorator
# ---------------------------------------------------------------------------


class TestPassContextDecorator:
    """The Click decorator injects the resolved CLIContext as the first arg."""

    def test_decorator_passes_context_to_command(self) -> None:
        import click
        from click.testing import CliRunner

        from chaoscypher_cli.context import pass_context

        fake_ctx = MagicMock(spec=CLIContext)
        fake_ctx.database_name = "decorated"

        @click.command()
        @pass_context
        def cmd(ctx: CLIContext) -> None:
            click.echo(f"db={ctx.database_name}")

        with patch("chaoscypher_cli.context.get_context", return_value=fake_ctx):
            result = CliRunner().invoke(cmd, [])

        assert result.exit_code == 0, result.output
        assert "db=decorated" in result.output


# ---------------------------------------------------------------------------
# embedding_service
# ---------------------------------------------------------------------------


class TestEmbeddingService:
    """The embedding provider is built lazily via the core factory and cached."""

    def test_embedding_provider_built_via_factory_and_cached(self) -> None:
        ctx, engine = _connected_context()
        provider = MagicMock(name="embedding")
        factory = MagicMock(return_value=provider)

        with patch(
            "chaoscypher_core.adapters.embedding.create_embedding_provider",
            factory,
        ):
            first = ctx.embedding_service
            second = ctx.embedding_service

        assert first is provider
        assert second is provider
        factory.assert_called_once_with(ctx.settings)


# ---------------------------------------------------------------------------
# _create_engine_settings
# ---------------------------------------------------------------------------


def _write_settings(data_dir: Path, data: dict[str, Any]) -> None:
    import yaml

    (data_dir / "settings.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


class TestCreateEngineSettings:
    """Engine settings come from settings.yaml via the app_config pipeline."""

    def test_engine_settings_come_from_settings_yaml(self, isolated_settings: Path) -> None:
        _write_settings(
            isolated_settings,
            {
                "setup_completed": True,
                "llm": {
                    "chat_provider": "ollama",
                    "ollama_instances": [
                        {"id": "default", "name": "Default", "base_url": "http://gpu-box:11434"}
                    ],
                    "ollama_chat_model": "qwen3:30b",
                },
                "embedding": {
                    "provider": "ollama",
                    "model": "qwen3-embedding:0.6b",
                    "is_configured": True,
                },
            },
        )

        settings = CLIContext(database_name="proj")._create_engine_settings()

        assert settings.current_database == "proj"  # flag/arg wins over file
        assert settings.llm.chat_provider == "ollama"
        assert settings.llm.primary_ollama_url == "http://gpu-box:11434"
        assert settings.llm.ollama_chat_model == "qwen3:30b"
        assert settings.embedding.provider == "ollama"
        assert settings.embedding.model == "qwen3-embedding:0.6b"

    def test_defaults_when_settings_yaml_missing(self, isolated_settings: Path) -> None:
        settings = CLIContext(database_name="db2")._create_engine_settings()
        assert settings.current_database == "db2"
        assert settings.llm.chat_provider == "ollama"  # core default

    def test_split_brain_regression_engine_and_direct_get_settings_agree(
        self, isolated_settings: Path
    ) -> None:
        """The 2026-06 unification invariant: a CLI-launched engine and the
        direct app_config.get_settings() call sites in core (e.g.
        mcp/extraction.py) must read the SAME file. Before Tier 1 the engine
        read cli.yaml while direct sites read settings.yaml/defaults.
        """
        _write_settings(
            isolated_settings,
            {"llm": {"chat_provider": "ollama", "ollama_chat_model": "split-brain-probe"}},
        )

        engine_settings = CLIContext(database_name="default")._create_engine_settings()

        from chaoscypher_core.app_config import get_settings

        assert engine_settings.llm.ollama_chat_model == "split-brain-probe"
        assert get_settings().llm.ollama_chat_model == "split-brain-probe"

    def test_connect_surfaces_config_error_without_traceback(
        self, isolated_settings: Path, capsys
    ) -> None:
        from chaoscypher_core.exceptions import ConfigError

        def _boom() -> None:
            raise ConfigError("Unrecognized top-level setting(s): ollma (did you mean 'llm'?)")

        # _create_engine_settings lazily imports get_settings from app_config —
        # patch at the source module. Use ``patch`` as a context manager (not
        # monkeypatch) so the lru_cached symbol is restored before the
        # isolated_settings teardown calls get_settings.cache_clear().
        ctx = CLIContext(database_name="default")
        with (
            patch("chaoscypher_core.app_config.get_settings", _boom),
            pytest.raises(SystemExit) as exc_info,
        ):
            ctx.connect()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "Configuration error" in err
        assert "ollma" in err


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    """get_stats aggregates counts from the graph repository."""

    def test_get_stats_aggregates_counts(self) -> None:
        ctx, engine = _connected_context(database_name="statsdb")
        engine.graph_repository.count_nodes.return_value = 11
        engine.graph_repository.count_edges.return_value = 7
        engine.graph_repository.count_templates.return_value = 3

        stats = ctx.get_stats()

        assert stats["database_name"] == "statsdb"
        assert stats["database_dir"] == str(ctx.database_dir)
        assert stats["nodes"] == 11
        assert stats["edges"] == 7
        assert stats["templates"] == 3
        engine.graph_repository.count_templates.assert_called_once_with(database_name="statsdb")

    def test_get_stats_raises_if_not_connected(self, tmp_path: Path) -> None:
        ctx = CLIContext(database_name="proj", data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Not connected"):
            ctx.get_stats()


# ---------------------------------------------------------------------------
# get_context / reset_context factory
# ---------------------------------------------------------------------------


class TestGetContextFactory:
    """Module-level singleton management."""

    def test_creates_and_connects_by_default(self) -> None:
        created = MagicMock(spec=CLIContext)
        created.database_name = "resolved"

        with (
            patch("chaoscypher_cli.context.get_database_name", return_value="resolved"),
            patch("chaoscypher_cli.context.CLIContext", return_value=created) as mock_cls,
        ):
            result = get_context(database_name="resolved")

        assert result is created
        mock_cls.assert_called_once_with(database_name="resolved", data_dir=None)
        created.connect.assert_called_once()

    def test_auto_connect_false_skips_connect(self) -> None:
        created = MagicMock(spec=CLIContext)
        created.database_name = "resolved"

        with (
            patch("chaoscypher_cli.context.get_database_name", return_value="resolved"),
            patch("chaoscypher_cli.context.CLIContext", return_value=created),
        ):
            get_context(database_name="resolved", auto_connect=False)

        created.connect.assert_not_called()

    def test_reuses_existing_context_for_same_database(self) -> None:
        first = MagicMock(spec=CLIContext)
        first.database_name = "same"

        with (
            patch("chaoscypher_cli.context.get_database_name", return_value="same"),
            patch("chaoscypher_cli.context.CLIContext", return_value=first) as mock_cls,
        ):
            a = get_context()
            b = get_context()

        assert a is b
        # Only one CLIContext is ever constructed.
        mock_cls.assert_called_once()

    def test_switching_database_disconnects_old_and_builds_new(self) -> None:
        old = MagicMock(spec=CLIContext)
        old.database_name = "old_db"
        new = MagicMock(spec=CLIContext)
        new.database_name = "new_db"

        # Seed the singleton with the "old" context.
        ctx_mod._context_instance = old

        with (
            patch("chaoscypher_cli.context.get_database_name", return_value="new_db"),
            patch("chaoscypher_cli.context.CLIContext", return_value=new),
        ):
            result = get_context(database_name="new_db")

        old.disconnect.assert_called_once()
        assert result is new

    def test_reset_context_disconnects_and_clears_singleton(self) -> None:
        existing = MagicMock(spec=CLIContext)
        ctx_mod._context_instance = existing

        reset_context()

        existing.disconnect.assert_called_once()
        assert ctx_mod._context_instance is None

    def test_reset_context_is_noop_when_no_singleton(self) -> None:
        ctx_mod._context_instance = None
        # Must not raise.
        reset_context()
        assert ctx_mod._context_instance is None
