# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The repo-root conftest sizes ``-n auto`` to the cgroup CPU quota and shards the suite.

A runner container capped at 4 CPUs saw every host core, started ~18 xdist workers and was
OOM-killed (2026-10-15). The hook tests go through the session's real hook chain, the one xdist
calls, so a root conftest that stops loading fails here too. pytest loads it only when the rootdir is
the repo root: every ``-n auto`` caller (ci.yml, the Makefile, run_internal_tool_tests.py) names
several packages or a path outside ``packages/``, so it is; a single-package run picks that
package's pyproject and skips here.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

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


@pytest.mark.parametrize(("spec", "expected"), [("1/3", (1, 3)), ("3/3", (3, 3)), ("1/1", (1, 1))])
def test_parse_shard(root_conftest: ModuleType, spec: str, expected: tuple[int, int]) -> None:
    assert root_conftest.parse_shard(spec) == expected


@pytest.mark.parametrize("spec", ["0/3", "4/3", "3", "a/b", "1/2/3", ""])
def test_a_bad_shard_is_a_usage_error(root_conftest: ModuleType, spec: str) -> None:
    with pytest.raises(pytest.UsageError, match="CI_TEST_SHARD"):
        root_conftest.parse_shard(spec)


def _nodeids() -> list[str]:
    sizes = [40, 25, 25, 12, 9, 8, 5, 3, 2, 1, 1, 1]
    return [
        f"pkg/tests/test_{f:02d}.py::test_{i}" for f, size in enumerate(sizes) for i in range(size)
    ]


def _shards_of(owner: dict[str, int], path: str) -> set[int]:
    return {shard for nodeid, shard in owner.items() if nodeid.split("::")[0] == path}


def test_every_test_lands_in_exactly_one_shard_and_the_shards_balance(
    root_conftest: ModuleType,
) -> None:
    nodeids = _nodeids()
    owner = root_conftest.assign_shards(nodeids, 3)
    assert set(owner) == set(nodeids)
    assert set(owner.values()) == {1, 2, 3}
    load = [sum(1 for n in nodeids if owner[n] == k) for k in (1, 2, 3)]
    assert max(load) - min(load) <= 21  # no more than one piece of a split file apart
    assert root_conftest.assign_shards(list(reversed(nodeids)), 3) == owner  # order-independent
    files = {n.split("::")[0] for n in nodeids}
    whole = [f for f in files if sum(n.startswith(f + "::") for n in nodeids) <= 20]
    assert all(len(_shards_of(owner, f)) == 1 for f in whole)  # small files stay whole


def test_a_file_heavier_than_half_a_share_goes_in_runs_of_consecutive_tests(
    root_conftest: ModuleType,
) -> None:
    """2026-10-16: test_pins.py alone held a 37-minute shard while the rest took 4 to 21."""
    pins = [f"pkg/tests/test_pins.py::test_{i:03d}" for i in range(98)]
    rest = [f"pkg/tests/test_{f:02d}.py::t{i}" for f in range(30) for i in range(20)]
    seconds = {"pkg/tests/test_pins.py": 1572.0} | {
        f"pkg/tests/test_{f:02d}.py": 180.0 for f in range(30)
    }
    owner = root_conftest.assign_shards(pins + rest, 6, seconds)
    assert set(owner) == set(pins + rest)
    pieces = [owner[n] for n in pins]
    assert len(set(pieces)) == 3  # 1572 s against a cap of 7,000 / 12 s: three runs
    assert pieces == sorted(pieces, key=pieces.index)  # each run is consecutive tests
    rate = {n: (1572.0 / 98 if n in pins else 180.0 / 20) for n in owner}
    load = [sum(rate[n] for n, k in owner.items() if k == shard) for shard in range(1, 7)]
    assert max(load) <= sum(load) / 6 + 1572.0 / 3  # within one piece of a fair share
    whole = {f"pkg/tests/test_{f:02d}.py" for f in range(30)}
    assert all(len(_shards_of(owner, f)) == 1 for f in whole)


def test_the_hook_keeps_one_shard_and_reports_the_rest_deselected(
    root_conftest: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    deselected: list[Any] = []
    config = SimpleNamespace(
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items))
    )
    nodeids = _nodeids()
    kept: list[str] = []
    for k in (1, 2, 3):
        items = [SimpleNamespace(nodeid=n) for n in nodeids]
        monkeypatch.setenv("CI_TEST_SHARD", f"{k}/3")
        root_conftest.pytest_collection_modifyitems(config, items)
        kept += [item.nodeid for item in items]
    assert sorted(kept) == sorted(nodeids)  # the shards cover the suite once
    assert len(deselected) == 2 * len(nodeids)
    monkeypatch.delenv("CI_TEST_SHARD")
    items = [SimpleNamespace(nodeid=n) for n in nodeids]
    root_conftest.pytest_collection_modifyitems(config, items)
    assert len(items) == len(nodeids)  # unset: the whole suite


def test_recorded_seconds_pack_the_shards_not_test_counts(root_conftest: ModuleType) -> None:
    """2026-10-16: packed by count, one of six shards ran 35 minutes and two ran 5."""
    nodeids = _nodeids()
    files = sorted({n.split("::")[0] for n in nodeids})
    # The two smallest files are the expensive ones (a recipe test file's shape).
    seconds = dict.fromkeys(files, 1.0) | {files[-1]: 300.0, files[-2]: 290.0}
    per_test = {
        n: seconds[f] / sum(m.startswith(f + "::") for m in nodeids)
        for f in files
        for n in nodeids
        if n.startswith(f + "::")
    }

    def heaviest(owner: dict[str, int]) -> float:
        return max(sum(per_test[n] for n, k in owner.items() if k == s) for s in (1, 2, 3))

    owner = root_conftest.assign_shards(nodeids, 3, seconds)
    assert _shards_of(owner, files[-1]) != _shards_of(owner, files[-2])
    assert heaviest(owner) <= 300.0 + 2.0  # the heaviest file, plus a little
    assert heaviest(root_conftest.assign_shards(nodeids, 3)) >= heaviest(owner)


def test_a_file_with_no_record_is_weighed_from_its_test_count(root_conftest: ModuleType) -> None:
    from collections import Counter

    counts = Counter({"a.py": 10, "b.py": 10, "new.py": 20})
    # a and b record 1 s a test, so new.py (unrecorded, 20 tests) weighs 20 s: as much as both.
    weights = root_conftest._file_weights(counts, {"a.py": 10.0, "b.py": 10.0})
    assert weights == {"a.py": 10.0, "b.py": 10.0, "new.py": 20.0}
    assert root_conftest._file_weights(counts, {}) == {"a.py": 10.0, "b.py": 10.0, "new.py": 20.0}
    assert root_conftest._file_weights(counts, None) == root_conftest._file_weights(counts, {})


def test_load_durations_merges_a_directory_and_ignores_what_it_cannot_read(
    root_conftest: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "shard-1.json").write_text(json.dumps({"a.py": 3.0, "b.py": 1.0}))
    (tmp_path / "shard-2.json").write_text(json.dumps({"b.py": 2.5, "c.py": "slow", "d.py": -1}))
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "list.json").write_text("[1, 2]")
    assert root_conftest.load_durations(tmp_path) == {"a.py": 3.0, "b.py": 2.5}
    assert root_conftest.load_durations(tmp_path / "shard-1.json") == {"a.py": 3.0, "b.py": 1.0}
    assert root_conftest.load_durations(tmp_path / "missing.json") == {}


def test_the_hook_packs_by_the_record_it_is_given_and_still_covers_the_suite_once(
    root_conftest: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nodeids = _nodeids()
    files = sorted({n.split("::")[0] for n in nodeids})
    # Every shard records its own files, so the record covers the suite (here, split in two).
    seconds = dict.fromkeys(files, 1.0) | {files[-1]: 300.0, files[-2]: 290.0}
    half = len(files) // 2
    (tmp_path / "shard-1.json").write_text(json.dumps(dict(list(seconds.items())[:half])))
    (tmp_path / "shard-2.json").write_text(json.dumps(dict(list(seconds.items())[half:])))
    monkeypatch.setenv("CI_TEST_DURATIONS", str(tmp_path))
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda items: None))
    kept: dict[int, list[str]] = {}
    for k in (1, 2, 3):
        items = [SimpleNamespace(nodeid=n) for n in nodeids]
        monkeypatch.setenv("CI_TEST_SHARD", f"{k}/3")
        root_conftest.pytest_collection_modifyitems(config, items)
        kept[k] = [item.nodeid for item in items]
    assert sorted(n for shard in kept.values() for n in shard) == sorted(nodeids)
    heavy = [k for k in kept if any(n.startswith(f"{files[-1]}::") for n in kept[k])]
    assert len(heavy) == 1 and not any(n.startswith(f"{files[-2]}::") for n in kept[heavy[0]])


def test_the_run_records_per_file_seconds_on_the_controller_only(
    root_conftest: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(root_conftest, "_seconds", defaultdict(float))
    out = tmp_path / "durations" / "shard-2.json"
    monkeypatch.setenv("CI_TEST_DURATIONS_OUT", str(out))
    for nodeid, duration in [
        ("pkg/tests/test_a.py::t1", 0.5),
        ("pkg/tests/test_a.py::t1", 1.25),  # setup and call of one test
        ("pkg/tests/test_b.py::C::t2[x]", 2.0),
    ]:
        root_conftest.pytest_runtest_logreport(SimpleNamespace(nodeid=nodeid, duration=duration))
    root_conftest.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace(workerinput={})))
    assert not out.exists(), "an xdist worker never writes; the controller has every report"
    root_conftest.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()))
    assert json.loads(out.read_text()) == {"pkg/tests/test_a.py": 1.75, "pkg/tests/test_b.py": 2.0}
    assert root_conftest.load_durations(out.parent) == {
        "pkg/tests/test_a.py": 1.75,
        "pkg/tests/test_b.py": 2.0,
    }


def test_parallel_runs_steal_work_unless_told_otherwise(
    root_conftest: ModuleType, request: pytest.FixtureRequest
) -> None:
    """The real session: a run with ``-n`` (CI, ``make test``) schedules with worksteal."""
    for numprocesses, dist, expected in [
        (4, "no", "worksteal"),
        (4, "load", "load"),
        (0, "no", "no"),
    ]:
        config = SimpleNamespace(option=SimpleNamespace(numprocesses=numprocesses, dist=dist))
        root_conftest.pytest_cmdline_main(config)
        assert config.option.dist == expected
    if getattr(request.config.option, "numprocesses", None):
        assert request.config.option.dist in (
            "worksteal",
            "each",
            "load",
            "loadscope",
            "loadfile",
            "loadgroup",
        )
        if "--dist" not in " ".join(request.config.invocation_params.args):
            assert request.config.option.dist == "worksteal"


def test_a_test_that_leaves_the_environment_changed_is_named_and_undone(
    root_conftest: ModuleType,
) -> None:
    """2026-10-16: a leaked CHAOSCYPHER_LLM_PROVIDER failed three tests in another shard."""
    added, edited, removed = (
        "CHAOSCYPHER_LEAK_ADDED",
        "CHAOSCYPHER_LEAK_EDITED",
        "CHAOSCYPHER_LEAK_GONE",
    )
    os.environ[edited] = "before"
    os.environ[removed] = "before"
    try:
        before = dict(os.environ)
        os.environ[added] = "leaked"
        os.environ[edited] = "changed"
        del os.environ[removed]
        os.environ["PYTEST_CURRENT_TEST"] = "another phase"  # pytest's own: exempt
        assert root_conftest.restore_environment(before) == sorted([added, edited, removed])
        assert added not in os.environ
        assert os.environ[edited] == os.environ[removed] == "before"
        assert root_conftest.restore_environment(dict(os.environ)) == []
    finally:
        for key in (added, edited, removed):
            os.environ.pop(key, None)
