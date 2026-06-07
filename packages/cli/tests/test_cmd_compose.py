# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the compose command group (build / up / down / run).

All four subcommands shell out to ComposeService (an async helper in
chaoscypher_core). Every test mocks ComposeConfig.from_yaml and ComposeService
so no real async subprocess or network I/O occurs.

Coverage targets (per module):
  commands/compose/build.py   ≥ 85 %
  commands/compose/up.py      ≥ 85 %
  commands/compose/run.py     ≥ 85 %
  commands/compose/down.py    ≥ 85 %
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.compose.build import build
from chaoscypher_cli.commands.compose.down import down
from chaoscypher_cli.commands.compose.run import run
from chaoscypher_cli.commands.compose.up import up


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_compose_config(
    name: str = "test-composition",
    package_count: int = 2,
    port: int = 8000,
) -> MagicMock:
    """Return a minimal ComposeConfig mock."""
    cfg = MagicMock()
    cfg.name = name
    cfg.packages = [MagicMock() for _ in range(package_count)]
    cfg.package_specs = cfg.packages
    cfg.settings = MagicMock()
    cfg.settings.merge_strategy = MagicMock()
    cfg.settings.merge_strategy.value = "namespace"
    cfg.settings.port = port
    cfg.resolved_output_dir = Path("/fake/output")
    return cfg


def _make_success_result(
    packages: list[str] | None = None,
    entities: int = 10,
    relationships: int = 5,
    output_dir: Path | None = None,
) -> MagicMock:
    """Return a successful CompositionResult mock."""
    result = MagicMock()
    result.success = True
    result.packages_included = packages or ["pkg-a", "pkg-b"]
    result.total_entities = entities
    result.total_relationships = relationships
    result.output_dir = output_dir or Path("/fake/output")
    result.errors = []
    result.warnings = []
    return result


def _make_failure_result(errors: list[str] | None = None) -> MagicMock:
    """Return a failed CompositionResult mock."""
    result = MagicMock()
    result.success = False
    result.errors = errors or ["Something went wrong"]
    result.packages_included = []
    return result


def _make_compose_error(message: str = "compose failed", details: dict | None = None) -> Any:
    """Return a ComposeError-like exception."""
    from chaoscypher_core.services.compose import ComposeError

    return ComposeError(message=message, stage="test", details=details)


# ---------------------------------------------------------------------------
# Shared patch targets
# ---------------------------------------------------------------------------

_BUILD_CONFIG = "chaoscypher_cli.commands.compose.build.ComposeConfig"
_BUILD_SERVICE = "chaoscypher_cli.commands.compose.build.ComposeService"
_BUILD_AUTH = "chaoscypher_cli.commands.compose.build.get_auth_config"
_BUILD_LEXICON = "chaoscypher_cli.commands.compose.build.get_lexicon_url"

_UP_CONFIG = "chaoscypher_cli.commands.compose.up.ComposeConfig"
_UP_SERVICE = "chaoscypher_cli.commands.compose.up.ComposeService"
_UP_AUTH = "chaoscypher_cli.commands.compose.up.get_auth_config"
_UP_LEXICON = "chaoscypher_cli.commands.compose.up.get_lexicon_url"

_DOWN_CONFIG = "chaoscypher_cli.commands.compose.down.ComposeConfig"
_DOWN_SERVICE = "chaoscypher_cli.commands.compose.down.ComposeService"

_RUN_CONFIG = "chaoscypher_cli.commands.compose.run.ComposeConfig"
_RUN_SERVICE = "chaoscypher_cli.commands.compose.run.ComposeService"


def _make_async(return_value: Any) -> AsyncMock:
    """Return an AsyncMock that resolves to return_value."""
    return AsyncMock(return_value=return_value)


# ---------------------------------------------------------------------------
# build command
# ---------------------------------------------------------------------------


class TestBuildHappyPath:
    """compose build succeeds — asserts output and service interactions."""

    def test_happy_path_exits_0(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 0, result.output

    def test_happy_path_calls_service_build(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance) as mock_svc_cls,
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(build, ["--config", str(config_file)])
            mock_svc_cls.assert_called_once()
            mock_service_instance.build.assert_called_once_with(compose_cfg, clean=False)

    def test_happy_path_output_mentions_composition_name(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config(name="my-test-composition")
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert "my-test-composition" in result.output

    def test_clean_flag_forwarded_to_service(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(build, ["--config", str(config_file), "--clean"])
            mock_service_instance.build.assert_called_once_with(compose_cfg, clean=True)

    def test_with_auth_creates_service_with_credentials(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_auth = MagicMock()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance) as mock_svc_cls,
            patch(_BUILD_AUTH, return_value=mock_auth),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(build, ["--config", str(config_file)])
            mock_svc_cls.assert_called_once_with(
                auth=mock_auth, lexicon_url="https://lexicon.example.com"
            )

    def test_warnings_printed_when_present(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_result.warnings = ["deprecated package version", "slow merge detected"]
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert "deprecated package version" in result.output
        assert result.exit_code == 0


class TestBuildFailurePath:
    """compose build failure paths — result.success=False or ComposeError."""

    def test_service_failure_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_failure_result(errors=["Package not found in hub"])
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 1

    def test_service_failure_prints_errors(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_failure_result(errors=["Package not found in hub"])
        mock_service_instance = MagicMock()
        mock_service_instance.build = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert "Package not found in hub" in result.output

    def test_compose_error_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error("network timeout during resolve")
        mock_service_instance = MagicMock()
        mock_service_instance.build = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "network timeout during resolve" in result.output

    def test_compose_error_with_details_printed(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error(
            "resolve error", details={"package": "my-pkg", "hint": "check name"}
        )
        mock_service_instance = MagicMock()
        mock_service_instance.build = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_SERVICE, return_value=mock_service_instance),
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 1

    def test_config_load_exception_exits_1(self, tmp_path: Path) -> None:
        """ComposeConfig.from_yaml raising a non-FileNotFoundError exits 1."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("broken: yaml: !!!\n")

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.side_effect = ValueError("invalid schema")
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "Failed to load config" in result.output

    def test_internal_file_not_found_exits_1(self, tmp_path: Path) -> None:
        """ComposeConfig.from_yaml raises FileNotFoundError with existing file → exits 1."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")  # file exists so Click passes

        runner = CliRunner()
        with (
            patch(_BUILD_CONFIG) as mock_cfg_cls,
            patch(_BUILD_AUTH, return_value=None),
            patch(_BUILD_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.side_effect = FileNotFoundError("referenced resource missing")
            result = runner.invoke(build, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_config_file_not_found_exits_2(self, tmp_path: Path) -> None:
        """Missing config file: click's exists=True gate fires first → exit 2."""
        runner = CliRunner()
        result = runner.invoke(build, ["--config", str(tmp_path / "missing.yaml")])
        # Click raises UsageError when exists=True and file is absent → exit 2
        assert result.exit_code == 2


class TestBuildCommandMeta:
    """Command registration / --help."""

    def test_build_cmd_name(self) -> None:
        assert build.name == "build"

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(build, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_clean(self) -> None:
        runner = CliRunner()
        result = runner.invoke(build, ["--help"])
        assert "clean" in result.output.lower()

    def test_help_mentions_config(self) -> None:
        runner = CliRunner()
        result = runner.invoke(build, ["--help"])
        assert "config" in result.output.lower()


# ---------------------------------------------------------------------------
# up command
# ---------------------------------------------------------------------------


class TestUpHappyPath:
    """compose up — foreground and detached modes."""

    def test_happy_path_exits_0(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 0, result.output

    def test_detach_flag_forwarded(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(up, ["--config", str(config_file), "--detach"])
            mock_service_instance.up.assert_called_once_with(
                compose_cfg, rebuild=False, detach=True
            )

    def test_build_flag_forwarded(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(up, ["--config", str(config_file), "--build"])
            mock_service_instance.up.assert_called_once_with(
                compose_cfg, rebuild=True, detach=False
            )

    def test_port_override_applied(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config(port=8000)
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(up, ["--config", str(config_file), "--port", "9999"])

        # The settings.port should be updated by the command
        assert compose_cfg.settings.port == 9999

    def test_detach_success_message(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config(port=8500)
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file), "--detach"])

        assert "background" in result.output.lower() or "started" in result.output.lower()

    def test_foreground_stopped_message(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert "stopped" in result.output.lower() or "composition" in result.output.lower()

    def test_no_auth_note_printed(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_success_result()
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert "not logged in" in result.output.lower() or "login" in result.output.lower()


class TestUpFailurePath:
    """compose up failure paths."""

    def test_service_failure_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_result = _make_failure_result(errors=["server port in use"])
        mock_service_instance = MagicMock()
        mock_service_instance.up = _make_async(mock_result)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "server port in use" in result.output

    def test_compose_error_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error("failed to start: disk full")
        mock_service_instance = MagicMock()
        mock_service_instance.up = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "failed to start: disk full" in result.output

    def test_compose_error_with_details(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error("start error", details={"port": "8000", "hint": "check firewall"})
        mock_service_instance = MagicMock()
        mock_service_instance.up = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 1

    def test_config_load_exception_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("broken: yaml\n")

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.side_effect = RuntimeError("yaml parse error")
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 1

    def test_keyboard_interrupt_handled(self, tmp_path: Path) -> None:
        """KeyboardInterrupt during foreground up prints shutdown message."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.up = AsyncMock(side_effect=KeyboardInterrupt())

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_SERVICE, return_value=mock_service_instance),
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(up, ["--config", str(config_file)])

        assert "shutting down" in result.output.lower()

    def test_internal_file_not_found_exits_1(self, tmp_path: Path) -> None:
        """ComposeConfig.from_yaml raises FileNotFoundError with existing file → exits 1."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")  # file exists so Click passes

        runner = CliRunner()
        with (
            patch(_UP_CONFIG) as mock_cfg_cls,
            patch(_UP_AUTH, return_value=None),
            patch(_UP_LEXICON, return_value="https://lexicon.example.com"),
        ):
            mock_cfg_cls.from_yaml.side_effect = FileNotFoundError("referenced file missing")
            result = runner.invoke(up, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_missing_config_file_exits_2(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(up, ["--config", str(tmp_path / "missing.yaml")])
        assert result.exit_code == 2


class TestUpCommandMeta:
    """Command registration / --help."""

    def test_up_cmd_name(self) -> None:
        assert up.name == "up"

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(up, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_detach(self) -> None:
        runner = CliRunner()
        result = runner.invoke(up, ["--help"])
        assert "detach" in result.output.lower()

    def test_help_mentions_port(self) -> None:
        runner = CliRunner()
        result = runner.invoke(up, ["--help"])
        assert "port" in result.output.lower()

    def test_help_mentions_build(self) -> None:
        runner = CliRunner()
        result = runner.invoke(up, ["--help"])
        assert "build" in result.output.lower()


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------


class TestRunHappyPath:
    """compose run — runs a command in composition context."""

    def test_happy_path_exits_service_exit_code(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.run = _make_async(0)  # exit code 0

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
            patch(_RUN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(run, ["--config", str(config_file), "python", "script.py"])

        assert result.exit_code == 0, result.output

    def test_nonzero_service_exit_code_propagated(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.run = _make_async(42)  # non-zero exit

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
            patch(_RUN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(run, ["--config", str(config_file), "pytest", "tests/"])

        assert result.exit_code == 42

    def test_command_argv_forwarded_to_service(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.run = _make_async(0)

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
            patch(_RUN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            # Use '--' to prevent Click from interpreting flags like -m as options
            runner.invoke(
                run,
                ["--config", str(config_file), "--", "python", "script.py"],
            )
            mock_service_instance.run.assert_called_once_with(compose_cfg, ["python", "script.py"])

    def test_output_mentions_command(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config(name="my-comp")
        mock_service_instance = MagicMock()
        mock_service_instance.run = _make_async(0)

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
            patch(_RUN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(run, ["--config", str(config_file), "pytest", "tests/"])

        assert "pytest" in result.output or "my-comp" in result.output


class TestRunFailurePath:
    """compose run failure paths."""

    def test_compose_error_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error("env setup failed")
        mock_service_instance = MagicMock()
        mock_service_instance.run = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
            patch(_RUN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(run, ["--config", str(config_file), "python", "script.py"])

        assert result.exit_code == 1
        assert "env setup failed" in result.output

    def test_config_load_exception_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("bad: yaml\n")

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
        ):
            mock_cfg_cls.from_yaml.side_effect = ValueError("bad schema")
            result = runner.invoke(run, ["--config", str(config_file), "python", "x.py"])

        assert result.exit_code == 1

    def test_missing_config_file_exits_2(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(run, ["--config", str(tmp_path / "missing.yaml"), "python", "x.py"])
        assert result.exit_code == 2

    def test_config_file_not_found_error_exits_1(self, tmp_path: Path) -> None:
        """ComposeConfig.from_yaml raises FileNotFoundError → exits 1 with message."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        runner = CliRunner()
        with (
            patch(_RUN_CONFIG) as mock_cfg_cls,
        ):
            mock_cfg_cls.from_yaml.side_effect = FileNotFoundError("yaml gone")
            result = runner.invoke(run, ["--config", str(config_file), "python", "x.py"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRunCommandMeta:
    """Command registration / --help."""

    def test_run_cmd_name(self) -> None:
        assert run.name == "run"

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(run, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_config(self) -> None:
        runner = CliRunner()
        result = runner.invoke(run, ["--help"])
        assert "config" in result.output.lower()


# ---------------------------------------------------------------------------
# down command
# ---------------------------------------------------------------------------


class TestDownHappyPath:
    """compose down — stops composition."""

    def test_happy_path_exits_0(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.down = _make_async(None)

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
            patch(_DOWN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(down, ["--config", str(config_file)])

        assert result.exit_code == 0, result.output

    def test_service_down_called_with_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        mock_service_instance = MagicMock()
        mock_service_instance.down = _make_async(None)

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
            patch(_DOWN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            runner.invoke(down, ["--config", str(config_file)])
            mock_service_instance.down.assert_called_once_with(compose_cfg)

    def test_success_message_printed(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config(name="test-comp")
        mock_service_instance = MagicMock()
        mock_service_instance.down = _make_async(None)

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
            patch(_DOWN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(down, ["--config", str(config_file)])

        assert "stopped" in result.output.lower() or "test-comp" in result.output.lower()


class TestDownFailurePath:
    """compose down failure paths."""

    def test_compose_error_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        compose_cfg = _make_compose_config()
        err = _make_compose_error("server not running")
        mock_service_instance = MagicMock()
        mock_service_instance.down = AsyncMock(side_effect=err)

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
            patch(_DOWN_SERVICE, return_value=mock_service_instance),
        ):
            mock_cfg_cls.from_yaml.return_value = compose_cfg
            result = runner.invoke(down, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "server not running" in result.output

    def test_config_load_exception_exits_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("bad: yaml\n")

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
        ):
            mock_cfg_cls.from_yaml.side_effect = RuntimeError("corrupt yaml")
            result = runner.invoke(down, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "Failed to load config" in result.output

    def test_config_file_not_found_error_exits_1(self, tmp_path: Path) -> None:
        """ComposeConfig.from_yaml raises FileNotFoundError → exits 1."""
        config_file = tmp_path / "axiomatize.yaml"
        config_file.write_text("name: test\n")

        runner = CliRunner()
        with (
            patch(_DOWN_CONFIG) as mock_cfg_cls,
        ):
            mock_cfg_cls.from_yaml.side_effect = FileNotFoundError("yaml gone")
            result = runner.invoke(down, ["--config", str(config_file)])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_missing_config_file_exits_2(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(down, ["--config", "totally_absent.yaml"])
        assert result.exit_code == 2


class TestDownCommandMeta:
    """Command registration / --help."""

    def test_down_cmd_name(self) -> None:
        assert down.name == "down"

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(down, ["--help"])
        assert result.exit_code == 0

    def test_help_mentions_config(self) -> None:
        runner = CliRunner()
        result = runner.invoke(down, ["--help"])
        assert "config" in result.output.lower()
