# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every test leaves structlog and stdlib logging as it found them (``logging_left_as_found``).

On 2026-10-26 two tests that shared an xdist worker with ``test_schema_updates.py``'s drift test
hid its warning: one left structlog on the stdlib bridge with caching on (and the engine module's
logger cached against it), and one ran Alembic in-process, whose ``fileConfig`` disabled every
existing stdlib logger. Either alone was harmless; together the warning went to a disabled logger.
Which tests share a worker changes with the shard packing, so the fixture puts the state back
after every test instead of each test owning its own clean-up.
"""

from __future__ import annotations

import logging
import logging.config
import subprocess
import sys
import textwrap
from importlib import resources
from pathlib import Path

import pytest
import structlog

from chaoscypher_core.testing.structlog_fixtures import logging_left_as_found


_ALEMBIC_INI = Path(str(resources.files("chaoscypher_core.database.migrations") / "alembic.ini"))
_PACKAGES = Path(__file__).resolve().parents[4]


def _pollute_like_the_2026_10_26_pair() -> None:
    """The state the two polluters left: the stdlib bridge with caching, a cached module logger,
    a contextvar, and Alembic's ``fileConfig`` (root handlers replaced, existing loggers disabled).
    """
    from chaoscypher_core.adapters.sqlite import engine

    structlog.configure(
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    engine.logger.bind()  # caches the bridge-wired logger on the module's proxy
    structlog.contextvars.bind_contextvars(leaked="yes")
    logging.config.fileConfig(str(_ALEMBIC_INI))
    logging.disable(logging.CRITICAL)


def test_a_polluted_logging_state_is_put_back(capsys: pytest.CaptureFixture[str]) -> None:
    """Driven by hand: after teardown a module logger's warning reaches stdout again."""
    from chaoscypher_core.adapters.sqlite import engine

    root = logging.getLogger()
    before = (structlog.get_config(), root.level, structlog.contextvars.get_contextvars())
    gen = logging_left_as_found.__wrapped__()  # type: ignore[attr-defined]  # pytest.fixture sets __wrapped__ at runtime
    next(gen)
    _pollute_like_the_2026_10_26_pair()
    assert logging.getLogger(engine.__name__).disabled, "precondition: fileConfig disabled it"
    with pytest.raises(StopIteration):
        next(gen)

    assert (structlog.get_config(), root.level, structlog.contextvars.get_contextvars()) == before
    assert "bind" not in engine.logger.__dict__, "the cached logger survived"
    assert not logging.getLogger(engine.__name__).disabled
    assert logging.root.manager.disable == logging.NOTSET
    assert not [h for h in root.handlers if type(h).__module__ == "logging"], root.handlers
    capsys.readouterr()
    engine.logger.warning("schema_constraint_drift_probe")
    assert "schema_constraint_drift_probe" in capsys.readouterr().out


def test_a_warning_after_a_polluting_test_is_still_captured(tmp_path: Path) -> None:
    """Through pytest's own phases, in order in one process: the polluter, then the drift test."""
    (tmp_path / "conftest.py").write_text(
        "from chaoscypher_core.testing import logging_left_as_found  # noqa: F401\n"
    )
    (tmp_path / "test_order.py").write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(Path(__file__).parent)!r})
            from test_logging_left_as_found import _pollute_like_the_2026_10_26_pair


            def test_1_leaves_logging_changed():
                _pollute_like_the_2026_10_26_pair()


            def test_2_the_drift_warning_is_seen(tmp_path, capsys):
                from sqlalchemy import MetaData, create_engine
                from sqlmodel import Field, SQLModel

                from chaoscypher_core.adapters.sqlite.engine import log_schema_constraint_drift

                db = create_engine(f"sqlite:///{{tmp_path / 'drift.db'}}")
                with db.begin() as conn:
                    conn.exec_driver_sql("CREATE TABLE parents (id TEXT PRIMARY KEY)")
                    conn.exec_driver_sql("CREATE TABLE children (id TEXT PRIMARY KEY, parent_id TEXT)")
                SQLModel.metadata = MetaData()  # its own process: nothing else shares the registry

                class Parent(SQLModel, table=True):
                    __tablename__ = "parents"
                    id: str = Field(primary_key=True)

                class Child(SQLModel, table=True):
                    __tablename__ = "children"
                    id: str = Field(primary_key=True)
                    parent_id: str = Field(foreign_key="parents.id")

                log_schema_constraint_drift(db)
                assert "schema_constraint_drift" in capsys.readouterr().out
            """
        )
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    result = subprocess.run(  # noqa: S603 - this interpreter, a test file written above
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-q",
            f"--basetemp={tmp_path / 'inner'}",
            str(tmp_path / "test_order.py"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout


@pytest.mark.parametrize("package", ["core", "cortex", "cli", "neuron"])
def test_every_package_conftest_puts_logging_back(package: str) -> None:
    """A package whose conftest leaves the fixture out runs its tests on whatever state leaked."""
    conftest = (_PACKAGES / package / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "logging_left_as_found" in conftest, f"packages/{package}/tests/conftest.py"
