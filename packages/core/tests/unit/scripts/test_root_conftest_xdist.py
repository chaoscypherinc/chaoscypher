# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The repo-root conftest sizes ``-n auto`` to the cgroup CPU quota.

A runner container capped at 4 CPUs saw every host core, started ~18 xdist workers and was
OOM-killed (2026-10-15). The hook tests go through the session's real hook chain, the one xdist
calls, so a root conftest that stops loading fails here too. pytest loads it only when the rootdir is
the repo root: every ``-n auto`` caller (ci.yml, the Makefile, run_internal_tool_tests.py) names
several packages or a path outside ``packages/``, so it is; a single-package run picks that
package's pyproject and skips here.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

import pytest


_ROOT_CONFTEST = Path(__file__).resolve().parents[5] / "conftest.py"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def root_conftest(request: pytest.FixtureRequest) -> ModuleType:
    """The root conftest module as this session registered it."""
    if request.config.rootpath != _ROOT_CONFTEST.parent:
        pytest.skip(
            f"rootdir is {request.config.rootpath}; the root conftest serves repo-root runs"
        )
    for plugin in request.config.pluginmanager.get_plugins():
        if (
            isinstance(plugin, ModuleType)
            and Path(plugin.__file__ or "").resolve() == _ROOT_CONFTEST
        ):
            return plugin
    pytest.fail(f"{_ROOT_CONFTEST} is not loaded in this session")


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"cpu.max": "400000 100000\n"}, 4),
        ({"cpu.max": "150000 100000\n"}, 1),
        ({"cpu.max": "50000 100000\n"}, 1),
        ({"cpu.max": "max 100000\n"}, None),
        ({"cpu/cpu.cfs_quota_us": "200000\n", "cpu/cpu.cfs_period_us": "100000\n"}, 2),
        ({"cpu/cpu.cfs_quota_us": "-1\n", "cpu/cpu.cfs_period_us": "100000\n"}, None),
        ({"cpu.max": "garbage\n"}, None),
        ({}, None),
    ],
)
def test_cgroup_cpu_limit(
    root_conftest: ModuleType, tmp_path: Path, files: dict[str, str], expected: int | None
) -> None:
    """Inside a container: the process's cgroup is the namespace root."""
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    _write(tmp_path, "self", "0::/\n")
    assert root_conftest.cgroup_cpu_limit(tmp_path, tmp_path / "self") == expected


def test_v2_takes_the_tightest_of_the_process_cgroup_and_its_parents(
    root_conftest: ModuleType, tmp_path: Path
) -> None:
    """Outside a container (systemd-run, a slice): the root's cpu.max is not this process's."""
    cg = tmp_path / "cg"
    _write(cg, "user.slice/cpu.max", "300000 100000\n")
    _write(cg, "user.slice/run.scope/cpu.max", "max 100000\n")
    _write(tmp_path, "self", "0::/user.slice/run.scope\n")
    assert root_conftest.cgroup_cpu_limit(cg, tmp_path / "self") == 3
    _write(cg, "user.slice/run.scope/cpu.max", "200000 100000\n")
    assert root_conftest.cgroup_cpu_limit(cg, tmp_path / "self") == 2
    _write(tmp_path, "self", "0::/elsewhere\n")
    assert root_conftest.cgroup_cpu_limit(cg, tmp_path / "self") is None


def test_auto_follows_the_quota(
    root_conftest: ModuleType,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("xdist")
    _write(tmp_path, "cpu.max", "100000 100000\n")
    _write(tmp_path, "self", "0::/\n")
    monkeypatch.setattr(root_conftest, "_CGROUP", tmp_path)
    monkeypatch.setattr(root_conftest, "_SELF_CGROUP", tmp_path / "self")
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    assert request.config.hook.pytest_xdist_auto_num_workers(config=request.config) == 1


def test_no_quota_and_the_env_override_defer_to_xdist(
    root_conftest: ModuleType,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("xdist")
    _write(tmp_path, "self", "0::/\n")
    monkeypatch.setattr(root_conftest, "_CGROUP", tmp_path)
    monkeypatch.setattr(root_conftest, "_SELF_CGROUP", tmp_path / "self")
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    hook = request.config.hook.pytest_xdist_auto_num_workers
    assert hook(config=request.config) == len(os.sched_getaffinity(0))
    _write(tmp_path, "cpu.max", "100000 100000\n")
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "3")
    assert hook(config=request.config) == 3
