# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the tree-wide secret scan's engine choice (scripts/secrets_scan.py).

detect-secrets runs everywhere and gitleaks runs beside it where installed: the two disagree,
and until 2026-10-17 a hex fixture only detect-secrets flagged (#775) kept the routine sandbox
red for six nights while the gitleaks-only pre-push hook and GitHub CI stayed green.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_scan():
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "secrets_scan.py"
    spec = importlib.util.spec_from_file_location("secrets_scan", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SCAN = _load_scan()


def _run_main(
    monkeypatch: pytest.MonkeyPatch, *, gitleaks: int | None, detect_secrets: int
) -> tuple[int, list[str]]:
    ran: list[str] = []

    def fake_gitleaks() -> int:
        ran.append("gitleaks")
        assert gitleaks is not None
        return gitleaks

    def fake_detect_secrets(refresh: bool = False) -> int:
        ran.append("detect-secrets")
        return detect_secrets

    monkeypatch.setattr(
        _SCAN.shutil, "which", lambda name: "/bin/gitleaks" if gitleaks is not None else None
    )
    monkeypatch.setattr(_SCAN, "_run_gitleaks", fake_gitleaks)
    monkeypatch.setattr(_SCAN, "_run_detect_secrets", fake_detect_secrets)
    monkeypatch.setattr(sys, "argv", ["secrets_scan.py"])
    return _SCAN.main(), ran


def test_a_clean_gitleaks_scan_does_not_skip_detect_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, ran = _run_main(monkeypatch, gitleaks=0, detect_secrets=1)
    assert ran == ["gitleaks", "detect-secrets"]
    assert code == 1


def test_without_gitleaks_detect_secrets_alone_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    code, ran = _run_main(monkeypatch, gitleaks=None, detect_secrets=0)
    assert ran == ["detect-secrets"]
    assert code == 0


@pytest.mark.parametrize(("gitleaks", "detect_secrets", "want"), [(1, 0, 1), (2, 1, 2), (0, 0, 0)])
def test_the_worst_status_wins(
    monkeypatch: pytest.MonkeyPatch, gitleaks: int, detect_secrets: int, want: int
) -> None:
    assert _run_main(monkeypatch, gitleaks=gitleaks, detect_secrets=detect_secrets)[0] == want


def _hook_flags(tmp_path: Path, line: str, rel: str = "board.json") -> bool:
    """Whether the real detect-secrets hook, on the committed baseline's filters, flags ``line``."""
    import shutil
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[5]
    baseline = tmp_path / "baseline.json"
    shutil.copy(repo / ".secrets.baseline", baseline)  # the hook may rewrite its baseline
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{\n" + line + "\n}\n", encoding="utf-8")
    # the hook diffs through git, so it only scans inside a repository
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607 - fixed argv
    done = subprocess.run(  # noqa: S603 - the installed hook, fixed argv
        [
            sys.executable,
            "-m",
            "detect_secrets.pre_commit_hook",
            "--baseline",
            str(baseline),
            rel,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "Not a git repository" not in done.stderr, done.stderr
    return "Potential secrets" in done.stdout + done.stderr


DIGEST = (
    "ac715178a3dbc83a3ecd180e79bf81c99013ba2fd151af10343bf1e8e80e4ba6"  # pragma: allowlist secret
)


def test_a_sha256_digest_field_is_not_a_secret(tmp_path: Path) -> None:
    """A generated file's ``*_sha256`` digest (the leaderboard's fixture_sha256) passes the scan."""
    assert not _hook_flags(tmp_path, f'  "fixture_sha256": "{DIGEST}"')


def test_the_same_hex_under_another_key_is_still_flagged(tmp_path: Path) -> None:
    """The digest rule is keyed on the field name, so a hex token elsewhere still trips the scan."""
    assert _hook_flags(tmp_path, f'  "api_token": "{DIGEST}"')


RECIPE = "14edff24a295"  # pragma: allowlist secret


def test_a_scorer_recipe_field_is_not_a_secret(tmp_path: Path) -> None:
    """The leaderboard's ``scorer_recipe`` (12 hex characters, a hash of the scorer) passes the scan."""
    assert not _hook_flags(tmp_path, f'  "scorer_recipe": "{RECIPE}"')


def test_a_short_hex_under_another_key_is_still_flagged(tmp_path: Path) -> None:
    """The recipe rule is keyed on the field name, like the digest rule."""
    assert _hook_flags(tmp_path, f'  "api_secret": "{RECIPE}"')


PASSAGE = (
    '           "context": "base_url=\'http://localhost:11434\',\\n'
    "    api_key='ollama',  # required but ignored\\n)\","  # pragma: allowlist secret
)


def test_a_retrieved_passage_on_the_board_is_not_scanned(tmp_path: Path) -> None:
    """A board row's ``context`` is corpus text retrieved as is, like the corpus file itself."""
    assert not _hook_flags(tmp_path, PASSAGE)


def test_the_same_example_outside_a_passage_is_still_flagged(tmp_path: Path) -> None:
    """The passage rule is keyed on the ``context`` field: the line under another key trips the scan."""
    assert _hook_flags(tmp_path, PASSAGE.replace('"context"', '"settings"'))


CORPUS = "packages/cli/src/chaoscypher_cli/benchmark/data/datasets/ollama_docs/ollama_docs.txt"


def test_a_benchmark_corpus_is_not_scanned_for_placeholder_keys(tmp_path: Path) -> None:
    """A corpus is third-party text measured as is: its docs' ``"your_api_key"`` examples pass."""
    line = 'export OLLAMA_API_KEY="your_api_key"'  # pragma: allowlist secret
    assert not _hook_flags(tmp_path, line, rel=CORPUS)
    assert _hook_flags(tmp_path, line, rel="packages/cli/src/chaoscypher_cli/settings_example.txt")


def test_the_scan_and_the_baseline_exclude_the_same_files() -> None:
    """``_FALLBACK_EXCLUDE`` (files the scan never hands the hook) is the baseline's own list."""
    import json

    repo = Path(__file__).resolve().parents[5]
    filters = json.loads((repo / ".secrets.baseline").read_text(encoding="utf-8"))["filters_used"]
    baseline = next(
        f["pattern"] for f in filters if f["path"].endswith("regex.should_exclude_file")
    )
    assert list(_load_scan()._FALLBACK_EXCLUDE) == baseline
