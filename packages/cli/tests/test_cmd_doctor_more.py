# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional tests for ``chaoscypher doctor`` to cover remaining branches.

Supplements ``test_doctor.py`` (which must NOT be modified).

Targets:
- check_lexicon_hub: status >= 400 response (False branch, line 57)
- check_config_file: exception during parse (lines 109-110)
- doctor command end-to-end: every uncovered branch in lines 162-271
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_settings(
    *,
    chat_model: str = "qwen3:30b-instruct",
    extraction_model: str | None = "qwen3:30b-instruct",
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
    timeout: float = 2.0,
    workers: int = 2,
) -> Any:
    """Build a SimpleNamespace shaped like the app settings ``doctor`` reads.

    Engine config (LLM, embedding, lexicon) plus cli timing all read off
    ``get_settings()`` as of the 2026-06 config unification.
    """
    return SimpleNamespace(
        llm=SimpleNamespace(
            primary_ollama_url="http://localhost:11434",
            ollama_chat_model=chat_model,
            ollama_extraction_model=extraction_model,
        ),
        embedding=SimpleNamespace(model=embedding_model),
        lexicon=SimpleNamespace(url="https://lexicon.chaoscypher.com"),
        cli=SimpleNamespace(
            ollama_connect_timeout_seconds=timeout,
            health_check_workers=workers,
        ),
    )


class _FakeResponse:
    """Minimal stand-in for urlopen context-manager return."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b""


def _invoke_doctor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ollama_result: tuple[bool, str | None, list[str]] = (True, None, ["qwen3:30b-instruct"]),
    db_result: dict[str, Any] | None = None,
    hub_result: tuple[bool, str | None] = (True, None),
    cortex_result: tuple[bool, str | None] = (False, None),
    config_file_result: tuple[bool, str, str | None] | None = None,
    chat_model: str = "qwen3:30b-instruct",
    extraction_model: str = "qwen3:30b-instruct",
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
    tmp_path: Any = None,
) -> Any:
    """Helper: patch all doctor probes and invoke the doctor command."""
    from chaoscypher_cli.commands import doctor as doctor_module
    from chaoscypher_cli.commands.doctor import doctor as doctor_cmd

    if db_result is None:
        db_result = {"context_error": True}

    monkeypatch.setattr(
        doctor_module,
        "get_settings",
        lambda: _fake_settings(
            chat_model=chat_model,
            extraction_model=extraction_model,
            embedding_model=embedding_model,
        ),
    )
    monkeypatch.setattr(doctor_module, "check_ollama", lambda _url: ollama_result)
    monkeypatch.setattr(doctor_module, "connect_context_stats", lambda: db_result)
    monkeypatch.setattr(doctor_module, "check_lexicon_hub", lambda _url, _timeout: hub_result)
    monkeypatch.setattr(doctor_module, "check_cortex", lambda _candidates, _timeout: cortex_result)

    # Stale-file probe is a real os-path check; stub it out so doctor output
    # is deterministic regardless of the host config dir.
    monkeypatch.setattr(doctor_module, "check_stale_config_files", list)

    if config_file_result is not None:
        monkeypatch.setattr(doctor_module, "check_config_file", lambda: config_file_result)
    elif tmp_path is not None:
        missing = tmp_path / "absent.yaml"
        monkeypatch.setattr(doctor_module.engine_config, "settings_yaml_path", lambda: missing)
    else:
        # Use a known-missing path so check_config_file returns (False, ..., None)
        monkeypatch.setattr(
            doctor_module,
            "check_config_file",
            lambda: (False, "/nonexistent/settings.yaml", None),
        )

    return CliRunner().invoke(doctor_cmd)


# ---------------------------------------------------------------------------
# check_lexicon_hub — missing branch: status >= 400 response
# ---------------------------------------------------------------------------


class TestCheckLexiconHubStatusBranch:
    def test_non_2xx_3xx_status_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A response with status 500 (not caught as HTTPError) → (False, 'HTTP 500')."""
        from chaoscypher_cli.commands.doctor import check_lexicon_hub

        monkeypatch.setattr(
            "chaoscypher_cli.commands.doctor.urllib.request.urlopen",
            lambda *_a, **_kw: _FakeResponse(500),
        )

        reachable, detail = check_lexicon_hub("https://hub.example.com", timeout=1.0)

        assert reachable is False
        assert detail == "HTTP 500"


# ---------------------------------------------------------------------------
# check_config_file — exception during reload
# ---------------------------------------------------------------------------


class TestCheckConfigFileParseFail:
    def test_exception_during_reload_returns_error_string(self, tmp_path: Any) -> None:
        """When reload_settings() raises, returns (True, path, error_str)."""
        from chaoscypher_cli.commands import doctor

        present = tmp_path / "settings.yaml"
        present.write_text("bad: [yaml: {{\n")

        def _patched_settings_yaml_path() -> Any:
            return present

        def _patched_reload_settings() -> Any:
            raise ValueError("YAML parse error")

        with patch.object(doctor.engine_config, "settings_yaml_path", _patched_settings_yaml_path):
            with patch.object(doctor, "reload_settings", _patched_reload_settings):
                exists, path, error = doctor.check_config_file()

        assert exists is True
        assert path == str(present)
        assert error == "YAML parse error"


# ---------------------------------------------------------------------------
# doctor command — branch coverage top-ups
# ---------------------------------------------------------------------------


class TestDoctorOllamaBranches:
    """Cover the Ollama-unreachable and chat/extraction model branches."""

    def test_ollama_unreachable_increments_issues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(False, None, []),
        )
        assert result.exit_code == 0
        assert "Not reachable" in result.output
        assert "issue" in result.output

    def test_chat_model_not_installed_when_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ollama reachable but chat model not in installed list."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(True, None, ["other-model"]),
            chat_model="missing-chat",
        )
        assert "NOT INSTALLED" in result.output
        assert "issue" in result.output

    def test_chat_model_ollama_unreachable_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ollama not reachable — chat model row says '(Ollama unreachable)'."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(False, None, []),
        )
        assert "Ollama unreachable" in result.output

    def test_extraction_model_not_installed_when_reachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ollama reachable, chat model ok, extraction model missing."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            extraction_model="missing-extract",
        )
        assert "NOT INSTALLED" in result.output

    def test_extraction_model_ollama_unreachable_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ollama unreachable with extraction model configured."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(False, None, []),
            extraction_model="some-model",
        )
        assert "Ollama unreachable" in result.output

    def test_extraction_model_empty_falls_back_to_chat_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty extraction model falls back to the chat model (core schema rule).

        ``settings.llm.ollama_extraction_model or ollama_chat_model`` means an
        unset extraction model resolves to the chat model, so the Extraction
        row shows it as installed rather than warning 'Not configured'.
        """
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            extraction_model="",
        )
        assert "Not configured" not in result.output
        assert result.output.count("qwen3:30b-instruct") >= 2


class TestDoctorSearchDbBranches:
    """Cover all db_result branching paths inside the doctor command."""

    def test_search_error_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={"search_error": True, "node_count": 5, "edge_count": 2},
        )
        assert "Check failed" in result.output

    def test_search_stats_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 0},
                    "vector": {"vector_count": 0},
                },
                "node_count": 0,
                "edge_count": 0,
            },
        )
        assert "Empty" in result.output

    def test_search_stats_with_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 42},
                    "vector": {"vector_count": 10},
                },
                "node_count": 20,
                "edge_count": 8,
            },
        )
        assert "42" in result.output
        assert "All systems healthy." in result.output

    def test_no_search_stats_key_shows_not_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={"node_count": 5, "edge_count": 2},
        )
        assert "Not available" in result.output

    def test_db_error_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 5},
                    "vector": {"vector_count": 2},
                },
                "db_error": True,
            },
        )
        assert "Check failed" in result.output
        assert "issue" in result.output

    def test_empty_database_node_count_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 5},
                    "vector": {"vector_count": 2},
                },
                "node_count": 0,
                "edge_count": 0,
            },
        )
        assert "Empty" in result.output

    def test_no_node_count_key_shows_not_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 5},
                    "vector": {"vector_count": 2},
                },
            },
        )
        assert "Not available" in result.output


class TestDoctorHubCortexBranches:
    """Cover hub and Cortex output branches."""

    def test_hub_unreachable_shows_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            hub_result=(False, "ConnectionRefusedError"),
        )
        assert "Not reachable" in result.output
        # Hub unreachable does NOT count as an issue
        assert "All systems healthy." in result.output

    def test_hub_reachable_with_detail_shows_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            hub_result=(True, "HTTP 404"),
        )
        assert "HTTP 404" in result.output

    def test_cortex_reachable_shows_running_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            cortex_result=(True, "http://127.0.0.1:8000"),
        )
        assert "Running at" in result.output
        assert "http://127.0.0.1:8000" in result.output


class TestDoctorConfigFileBranches:
    """Cover config file output branches."""

    def test_config_file_with_parse_error_increments_issues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _invoke_doctor(
            monkeypatch,
            config_file_result=(True, "/path/to/cli.yaml", "YAML parse error"),
        )
        assert "YAML parse error" in result.output
        assert "issue" in result.output

    def test_config_file_exists_and_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            config_file_result=(True, "/path/to/cli.yaml", None),
        )
        assert "/path/to/cli.yaml" in result.output
        assert "All systems healthy." in result.output

    def test_config_file_absent_shows_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_doctor(
            monkeypatch,
            config_file_result=(False, "/path/to/cli.yaml", None),
        )
        assert "running with defaults" in result.output
        assert "All systems healthy." in result.output


class TestDoctorIssuesSummary:
    """Cover the 'issues found' summary line variations."""

    def test_multiple_issues_plural(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When >= 2 issues, output says 'issues' plural."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(False, None, []),  # ollama + chat + extraction = 3 issues
            db_result={"search_error": True, "db_error": True},  # +2 more
        )
        assert "issues" in result.output

    def test_single_issue_singular(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When exactly 1 issue, output says '1 issue found.' (singular)."""
        result = _invoke_doctor(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"search_error": True, "node_count": 5, "edge_count": 2},
        )
        assert "1 issue found." in result.output
