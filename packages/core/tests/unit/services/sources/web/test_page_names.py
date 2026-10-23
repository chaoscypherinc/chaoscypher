# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``page_names``: the one rule turning a fetched page's title into a stored title and a filename.

The worker's ``fetch_url`` handler, the CLI's ``upload_url``, the MCP server's
URL import and the site import all name a page through it, so a page gets the
same title and filename whichever surface imported it.
"""

from __future__ import annotations

from types import SimpleNamespace

from chaoscypher_core.services.sources.web import page_names
from chaoscypher_core.settings import PathSettings


def _settings(max_length: int = 80) -> SimpleNamespace:
    return SimpleNamespace(paths=SimpleNamespace(max_filename_length=max_length))


def test_title_keeps_words_and_the_stem_joins_them() -> None:
    names = page_names("Getting Started: Install", _settings())
    assert names.title == "Getting Started: Install"
    assert names.stem == "Getting_Started_Install"


def test_control_and_fence_characters_leave_the_title() -> None:
    names = page_names("<script>Docs\x00\nHome</script>", _settings())
    assert names.title == "scriptDocs Home/script"


def test_an_empty_title_falls_back() -> None:
    names = page_names("", _settings())
    assert names.title == "Untitled Page"
    assert names.stem == "Untitled_Page"


def test_a_title_of_only_fence_characters_falls_back_after_cleaning() -> None:
    assert page_names("<>", _settings()).title == "Untitled Page"


def test_the_caller_names_its_own_title_fallback() -> None:
    names = page_names("", _settings(), fallback_title="https://example.com/a")
    assert names.title == "https://example.com/a"
    assert names.stem == "httpsexamplecoma"


def test_punctuation_only_stem_falls_back_to_web_import() -> None:
    names = page_names("!!!", _settings())
    assert names.title == "!!!"
    assert names.stem == "web_import"


def test_the_stem_is_capped_by_the_paths_setting() -> None:
    assert len(page_names("A" * 200, _settings(12)).stem) == 12


def test_settings_without_paths_use_the_setting_default() -> None:
    default = PathSettings.model_fields["max_filename_length"].default
    assert len(page_names("B" * 500, object()).stem) == default
