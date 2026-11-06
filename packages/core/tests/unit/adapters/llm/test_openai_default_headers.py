# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``llm.openai_default_headers``: extra headers on every OpenAI chat request.

An OpenAI-compatible proxy may route on a header (send this request to the
local model only), so the setting reaches ``ChatOpenAI(default_headers=...)``
on every provider the factory builds, the import-time summary's included.
Empty sends nothing. Values may be tokens: they never appear in a settings
dump, a log line or a validation message, and the names are checked so a
setting cannot inject a header line or replace ``Authorization``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.adapters.llm.summary import summary_llm_provider
from chaoscypher_core.app_config import Settings, mask_settings_dict, strip_masked_values
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.diagnostics.collector import bundle_settings
from chaoscypher_core.settings import EngineSettings, LLMSettings


_ENV = "CHAOSCYPHER_LLM__OPENAI_DEFAULT_HEADERS"
_SECRET = "route-token-not-for-logs"  # pragma: allowlist secret
_HEADERS = {"X-Example-Route": "local", "X-Example-Token": _SECRET}

_PROVIDER_CONFIG: dict[str, Any] = {
    "chat_provider": "openai",
    "llm_max_concurrent": 1,
    "llm_reserved_interactive": 0,
    "llm_enable_priority": True,
    "llm_request_timeout": 300,
    "openai_api_key": "sk-test",  # pragma: allowlist secret
    "openai_base_url": "http://127.0.0.1:9999/v1",
    "openai_chat_model": "example-model",
}


def _chat_openai_kwargs(**config: Any) -> dict[str, Any]:
    from chaoscypher_core.adapters.llm.providers import openai_provider

    captured: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    with patch.object(openai_provider, "ChatOpenAI", side_effect=fake_client):
        openai_provider.OpenAIProvider({**_PROVIDER_CONFIG, **config})
    return captured


def _llm(headers: object) -> LLMSettings:
    """``LLMSettings`` from plain input, as settings.yaml and the API hand it over."""
    return LLMSettings.model_validate({"openai_default_headers": headers})


def _settings(headers: dict[str, str] | None = None) -> EngineSettings:
    llm = LLMSettings.model_validate(
        {"chat_provider": "openai", "openai_default_headers": headers or {}}
    )
    return EngineSettings(llm=llm)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestHeadersReachTheClient:
    def test_headers_reach_chat_openai(self) -> None:
        kwargs = _chat_openai_kwargs(openai_default_headers=_HEADERS)
        assert kwargs["default_headers"] == _HEADERS

    @pytest.mark.parametrize("value", [None, {}])
    def test_empty_sends_no_default_headers(self, value: dict[str, str] | None) -> None:
        assert "default_headers" not in _chat_openai_kwargs(openai_default_headers=value)
        assert "default_headers" not in _chat_openai_kwargs()

    def test_the_init_log_names_headers_but_not_their_values(self) -> None:
        with capture_logs() as logs:
            _chat_openai_kwargs(openai_default_headers=_HEADERS)
        init = next(e for e in logs if e["event"] == "openai_initialized")
        assert init["default_header_names"] == sorted(_HEADERS)
        assert _SECRET not in repr(logs)

    def test_the_factory_passes_plain_header_values(self) -> None:
        config = ProviderFactory(_settings(_HEADERS))._get_provider_config()
        assert config["openai_default_headers"] == _HEADERS
        assert ProviderFactory(_settings())._get_provider_config()["openai_default_headers"] == {}

    def test_the_extraction_provider_gets_them_too(self) -> None:
        settings = _settings(_HEADERS)
        settings.llm.openai_extraction_model = "example-extraction-model"
        config = ProviderFactory(settings)._get_extraction_config()
        assert config["openai_default_headers"] == _HEADERS

    def test_the_import_summary_provider_gets_them_too(self) -> None:
        """The summary is built from a settings copy; the headers must survive the copy."""
        provider = summary_llm_provider(_settings(_HEADERS))
        config = provider._provider_factory._get_provider_config()
        assert config["openai_default_headers"] == _HEADERS
        assert config["client_max_retries"] == 0


# ---------------------------------------------------------------------------
# Loading from env and settings.yaml
# ---------------------------------------------------------------------------


class TestLoading:
    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV, raising=False)

    def test_default_is_empty(self, tmp_path: Path) -> None:
        assert Settings.load_from_yaml(tmp_path / "settings.yaml").llm.openai_default_headers == {}

    def test_env_json_object(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV, '{"X-Example-Route": "local"}')
        settings = Settings.load_from_yaml(tmp_path / "settings.yaml")
        headers = settings.llm.openai_default_headers
        assert {k: v.get_secret_value() for k, v in headers.items()} == {"X-Example-Route": "local"}

    def test_env_beats_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            "llm:\n  openai_default_headers:\n    X-Example-Other: from-yaml\n", encoding="utf-8"
        )
        monkeypatch.setenv(_ENV, '{"X-Example-Route": "local"}')
        headers = Settings.load_from_yaml(yaml_path).llm.openai_default_headers
        assert sorted(headers) == ["X-Example-Route"]

    def test_yaml_keeps_header_name_case(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(
            "llm:\n  openai_default_headers:\n    X-Example-Route: local\n", encoding="utf-8"
        )
        headers = Settings.load_from_yaml(yaml_path).llm.openai_default_headers
        assert headers["X-Example-Route"].get_secret_value() == "local"

    def test_bad_env_json_is_refused_without_echoing_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_ENV, f"X-Example-Token={_SECRET}")
        with pytest.raises(ValidationError, match="not valid JSON") as exc:
            Settings.load_from_yaml(tmp_path / "settings.yaml")
        assert _SECRET not in str(exc.value)

    def test_round_trips_through_the_settings_file(self, tmp_path: Path) -> None:
        """The serializer writes plaintext, so a saved file loads back the same headers."""
        import yaml

        settings = Settings(llm=_llm(_HEADERS))
        dumped = settings.model_dump(mode="json")
        assert dumped["llm"]["openai_default_headers"] == _HEADERS
        yaml_path = tmp_path / "settings.yaml"
        yaml_path.write_text(yaml.safe_dump({"llm": dumped["llm"]}), encoding="utf-8")
        loaded = Settings.load_from_yaml(yaml_path).llm.openai_default_headers
        assert {k: v.get_secret_value() for k, v in loaded.items()} == _HEADERS


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


class TestMasking:
    def test_repr_hides_values(self) -> None:
        assert _SECRET not in repr(_llm(_HEADERS))

    def test_settings_dump_masks_each_value_and_keeps_names(self) -> None:
        settings = Settings(llm=_llm(_HEADERS))
        for dump in (settings.model_dump(), settings.model_dump(mode="json")):
            masked = mask_settings_dict(dump)
            assert masked["llm"]["openai_default_headers"] == dict.fromkeys(_HEADERS, "configured")
            assert _SECRET not in repr(masked)

    def test_the_diagnostic_bundle_hides_values(self) -> None:
        bundle = bundle_settings(Settings(llm=_llm(_HEADERS)))
        assert _SECRET not in repr(bundle)
        assert sorted(bundle["llm"]["openai_default_headers"]) == sorted(_HEADERS)

    def test_echoed_masks_keep_the_stored_values(self) -> None:
        """A PATCH echoing the masked dump drops the masked entries; a real new value stays."""
        update = {
            "llm": {
                "openai_default_headers": {
                    "X-Example-Route": "configured",
                    "X-Example-Token": "configured",
                    "X-Example-New": "fresh",
                }
            }
        }
        strip_masked_values(update)
        assert update["llm"]["openai_default_headers"] == {"X-Example-New": "fresh"}


# ---------------------------------------------------------------------------
# Name and value checks
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize(
        "name",
        ["", "X Example", "X-Example:", "X-Example\r\nX-Injected", "X-Example\n", "X-Ünicode"],
    )
    def test_bad_names_are_refused(self, name: str) -> None:
        with pytest.raises(ValidationError, match="invalid header name") as exc:
            _llm({name: _SECRET})
        assert _SECRET not in str(exc.value)

    @pytest.mark.parametrize("name", ["Authorization", "authorization", "AUTHORIZATION"])
    def test_authorization_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError, match="from the API key"):
            _llm({name: _SECRET})

    @pytest.mark.parametrize("value", [f"{_SECRET}\r\nX-Injected: 1", f"{_SECRET}\n", "a\0b"])
    def test_line_breaks_in_values_are_refused(self, value: str) -> None:
        with pytest.raises(ValidationError, match="line break") as exc:
            _llm({"X-Example-Token": value})
        assert _SECRET not in str(exc.value)

    def test_non_text_values_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="text value"):
            _llm({"X-Example-Version": 2})

    def test_a_non_mapping_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="map of header names"):
            _llm(["X-Example-Route"])

    def test_refusal_inside_settings_hides_other_values(self) -> None:
        """A bad name next to a token: the error text names the bad header only."""
        with pytest.raises(ValidationError) as exc:
            Settings.model_validate(
                {"llm": {"openai_default_headers": {"X-Example-Token": _SECRET, "Bad Name": "x"}}}
            )
        assert _SECRET not in str(exc.value)

    def test_token_punctuation_is_allowed(self) -> None:
        headers = {"X-Example_Route.v2": "local", "x-lower": "", "Proxy-Authorization": "p"}
        settings = _llm(headers)
        assert sorted(settings.openai_default_headers) == sorted(headers)
