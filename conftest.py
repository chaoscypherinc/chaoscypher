# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Workspace-wide pytest hooks, loaded for every run started from the repo root.

``-n auto`` sizes the xdist pool from ``sched_getaffinity`` (psutil is not installed), and a
container capped with ``docker run --cpus=4`` still sees every host core. On the home CI runners
(4 CPUs, 16 GB each) that started ~18 workers, each importing torch, and the kernel killed the job
(exit 137) on 2026-10-15. The pool now follows the cgroup's CPU quota when one is set.
``PYTEST_XDIST_AUTO_NUM_WORKERS`` still overrides it.

Parallel runs schedule with ``--dist worksteal`` unless another ``--dist`` is given. xdist's
default, ``load``, hands each worker a large first batch in collection order, so a file of slow
tests queued up behind one worker while the rest sat idle: a 4-worker shard with 2,520 worker-seconds
of tests ran 34 minutes (31% busy) on 2026-10-16. ``worksteal`` lets an idle worker take queued
tests from a busy one.

``CI_TEST_SHARD=K/N`` keeps the K-th of N shards of the collected tests: whole files, heaviest
first onto the lightest shard, so a module's fixtures stay in one shard; a file heavier than half a
shard's share goes in runs of consecutive tests. CI runs the suite as N
jobs (about 27 minutes on one 4-CPU runner, past its timeout) and combines their coverage after.

A file's weight is its recorded duration when ``CI_TEST_DURATIONS`` names a JSON file, or a
directory of them, mapping test files to seconds; a file with no record is estimated from its test
count at the recorded files' average. Without records the weight is the test count. Packing by
count left one of six shards at 35 minutes and two at 5 on 2026-10-16, because the expensive tests
(the benchmark recipe tests, 20-40 s each) cluster in a few files. ``CI_TEST_DURATIONS_OUT`` makes
the run write its own per-file seconds (summed over every test phase) for the next run to read.

A test that leaves ``os.environ`` changed fails, naming the variables, and the environment is put
back. Which tests share an xdist worker changes with the shard packing and the scheduler, so a leak
turns into some other test's failure in some other run: on 2026-10-16 a benchmark helper left
``CHAOSCYPHER_LLM_PROVIDER=mystery`` behind and three provider-factory tests failed in another shard.
``monkeypatch.setenv`` is restored before this check runs.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import pytest


_CGROUP = Path("/sys/fs/cgroup")
_DURATIONS = "CI_TEST_DURATIONS"
_DURATIONS_OUT = "CI_TEST_DURATIONS_OUT"
# Set by pytest itself each phase, or once per process by an import as deliberate process-wide
# configuration (torch; Hugging Face; chaoscypher_core.adapters.embedding.local_provider's quiet
# progress bars): which test first imports them is an accident of the run, not a leak.
_PROCESS_ENVIRONMENT = frozenset(
    {
        "PYTEST_CURRENT_TEST",
        "HF_HUB_DISABLE_PROGRESS_BARS",
        "TOKENIZERS_PARALLELISM",
        "TQDM_DISABLE",
        "KMP_DUPLICATE_LIB_OK",
        "KMP_INIT_AT_FORK",
        "TORCHINDUCTOR_CACHE_DIR",
    }
)
_SELF_CGROUP = Path("/proc/self/cgroup")


def _v2_quota(cpu_max: Path) -> int | None:
    """Whole CPUs a cgroup v2 ``cpu.max`` allows ("<quota> <period>"), or None when unlimited."""
    try:
        quota, period = cpu_max.read_text().split()[:2]
        return None if quota == "max" else max(1, int(quota) // int(period))
    except (OSError, ValueError, ZeroDivisionError):
        return None


def cgroup_cpu_limit(root: Path = _CGROUP, self_cgroup: Path = _SELF_CGROUP) -> int | None:
    """Return the whole CPUs this process's cgroup may use, or None when it has no quota.

    cgroup v2: the tightest ``cpu.max`` of the process's own cgroup and its parents (inside a
    container the namespace makes that ``root`` itself). cgroup v1: the mounted ``cpu`` quota.
    """
    try:
        rel = next(line[3:] for line in self_cgroup.read_text().splitlines() if line[:3] == "0::")
    except (OSError, StopIteration):
        rel = "/"
    here = root / rel.strip().lstrip("/")
    limits = [
        quota
        for d in (here, *here.parents)
        if d.is_relative_to(root) and (quota := _v2_quota(d / "cpu.max")) is not None
    ]
    if limits:
        return min(limits)
    try:  # cgroup v1: a quota of -1 means unlimited
        quota_us = int((root / "cpu" / "cpu.cfs_quota_us").read_text())
        period_us = int((root / "cpu" / "cpu.cfs_period_us").read_text())
    except (OSError, ValueError):
        return None
    if quota_us <= 0 or period_us <= 0:
        return None
    return max(1, quota_us // period_us)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    """Size ``-n auto`` to the cgroup CPU quota; None defers to xdist's own count."""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    limit = cgroup_cpu_limit(_CGROUP, _SELF_CGROUP)
    if limit is None:
        return None
    return min(limit, len(os.sched_getaffinity(0)))


def restore_environment(before: dict[str, str]) -> list[str]:
    """Put ``os.environ`` back to ``before`` and return the keys a test changed (sorted).

    The keys in ``_PROCESS_ENVIRONMENT`` are neither reported nor restored.
    """
    after = dict(os.environ)
    changed = sorted(
        key
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key) and key not in _PROCESS_ENVIRONMENT
    )
    for key in changed:
        if key in before:
            os.environ[key] = before[key]
        else:
            os.environ.pop(key, None)
    return changed


@pytest.fixture(autouse=True)
def _environment_is_left_as_found() -> Iterator[None]:
    """Fail a test that changes ``os.environ`` without restoring it, then restore it."""
    before = dict(os.environ)
    yield
    if changed := restore_environment(before):
        pytest.fail(
            f"the test left os.environ changed: {', '.join(changed)} "
            "(use monkeypatch.setenv/delenv, or restore what you set)",
            pytrace=False,
        )


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> None:
    """``-n`` without ``--dist`` schedules with ``worksteal`` instead of xdist's ``load``."""
    if getattr(config.option, "numprocesses", None) and getattr(config.option, "dist", "") == "no":
        config.option.dist = "worksteal"


def parse_shard(spec: str) -> tuple[int, int]:
    """Parse ``K/N`` (1 <= K <= N) into ``(K, N)``; raise ``pytest.UsageError`` otherwise."""
    message = f"CI_TEST_SHARD={spec!r}: expected K/N with 1 <= K <= N, e.g. 2/3"
    try:
        k, n = (int(part) for part in spec.split("/"))
    except ValueError:
        raise pytest.UsageError(message) from None
    if not 1 <= k <= n:
        raise pytest.UsageError(message)
    return k, n


def _test_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def load_durations(path: Path) -> dict[str, float]:
    """Per-file seconds from a JSON file, or every ``*.json`` in a directory (the larger value wins).

    A missing or unreadable record is no record: the shards fall back to test counts.
    """
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    merged: dict[str, float] = {}
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for name, seconds in data.items():
            if isinstance(seconds, int | float) and seconds >= 0:
                merged[str(name)] = max(float(seconds), merged.get(str(name), 0.0))
    return merged


def _file_weights(per_file: Counter[str], durations: dict[str, float] | None) -> dict[str, float]:
    """Each file's weight: recorded seconds, else its test count at the recorded average rate."""
    known = {path: durations[path] for path in per_file if durations and path in durations}
    if not known:
        return {path: float(count) for path, count in per_file.items()}
    rate = sum(known.values()) / sum(per_file[path] for path in known)
    return {path: known.get(path, count * rate) for path, count in per_file.items()}


def assign_shards(
    nodeids: list[str], shards: int, durations: dict[str, float] | None = None
) -> dict[str, int]:
    """Assign each test a shard in ``1..shards``: heaviest unit first, onto the lightest shard.

    A unit is a whole test file, so its module fixtures stay in one shard, unless the file
    outweighs half a shard's fair share: then it is cut into that many runs of consecutive tests
    (by node id), each weighed pro rata. ``test_pins.py`` alone held a 37-minute shard of six on
    2026-10-16 while the next heaviest shard of many files took 21. A file's weight is the
    recorded seconds when ``durations`` has it, its test count at the recorded files' average
    rate when not, and the plain test count without ``durations``. Deterministic for a given
    collection and record, so every xdist worker (each collects alone) agrees.
    """
    by_file: dict[str, list[str]] = defaultdict(list)
    for nodeid in sorted(set(nodeids)):
        by_file[_test_file(nodeid)].append(nodeid)
    weight = _file_weights(Counter({f: len(ids) for f, ids in by_file.items()}), durations)
    cap = sum(weight.values()) / (2 * shards)
    units: list[tuple[float, str, list[str]]] = []
    for path, ids in by_file.items():
        pieces = min(len(ids), max(1, math.ceil(weight[path] / cap))) if cap > 0 else 1
        for i in range(pieces):
            chunk = ids[i * len(ids) // pieces : (i + 1) * len(ids) // pieces]
            units.append((weight[path] * len(chunk) / len(ids), chunk[0], chunk))
    load = [0.0] * shards
    owner: dict[str, int] = {}
    for cost, _first, chunk in sorted(units, key=lambda unit: (-unit[0], unit[1])):
        lightest = min(range(shards), key=lambda i: (load[i], i))
        load[lightest] += cost
        owner.update(dict.fromkeys(chunk, lightest + 1))
    return owner


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only this shard's tests when ``CI_TEST_SHARD`` is set."""
    spec = os.environ.get("CI_TEST_SHARD")
    if not spec:
        return
    k, n = parse_shard(spec)
    record = os.environ.get(_DURATIONS)
    durations = load_durations(Path(record)) if record else None
    owner = assign_shards([item.nodeid for item in items], n, durations)
    keep = [item for item in items if owner[item.nodeid] == k]
    drop = [item for item in items if owner[item.nodeid] != k]
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


_seconds: defaultdict[str, float] = defaultdict(float)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Sum each test file's seconds (setup, call, teardown) when the run is asked to record them.

    Under xdist the controller receives every worker's reports here, so its sum covers the run.
    """
    if os.environ.get(_DURATIONS_OUT):
        _seconds[_test_file(report.nodeid)] += report.duration


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Write the per-file seconds to ``CI_TEST_DURATIONS_OUT`` (the controller only)."""
    out = os.environ.get(_DURATIONS_OUT)
    if not out or hasattr(session.config, "workerinput") or not _seconds:
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {name: round(seconds, 3) for name, seconds in sorted(_seconds.items())}
    path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
