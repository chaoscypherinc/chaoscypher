# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``chaoscypher compose build`` end to end: real packages, the real merger, the CLI's logging.

Two promises are checked through the command itself rather than a patched service:

* a database failure in the merge never prints SQLAlchemy's SQL text or its link, in the
  command's output or in its logs (at the CLI's default level), and the output ends with
  the plain ``Build failed:`` line;
* a clean build whose output folder is also the data folder succeeds twice in one process
  (the stale cached engine of the deleted database used to fail the second one with
  ``no such table: sources``).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from click.testing import CliRunner

from chaoscypher_cli.commands.compose.build import build
from chaoscypher_cli.log_setup import configure_cli_logging, plain_database_errors
from chaoscypher_core.testing.ccx_packages import build_ccx_package
from chaoscypher_core.testing.structlog_fixtures import forget_cached_loggers


class _CurrentStdout:
    """A log stream that writes to whatever ``sys.stdout`` is now (CliRunner swaps it).

    The CLI's logs go to stdout by default; this keeps them in the command's captured
    output instead of the stdout the test process had when logging was configured.
    """

    def write(self, text: str) -> int:
        return sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()

    def isatty(self) -> bool:
        return False


def _real_missing_table_error(tmp_path: Path) -> Exception:
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import OperationalError

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT id FROM sources"))
    except OperationalError as exc:
        return exc
    finally:
        engine.dispose()
    raise AssertionError("the query against an empty database should have failed")


@pytest.fixture
def no_embedding() -> Iterator[None]:
    """Keep the post-merge indexing pass off the network and off the GPU."""
    with patch(
        "chaoscypher_core.operations.importing.imported_source_handler.index_imported_package",
        new=AsyncMock(return_value={"sources_indexed": 0, "nodes_indexed": 0}),
    ):
        yield


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Settings read again from the environment this test sets, and forgotten after."""
    from chaoscypher_core import app_config

    def _reset() -> None:
        app_config.get_config_manager.cache_clear()
        app_config.get_settings.cache_clear()
        monkeypatch.setattr(app_config, "_settings", None)

    _reset()
    yield
    _reset()


def _composition(tmp_path: Path, out: Path, packages: list[str]) -> Path:
    config = tmp_path / "axiomatize.yaml"
    lines = ["name: pack-a-system", "packages:"]
    lines += [f"  - {spec}" for spec in packages]
    lines += ["settings:", f"  output_dir: {out}"]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def _run_build(config: Path, *args: str) -> Any:
    with (
        patch("chaoscypher_cli.commands.compose.build.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.compose.build.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        return CliRunner().invoke(build, ["--config", str(config), *args])


def _last_line(output: str) -> str:
    return [line for line in output.splitlines() if line.strip()][-1]


# ---------------------------------------------------------------------------
# The CLI's logging: a database error's traceback becomes one plain line
# ---------------------------------------------------------------------------


def test_compose_build_database_failure_prints_no_sql_anywhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_embedding: None,
    fresh_settings: None,
) -> None:
    from chaoscypher_core.services.compose.merger import NamespaceMerger

    # The real CLI logging setup, at the CLI's default level, writing to stdout.
    monkeypatch.setattr(logging, "_chaoscypher_logging_configured", False, raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_cli_logging(stream=_CurrentStdout())  # type: ignore[arg-type]
    forget_cached_loggers()

    error = _real_missing_table_error(tmp_path)
    assert "[SQL:" in str(error)
    assert "sqlalche.me" in str(error)

    async def _fails(self: NamespaceMerger, engine: object, pkg: object) -> object:
        raise error

    monkeypatch.setattr(NamespaceMerger, "_merge_package", _fails)
    build_ccx_package(tmp_path, "pack-a", ("Alice", "Bob"))
    out = tmp_path / "out"
    config = _composition(tmp_path, out, ["./pack-a.ccx"])

    result = _run_build(config)

    assert result.exit_code == 1, result.output
    for stream in (result.output, result.stderr):
        assert "[SQL:" not in stream
        assert "sqlalche.me" not in stream
        assert "Traceback" not in stream
    # The logged event is still there, with the plain line in place of the traceback.
    assert "merger_package_failed" in result.output
    last = _last_line(result.output)
    # The package is named by its manifest, not its file name.
    assert last.startswith("Build failed: Failed to merge "), last
    assert f"the database at {out / 'databases' / 'default' / 'app.db'} has no" in last
    assert '"sources" table while importing package ' in last


def test_debug_level_keeps_the_whole_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging, "_chaoscypher_logging_configured", False, raising=False)

    configure_cli_logging(log_level="DEBUG", stream=_CurrentStdout())  # type: ignore[arg-type]

    assert plain_database_errors not in structlog.get_config()["processors"]


def test_default_level_installs_the_processor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logging, "_chaoscypher_logging_configured", False, raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    configure_cli_logging(stream=_CurrentStdout())  # type: ignore[arg-type]

    assert plain_database_errors in structlog.get_config()["processors"]


def test_processor_replaces_only_a_database_error(tmp_path: Path) -> None:
    error = _real_missing_table_error(tmp_path)

    database = plain_database_errors(None, "error", {"event": "x_failed", "exc_info": error})
    assert database == {
        "event": "x_failed",
        "error": 'the database has no "sources" table '
        "(it may not be set up, or was replaced while open)",
    }

    other = ValueError("manifest is missing")
    kept = plain_database_errors(None, "error", {"event": "y_failed", "exc_info": other})
    assert kept == {"event": "y_failed", "exc_info": other}

    try:
        raise error
    except Exception:
        inside = plain_database_errors(None, "error", {"event": "z_failed", "exc_info": True})
    assert "exc_info" not in inside
    assert inside["error"].startswith('the database has no "sources" table')

    raw = plain_database_errors(
        None, "error", {"event": "w_failed", "exc_info": error, "error": str(error)}
    )
    assert "[SQL:" not in raw["error"]


def _render(monkeypatch: pytest.MonkeyPatch, level: str, log: Any) -> str:
    """Run the real CLI logging setup at ``level`` into a buffer, call ``log``, return the text."""
    import io

    monkeypatch.setattr(logging, "_chaoscypher_logging_configured", False, raising=False)
    buf = io.StringIO()
    configure_cli_logging(log_level=level, stream=buf)
    forget_cached_loggers()
    log(structlog.get_logger("pack_a_test"))
    return buf.getvalue()


@pytest.mark.parametrize(
    "how",
    ["error_message_with_exc_info", "error_without_exc_info"],
)
def test_raw_sqlalchemy_text_in_any_field_is_scrubbed_below_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """Core logs ``error_message=str(e)`` / ``error=str(e)`` in hundreds of places."""
    error = _real_missing_table_error(tmp_path)

    def _log(logger: Any) -> None:
        if how == "error_message_with_exc_info":
            try:
                raise error
            except Exception:
                logger.exception("pack_a_failed", error_message=str(error))
        else:
            logger.warning("pack_a_skipped", error=str(error))

    rendered = _render(monkeypatch, "WARNING", _log)

    assert "pack_a_" in rendered
    assert "no such table: sources" in rendered
    assert "[SQL:" not in rendered
    assert "sqlalche.me" not in rendered


@pytest.mark.parametrize(
    "how",
    ["error_message_with_exc_info", "error_without_exc_info"],
)
def test_debug_keeps_the_raw_sqlalchemy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    error = _real_missing_table_error(tmp_path)

    def _log(logger: Any) -> None:
        if how == "error_message_with_exc_info":
            try:
                raise error
            except Exception:
                logger.exception("pack_a_failed", error_message=str(error))
        else:
            logger.warning("pack_a_skipped", error=str(error))

    rendered = _render(monkeypatch, "DEBUG", _log)

    assert "[SQL:" in rendered
    assert "sqlalche.me" in rendered


# ---------------------------------------------------------------------------
# A clean build into the data folder, twice in one process
# ---------------------------------------------------------------------------


def test_clean_build_into_the_data_folder_succeeds_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_embedding: None,
    fresh_settings: None,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(out))
    from chaoscypher_core import app_config

    app_config.get_settings.cache_clear()
    build_ccx_package(tmp_path, "pack-a", ("Alice", "Bob"))
    config = _composition(tmp_path, out, ["./pack-a.ccx"])

    first = _run_build(config)
    assert first.exit_code == 0, first.output

    second = _run_build(config)
    assert second.exit_code == 0, second.output
    assert "Built composition: pack-a-system" in second.output
    assert (out / "databases" / "default" / "app.db").exists()
