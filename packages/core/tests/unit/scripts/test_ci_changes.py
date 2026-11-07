# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""scripts/ci_changes.py: which CI jobs a change needs.

A change only to the private operations tree skips the jobs that build and test what ships;
anything the detection cannot place runs everything. A push to main diffs from the newest
green commit, so a superseded run cancelled on main loses no change.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


_PATH = Path(__file__).resolve().parents[5] / "scripts" / "ci_changes.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("ci_changes", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CC = _load()


@pytest.mark.parametrize(
    ("paths", "code", "docker"),
    [
        (["internal/TODO.md", "internal/scripts/merge_train.py"], False, False),
        (["internal/OPERATIONS.md"], False, False),
        # The benchmark packs, holdouts, run archive and tools are read by shipped unit tests.
        (["internal/benchmarks/holdout/x.yaml"], True, False),
        (["internal/TODO.md", "packages/core/src/x.py"], True, False),
        (["internal/TODO.md", "uv.lock"], True, True),
        (["packages/docker/Dockerfile"], True, True),
        (["packages/cli/pyproject.toml"], True, True),
        (["packages/interface/package-lock.json"], True, True),
        (["packages/interface/src/App.tsx"], True, False),
        ([".github/workflows/ci.yml"], True, False),
        (["internalx/a.md"], True, False),
        # No paths: a change the detection cannot place runs everything.
        ([], True, True),
        (None, True, True),
    ],
)
def test_classify(paths: list[str] | None, code: bool, docker: bool) -> None:
    assert CC.classify(paths) == {"code": code, "docker": docker}


def test_push_base_is_the_newest_green_ancestor_other_than_head() -> None:
    ancestors = {"a", "b"}
    assert CC.push_base("h", ["h", "x", "b", "a"], lambda old, new: old in ancestors) == "b"
    assert CC.push_base("h", ["x", "h"], lambda old, new: old in ancestors) is None
    assert CC.push_base("h", [], lambda old, new: True) is None


def test_changed_range() -> None:
    def never() -> list[str]:
        raise AssertionError("a pull request never reads the runs")

    yes = lambda old, new: True  # noqa: E731
    assert CC.changed_range("pull_request", "main", "h", never, yes) == "origin/main...HEAD"
    assert CC.changed_range("pull_request", "", "h", never, yes) is None
    assert CC.changed_range("push", "", "h", lambda: ["g"], yes) == "g..HEAD"
    assert CC.changed_range("push", "", "h", list, yes) is None
    assert CC.changed_range("workflow_dispatch", "", "h", list, yes) is None


def _run_git(root: Path, args: Any) -> str:
    # No hooks and no GIT_* from a calling hook (the pre-commit run of this suite): the throwaway
    # repo must not pick up the real checkout's hooks or repository pointers.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(  # noqa: S603 - a fixed git argv in a throwaway repo
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],  # noqa: S607
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _repo(root: Path) -> Any:
    def git(*args: str) -> str:
        return _run_git(root, args).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("config", "commit.gpgsign", "false")
    return git


def _commit(root: Path, git: Any, rel: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{rel} {len(git('log', '--oneline', '--all') or '')}\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", rel)
    return str(git("rev-parse", "HEAD"))


def _detect(root: Path, monkeypatch: pytest.MonkeyPatch, green: list[str], event: str = "push"):
    monkeypatch.setattr(CC, "green_runs", lambda *a: green)

    env = {"GITHUB_EVENT_NAME": event, "GITHUB_REPOSITORY": "o/r", "GITHUB_REF_NAME": "main"}
    return CC.detect(env, "ci.yml", lambda args: _run_git(root, args))


def test_a_push_diffs_from_the_newest_green_commit_not_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merges B (code) then C (private only) land together; B's run is cancelled on main.

    C's run must still see B's code change, so it diffs from A, the newest green commit.
    """
    git = _repo(tmp_path)
    a = _commit(tmp_path, git, "packages/core/a.py")
    _commit(tmp_path, git, "packages/core/b.py")
    c = _commit(tmp_path, git, "internal/TODO.md")
    outputs, why = _detect(tmp_path, monkeypatch, green=[a])
    assert outputs == {"code": True, "docker": False}, why
    assert why.endswith(f"{a}..HEAD")
    # Once B is green, a private-only push after it skips the code jobs.
    b = git("rev-parse", "HEAD~1")
    outputs, _ = _detect(tmp_path, monkeypatch, green=[b, a])
    assert outputs == {"code": False, "docker": False}
    assert c == git("rev-parse", "HEAD")


def test_no_green_ancestor_runs_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git = _repo(tmp_path)
    _commit(tmp_path, git, "internal/a.md")
    _commit(tmp_path, git, "internal/b.md")
    outputs, why = _detect(tmp_path, monkeypatch, green=["0" * 40])
    assert outputs == {"code": True, "docker": True}, why


def test_a_file_moved_into_the_private_tree_counts_its_old_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = _repo(tmp_path)
    _commit(tmp_path, git, "packages/core/moved.py")
    base = git("rev-parse", "HEAD")
    (tmp_path / "internal").mkdir()
    git("mv", "packages/core/moved.py", "internal/moved.py")
    git("commit", "-q", "-m", "move")
    outputs, why = _detect(tmp_path, monkeypatch, green=[base])
    assert outputs["code"] is True, why


def test_main_writes_github_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(CC, "detect", lambda env, wf: ({"code": False, "docker": False}, "why"))
    assert CC.main(["--workflow", "ci.yml"]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["code=false", "docker=false"]
