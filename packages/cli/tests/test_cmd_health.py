# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ``chaoscypher health`` command and its probe helpers.

Covers:
- check_ollama: reachable with models, unreachable
- connect_context_stats: context_error, search_stats, db_error, node_count branches
- health command: all-healthy path, each individual check failing,
  DB/search variations, exit-code semantics
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResp:
    """Minimal stand-in for urllib.request.urlopen context-manager return."""

    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _tags_body(model_names: list[str]) -> bytes:
    payload = {"models": [{"name": n} for n in model_names]}
    return json.dumps(payload).encode()


def _fake_settings(timeout: float = 2.0, workers: int = 2) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        cli=SimpleNamespace(
            ollama_connect_timeout_seconds=timeout,
            health_check_workers=workers,
        )
    )


def _fake_engine_settings(
    *,
    base_url: str = "http://localhost:11434",
    chat_model: str = "qwen3:30b-instruct",
    extraction_model: str | None = "qwen3:30b-instruct",
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
    timeout: float = 2.0,
    workers: int = 2,
) -> Any:
    """Build a SimpleNamespace shaped like the app settings the health command reads.

    Engine config lives in settings.yaml as of the 2026-06 config unification,
    so the health command reads everything (LLM + cli timing) off get_settings().
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        llm=SimpleNamespace(
            primary_ollama_url=base_url,
            ollama_chat_model=chat_model,
            ollama_extraction_model=extraction_model,
        ),
        embedding=SimpleNamespace(model=embedding_model),
        cli=SimpleNamespace(
            ollama_connect_timeout_seconds=timeout,
            health_check_workers=workers,
        ),
    )


# ---------------------------------------------------------------------------
# check_ollama
# ---------------------------------------------------------------------------


class TestCheckOllama:
    """Unit tests for the check_ollama probe helper."""

    def test_reachable_returns_true_and_model_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chaoscypher_cli.commands.health import check_ollama

        body = _tags_body(["qwen3:30b-instruct", "llama3.2"])
        monkeypatch.setattr(
            "chaoscypher_cli.commands.health.urllib.request.urlopen",
            lambda *_a, **_kw: _FakeResp(200, body),
        )
        monkeypatch.setattr(
            "chaoscypher_cli.commands.health.get_settings",
            lambda: _fake_settings(),
        )

        reachable, version, models = check_ollama("http://localhost:11434")

        assert reachable is True
        assert version is None
        assert "qwen3:30b-instruct" in models
        assert "llama3.2" in models

    def test_reachable_empty_model_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chaoscypher_cli.commands.health import check_ollama

        body = _tags_body([])
        monkeypatch.setattr(
            "chaoscypher_cli.commands.health.urllib.request.urlopen",
            lambda *_a, **_kw: _FakeResp(200, body),
        )
        monkeypatch.setattr(
            "chaoscypher_cli.commands.health.get_settings",
            lambda: _fake_settings(),
        )

        reachable, _version, models = check_ollama("http://localhost:11434")

        assert reachable is True
        assert models == []

    def test_unreachable_returns_false_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from chaoscypher_cli.commands.health import check_ollama

        def _raise(*_a: object, **_kw: object) -> None:
            raise ConnectionRefusedError("nope")

        monkeypatch.setattr("chaoscypher_cli.commands.health.urllib.request.urlopen", _raise)
        monkeypatch.setattr(
            "chaoscypher_cli.commands.health.get_settings",
            lambda: _fake_settings(),
        )

        reachable, _version, models = check_ollama("http://localhost:11434")

        assert reachable is False
        assert models == []


# ---------------------------------------------------------------------------
# connect_context_stats
# ---------------------------------------------------------------------------


class TestConnectContextStats:
    """Unit tests for connect_context_stats helper.

    CLIContext is imported lazily inside connect_context_stats, so we must
    patch ``chaoscypher_cli.context.CLIContext`` (the source module) rather
    than a module-level attribute on health.py.
    """

    def test_returns_context_error_when_clictx_raises(self) -> None:
        from chaoscypher_cli.commands.health import connect_context_stats

        with patch(
            "chaoscypher_cli.context.CLIContext",
            side_effect=RuntimeError("no config"),
        ):
            result = connect_context_stats()

        assert result.get("context_error") is True

    def test_returns_search_stats_when_search_repo_present(self) -> None:
        from chaoscypher_cli.commands.health import connect_context_stats

        mock_search_repo = MagicMock()
        mock_search_repo.get_index_stats.return_value = {
            "fulltext": {"document_count": 42},
            "vector": {"vector_count": 10},
        }
        mock_graph_repo = MagicMock()
        mock_graph_repo.count_nodes.return_value = 5
        mock_graph_repo.count_edges.return_value = 3

        mock_ctx = MagicMock()
        mock_ctx.search_repository = mock_search_repo
        mock_ctx.graph_repository = mock_graph_repo

        with patch("chaoscypher_cli.context.CLIContext", return_value=mock_ctx):
            result = connect_context_stats()

        assert result.get("search_stats") == {
            "fulltext": {"document_count": 42},
            "vector": {"vector_count": 10},
        }
        assert result.get("node_count") == 5
        assert result.get("edge_count") == 3

    def test_returns_search_error_when_get_index_stats_raises(self) -> None:
        from chaoscypher_cli.commands.health import connect_context_stats

        mock_search_repo = MagicMock()
        mock_search_repo.get_index_stats.side_effect = RuntimeError("index gone")
        mock_graph_repo = MagicMock()
        mock_graph_repo.count_nodes.return_value = 0
        mock_graph_repo.count_edges.return_value = 0

        mock_ctx = MagicMock()
        mock_ctx.search_repository = mock_search_repo
        mock_ctx.graph_repository = mock_graph_repo

        with patch("chaoscypher_cli.context.CLIContext", return_value=mock_ctx):
            result = connect_context_stats()

        assert "search_error" in result

    def test_returns_db_error_when_count_raises(self) -> None:
        from chaoscypher_cli.commands.health import connect_context_stats

        mock_search_repo = MagicMock()
        mock_search_repo.get_index_stats.return_value = {}
        mock_graph_repo = MagicMock()
        mock_graph_repo.count_nodes.side_effect = RuntimeError("db gone")

        mock_ctx = MagicMock()
        mock_ctx.search_repository = mock_search_repo
        mock_ctx.graph_repository = mock_graph_repo

        with patch("chaoscypher_cli.context.CLIContext", return_value=mock_ctx):
            result = connect_context_stats()

        assert "db_error" in result

    def test_returns_context_error_when_connect_raises(self) -> None:
        from chaoscypher_cli.commands.health import connect_context_stats

        mock_ctx = MagicMock()
        mock_ctx.connect.side_effect = RuntimeError("connect failed")

        with patch("chaoscypher_cli.context.CLIContext", return_value=mock_ctx):
            result = connect_context_stats()

        assert result.get("context_error") is True


# ---------------------------------------------------------------------------
# health command — end-to-end
# ---------------------------------------------------------------------------


def _invoke_health(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ollama_result: tuple[bool, str | None, list[str]],
    db_result: dict[str, Any],
    chat_model: str = "qwen3:30b-instruct",
    extraction_model: str = "qwen3:30b-instruct",
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B",
) -> Any:
    """Helper: patch all health probes and invoke the health command."""
    from chaoscypher_cli.commands.health import health

    monkeypatch.setattr(
        "chaoscypher_cli.commands.health.get_settings",
        lambda: _fake_engine_settings(
            chat_model=chat_model,
            extraction_model=extraction_model,
            embedding_model=embedding_model,
        ),
    )
    monkeypatch.setattr(
        "chaoscypher_cli.commands.health.check_ollama",
        lambda _url: ollama_result,
    )
    monkeypatch.setattr(
        "chaoscypher_cli.commands.health.connect_context_stats",
        lambda: db_result,
    )

    runner = CliRunner()
    return runner.invoke(health)


class TestHealthCommand:
    """End-to-end tests for the health command through CliRunner."""

    def test_all_healthy_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 10},
                    "vector": {"vector_count": 5},
                },
                "node_count": 20,
                "edge_count": 8,
            },
        )
        assert result.exit_code == 0, result.output
        assert "All systems healthy." in result.output

    def test_all_healthy_output_contains_checks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 10},
                    "vector": {"vector_count": 5},
                },
                "node_count": 20,
                "edge_count": 8,
            },
        )
        for label in (
            "Ollama",
            "Chat Model",
            "Extraction",
            "Embeddings",
            "Search Index",
            "Database",
        ):
            assert label in result.output, f"expected '{label}' in output"

    def test_ollama_unreachable_shows_issues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(False, None, []),
            db_result={"context_error": True},
        )
        assert result.exit_code != 0  # issues found → non-zero exit
        assert "Not reachable" in result.output
        assert "issue" in result.output

    def test_chat_model_not_installed_shows_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ollama up but chat model missing."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["other-model"]),
            db_result={"context_error": True},
            chat_model="qwen3:30b-instruct",
        )
        assert "NOT INSTALLED" in result.output
        assert "issue" in result.output

    def test_extraction_model_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ollama up, chat model present, extraction model missing."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"context_error": True},
            chat_model="qwen3:30b-instruct",
            extraction_model="missing-model",
        )
        assert "NOT INSTALLED" in result.output

    def test_extraction_model_falls_back_to_chat_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty extraction model falls back to the chat model (core schema rule).

        Engine config reads ``settings.llm.ollama_extraction_model or
        ollama_chat_model``, so an unset extraction model resolves to the chat
        model and the Extraction row shows it as installed rather than warning.
        """
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"context_error": True},
            chat_model="qwen3:30b-instruct",
            extraction_model="",
        )
        assert "Not configured" not in result.output
        # Extraction row resolves to the chat model and is shown as present.
        assert result.output.count("qwen3:30b-instruct") >= 2

    def test_context_error_shows_skip_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"context_error": True},
        )
        assert "Skipped" in result.output

    def test_search_error_shows_check_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"search_error": True, "node_count": 5, "edge_count": 2},
        )
        assert "Check failed" in result.output

    def test_empty_search_index_shows_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
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
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 100},
                    "vector": {"vector_count": 50},
                },
                "node_count": 30,
                "edge_count": 10,
            },
        )
        assert "100" in result.output  # docs count
        assert "All systems healthy." in result.output

    def test_db_error_shows_check_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
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

    def test_empty_database_shows_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
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

    def test_no_search_stats_key_shows_not_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """db_result has no search_stats key and no search_error — show 'Not available'."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"node_count": 5, "edge_count": 2},
        )
        assert "Not available" in result.output

    def test_no_node_count_key_shows_not_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """db_result has no node_count and no db_error — show 'Not available'."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={
                "search_stats": {
                    "fulltext": {"document_count": 5},
                    "vector": {"vector_count": 2},
                },
            },
        )
        assert "Not available" in result.output

    def test_embedding_model_without_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Embedding model name without '/' uses it verbatim."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"context_error": True},
            embedding_model="my-embed-model",
        )
        assert "my-embed-model" in result.output

    def test_multiple_issues_plural_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ≥2 issues, output says 'issues' (plural)."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(False, None, []),  # 3 issues: ollama + chat + extraction
            db_result={
                "search_error": True,  # +1
                "db_error": True,  # +1
            },
        )
        assert "issues" in result.output

    def test_single_issue_singular_word(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When exactly 1 issue, output says 'issue' (singular)."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(True, None, ["qwen3:30b-instruct"]),
            db_result={"search_error": True, "node_count": 5, "edge_count": 2},
        )
        # Should say "1 issue found." not "1 issues found."
        assert "1 issue found." in result.output
        assert result.exit_code == 1

    def test_ollama_unreachable_chat_model_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When Ollama unreachable, chat model row says '(Ollama unreachable)'."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(False, None, []),
            db_result={"context_error": True},
        )
        assert "Ollama unreachable" in result.output

    def test_ollama_unreachable_extraction_model_branch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When Ollama unreachable and extraction model set, shows '(Ollama unreachable)'."""
        result = _invoke_health(
            monkeypatch,
            ollama_result=(False, None, []),
            db_result={"context_error": True},
            extraction_model="some-model",
        )
        assert "Ollama unreachable" in result.output
