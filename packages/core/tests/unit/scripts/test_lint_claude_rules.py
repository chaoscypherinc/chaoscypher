# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CC011 rule in scripts/lint_claude_rules.py."""

import ast
import importlib.util
from pathlib import Path


def _load_linter_module():
    """Load lint_claude_rules.py as a module so we can import ClaudeRulesChecker."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cc011_flags_session_commit_in_mixin(tmp_path):
    """CC011 reports self.session.commit() inside an adapter mixin file."""
    linter = _load_linter_module()

    mixin_dir = tmp_path / "adapters" / "sqlite" / "mixins"
    mixin_dir.mkdir(parents=True)
    mixin_file = mixin_dir / "bad_mixin.py"
    mixin_file.write_text(
        "class BadMixin:\n"
        "    def write_something(self):\n"
        "        self.session.add(None)\n"
        "        self.session.commit()\n"
    )

    tree = ast.parse(mixin_file.read_text())
    checker = linter.ClaudeRulesChecker(mixin_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert len(cc011) == 1
    assert "_maybe_commit" in cc011[0].message


def test_cc011_allows_maybe_commit(tmp_path):
    """CC011 does not flag self._maybe_commit() in mixins."""
    linter = _load_linter_module()

    mixin_dir = tmp_path / "adapters" / "sqlite" / "mixins"
    mixin_dir.mkdir(parents=True)
    mixin_file = mixin_dir / "good_mixin.py"
    mixin_file.write_text(
        "class GoodMixin:\n"
        "    def write_something(self):\n"
        "        self.session.add(None)\n"
        "        self._maybe_commit()\n"
    )

    tree = ast.parse(mixin_file.read_text())
    checker = linter.ClaudeRulesChecker(mixin_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert cc011 == []


def test_cc011_ignores_non_mixin_files(tmp_path):
    """CC011 does not flag self.session.commit() outside adapter mixins."""
    linter = _load_linter_module()

    other_dir = tmp_path / "services"
    other_dir.mkdir(parents=True)
    other_file = other_dir / "some_service.py"
    other_file.write_text(
        "class SomeService:\n    def write_something(self):\n        self.session.commit()\n"
    )

    tree = ast.parse(other_file.read_text())
    checker = linter.ClaudeRulesChecker(other_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert cc011 == []


def test_cc011_flags_session_commit_in_core_repo(tmp_path):
    """CC011 flags self.session.commit() in packages/core/.../repos/."""
    linter = _load_linter_module()

    repo_dir = tmp_path / "chaoscypher_core" / "repos" / "graph"
    repo_dir.mkdir(parents=True)
    repo_file = repo_dir / "sqlite_node_ops.py"
    repo_file.write_text(
        "class NodeOps:\n"
        "    def create(self):\n"
        "        self.session.add(None)\n"
        "        self.session.commit()\n"
    )

    tree = ast.parse(repo_file.read_text())
    checker = linter.ClaudeRulesChecker(repo_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert len(cc011) == 1


def test_cc011_flags_session_commit_in_cortex_feature_repo(tmp_path):
    """CC011 flags self.session.commit() in cortex features/*/repository.py files."""
    linter = _load_linter_module()

    feat_dir = tmp_path / "chaoscypher_cortex" / "features" / "workflows"
    feat_dir.mkdir(parents=True)
    feat_file = feat_dir / "repository.py"
    feat_file.write_text(
        "class WorkflowRepository:\n    def save(self):\n        self.session.commit()\n"
    )

    tree = ast.parse(feat_file.read_text())
    checker = linter.ClaudeRulesChecker(feat_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert len(cc011) == 1


def test_cc011_allows_session_commit_in_cortex_shared(tmp_path):
    """CC011 does NOT flag self.session.commit() in cortex/shared/ (reset/seed entry points)."""
    linter = _load_linter_module()

    shared_dir = tmp_path / "chaoscypher_cortex" / "shared" / "reset"
    shared_dir.mkdir(parents=True)
    shared_file = shared_dir / "data_reset.py"
    shared_file.write_text(
        "class DataReset:\n    def reset(self):\n        self.session.commit()\n"
    )

    tree = ast.parse(shared_file.read_text())
    checker = linter.ClaudeRulesChecker(shared_file)
    checker.visit(tree)

    cc011 = [v for v in checker.violations if v.rule == "CC011"]
    assert cc011 == []
