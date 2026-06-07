# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ``chaoscypher doctor`` and its probe helpers.

The doctor command is the comprehensive diagnostic sweep — superset
of ``health`` with Lexicon hub, local Cortex, and config-file probes
added. These tests pin both the probe-helper contracts and the
end-to-end output structure so future probe additions don't silently
drop sections.
"""

from __future__ import annotations

import urllib.error
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner


def _fake_settings(**overrides: Any) -> SimpleNamespace:
    """Build a SimpleNamespace shaped like the app settings ``doctor`` reads.

    Engine config (LLM, embedding, lexicon) plus cli timing all read off
    ``get_settings()`` as of the 2026-06 config unification. Overrides let
    individual tests pin specific sections.
    """
    base = SimpleNamespace(
        llm=SimpleNamespace(
            primary_ollama_url="http://localhost:11434",
            ollama_chat_model="qwen3:30b-instruct",
            ollama_extraction_model="qwen3:30b-instruct",
        ),
        embedding=SimpleNamespace(model="Qwen/Qwen3-Embedding-0.6B"),
        lexicon=SimpleNamespace(url="https://lexicon.chaoscypher.com"),
        cli=SimpleNamespace(
            ollama_connect_timeout_seconds=1.0,
            health_check_workers=2,
        ),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ---------------------------------------------------------------------------
# check_lexicon_hub
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for ``urlopen``'s context-manager return."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b""


def test_check_lexicon_hub_returns_true_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_cli.commands.doctor import check_lexicon_hub

    monkeypatch.setattr(
        "chaoscypher_cli.commands.doctor.urllib.request.urlopen",
        lambda *_args, **_kwargs: _FakeResponse(200),
    )

    reachable, detail = check_lexicon_hub("https://hub.example.com", timeout=1.0)

    assert reachable is True
    assert detail is None


def test_check_lexicon_hub_treats_http_error_as_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 from the hub root still proves the host is online — surface
    that as reachable-with-detail rather than a hard fail (the hub
    frontend may legitimately 404 on HEAD /).
    """
    from chaoscypher_cli.commands.doctor import check_lexicon_hub

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError(
            url="https://hub.example.com",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr("chaoscypher_cli.commands.doctor.urllib.request.urlopen", _raise)

    reachable, detail = check_lexicon_hub("https://hub.example.com", timeout=1.0)

    assert reachable is True
    assert detail == "HTTP 404"


def test_check_lexicon_hub_returns_false_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_cli.commands.doctor import check_lexicon_hub

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr("chaoscypher_cli.commands.doctor.urllib.request.urlopen", _raise)

    reachable, detail = check_lexicon_hub("https://hub.example.com", timeout=1.0)

    assert reachable is False
    assert detail == "ConnectionRefusedError"


# ---------------------------------------------------------------------------
# check_cortex
# ---------------------------------------------------------------------------


def test_check_cortex_returns_first_reachable_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_cli.commands.doctor import check_cortex

    calls: list[str] = []

    def _urlopen(req: Any, **_kwargs: object) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        if "8080" in url:
            return _FakeResponse(200)
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr("chaoscypher_cli.commands.doctor.urllib.request.urlopen", _urlopen)

    reachable, base = check_cortex(["http://127.0.0.1:8000", "http://127.0.0.1:8080"], timeout=1.0)

    assert reachable is True
    assert base == "http://127.0.0.1:8080"
    # Should have tried 8000 first and then succeeded on 8080.
    assert calls == [
        "http://127.0.0.1:8000/api/v1/health",
        "http://127.0.0.1:8080/api/v1/health",
    ]


def test_check_cortex_returns_false_when_no_candidate_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_cli.commands.doctor import check_cortex

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("nope")

    monkeypatch.setattr("chaoscypher_cli.commands.doctor.urllib.request.urlopen", _raise)

    reachable, base = check_cortex(["http://127.0.0.1:8000", "http://127.0.0.1:8080"], timeout=1.0)

    assert reachable is False
    assert base is None


# ---------------------------------------------------------------------------
# check_config_file
# ---------------------------------------------------------------------------


def test_check_config_file_reports_absence(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from chaoscypher_cli.commands import doctor

    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setattr(doctor.engine_config, "settings_yaml_path", lambda: missing)

    exists, path, error = doctor.check_config_file()

    assert exists is False
    assert path == str(missing)
    assert error is None


def test_check_config_file_reports_presence_and_clean_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from chaoscypher_cli.commands import doctor

    present = tmp_path / "settings.yaml"
    present.write_text("setup_completed: true\n")
    monkeypatch.setattr(doctor.engine_config, "settings_yaml_path", lambda: present)
    monkeypatch.setattr(doctor, "reload_settings", lambda: _fake_settings())

    exists, path, error = doctor.check_config_file()

    assert exists is True
    assert path == str(present)
    assert error is None


# ---------------------------------------------------------------------------
# doctor command — end-to-end output sections
# ---------------------------------------------------------------------------


def test_doctor_renders_all_expected_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Pin the section headings the doctor command renders so future
    refactors can't silently drop a probe.
    """
    from chaoscypher_cli.commands import doctor as doctor_module
    from chaoscypher_cli.commands.doctor import doctor

    monkeypatch.setattr(doctor_module, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(
        doctor_module,
        "check_ollama",
        lambda _url: (True, None, ["qwen3:30b-instruct"]),
    )
    monkeypatch.setattr(
        doctor_module,
        "connect_context_stats",
        lambda: {"context_error": True},
    )
    monkeypatch.setattr(
        doctor_module,
        "check_lexicon_hub",
        lambda _url, _timeout: (True, None),
    )
    monkeypatch.setattr(
        doctor_module,
        "check_cortex",
        lambda _candidates, _timeout: (False, None),
    )

    missing_cfg = tmp_path / "absent.yaml"
    monkeypatch.setattr(doctor_module.engine_config, "settings_yaml_path", lambda: missing_cfg)
    monkeypatch.setattr(doctor_module, "check_stale_config_files", list)

    result = CliRunner().invoke(doctor)

    assert result.exit_code == 0, (result.output, result.stderr)
    expected_labels = [
        "Ollama",
        "Chat Model",
        "Extraction",
        "Embeddings",
        "Search Index",
        "Database",
        "Lexicon Hub",
        "Cortex API",
        "Config File",
    ]
    for label in expected_labels:
        assert label in result.output, f"expected `{label}` row in doctor output"


def test_doctor_marks_cortex_warn_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Cortex is informational — CLI works standalone, so an unreachable
    Cortex must not bump the issue count (i.e. the run still ends with
    'All systems healthy.' when everything else is fine).
    """
    from chaoscypher_cli.commands import doctor as doctor_module
    from chaoscypher_cli.commands.doctor import doctor

    monkeypatch.setattr(doctor_module, "get_settings", lambda: _fake_settings())
    monkeypatch.setattr(
        doctor_module,
        "check_ollama",
        lambda _url: (True, None, ["qwen3:30b-instruct"]),
    )
    monkeypatch.setattr(
        doctor_module,
        "connect_context_stats",
        lambda: {"context_error": True},
    )
    monkeypatch.setattr(
        doctor_module,
        "check_lexicon_hub",
        lambda _url, _timeout: (True, None),
    )
    monkeypatch.setattr(
        doctor_module,
        "check_cortex",
        lambda _candidates, _timeout: (False, None),
    )
    monkeypatch.setattr(
        doctor_module.engine_config, "settings_yaml_path", lambda: tmp_path / "absent.yaml"
    )
    monkeypatch.setattr(doctor_module, "check_stale_config_files", list)

    result = CliRunner().invoke(doctor)

    assert result.exit_code == 0
    assert "All systems healthy." in result.output
    assert "Cortex API" in result.output
