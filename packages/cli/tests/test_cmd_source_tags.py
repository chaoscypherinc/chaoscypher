# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Document tags and summaries on the command line.

``source add --tag/--summary`` (a file, and ``--site`` with a later re-add),
``source list --tag/--match`` with its Tags and Summary columns, ``source tag``
and ``source search --tag/--match``. A real CLI context on the test's data
directory, as ``test_cmd_source_site.py`` builds it, and the fake web site
behind the real fetcher: no network, no model (``--skip-extract
--skip-embeddings``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from chaoscypher_cli.commands.source import source as source_group
from chaoscypher_cli.commands.source.add import add
from chaoscypher_cli.commands.source.list import list_files
from chaoscypher_cli.commands.source.search import search
from chaoscypher_cli.commands.source.site import site as site_group
from chaoscypher_cli.commands.source.tag import tag
from chaoscypher_core.services.sources.tags import TagMatch, source_tag_names


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext
    from chaoscypher_core.testing.fake_web import FakeSite


ROOT = "https://docs.example.com"
_MD = "text/markdown; charset=utf-8"
_BODY = "This page explains one part of the product in enough words to import. "
_OFFLINE = ["--skip-extract", "--skip-embeddings"]
REPLACE_REFUSAL = "Error: replace sets the whole list; it cannot be combined with add or remove"
BAD_TAG_REFUSAL = (
    "Error: tag \"Work Docs\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
    "starting with a letter or digit, at most 64 characters"
)
_COMMANDS: dict[str, Any] = {
    "add": add,
    "list": list_files,
    "search": search,
    "site": site_group,
    "tag": tag,
}


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Iterator[CLIContext]:
    """A real CLI context on the test's data directory, with no politeness delay."""
    from chaoscypher_cli.context import CLIContext

    monkeypatch.setenv("WEB__CRAWL_DELAY_SECONDS", "0")
    context = CLIContext(database_name="default")
    context.connect()
    context.settings.web.crawl_delay_seconds = 0.0
    try:
        yield context
    finally:
        context.disconnect()


def _invoke(ctx: CLIContext, args: list[str]) -> Any:
    wide = Console(width=240)
    with (
        patch("chaoscypher_cli.context.get_context", return_value=ctx),
        patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx),
        patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx),
        patch("chaoscypher_cli.commands.source.site.get_context", return_value=ctx),
        patch("chaoscypher_cli.commands.source.list.console", wide),
        patch("chaoscypher_cli.commands.source.search.console", wide),
        patch(
            "chaoscypher_core.operations.sources.site_import_handler.get_settings"
        ) as handler_settings,
        patch(
            "chaoscypher_core.operations.sources.url_fetch_handler.get_settings"
        ) as fetch_settings,
    ):
        from chaoscypher_core.app_config import get_settings

        app = get_settings()
        app.web.crawl_delay_seconds = 0.0
        handler_settings.return_value = app
        fetch_settings.return_value = app
        return CliRunner().invoke(_COMMANDS[args[0]], args[1:], catch_exceptions=False)


def _json(output: str) -> Any:
    """The JSON a command printed, after any log lines the run wrote first."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(("{", "[")))
    return json.loads("\n".join(lines[start:]))


def _last_line(output: str) -> str:
    return output.strip().splitlines()[-1]


def _doc(tmp_path: Path, name: str, words: str) -> Path:
    path = tmp_path / f"{name}.txt"
    path.write_text(f"{words}\n\nA document long enough to be read and indexed for search.\n")
    return path


def _added(ctx: CLIContext, doc: Path, *extra: str) -> str:
    result = _invoke(ctx, ["add", str(doc), "--json", *_OFFLINE, *extra])
    assert result.exit_code == 0, result.output
    return str(_json(result.output)["file_id"])


def _serve(site: FakeSite, pages: dict[str, str]) -> None:
    site.routes.clear()
    site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{name.title()}]({ROOT}/{name}.md)\n" for name in pages)
    site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type=_MD)
    for name, body in pages.items():
        site.add(f"{ROOT}/{name}.md", body, content_type=_MD)


def _site_pages(ctx: CLIContext) -> list[str]:
    sites, total = ctx.storage_adapter.list_sites(ctx.database_name, page=1, page_size=10)
    assert total == 1
    return [p["id"] for p in ctx.storage_adapter.list_site_pages(sites[0]["id"], ctx.database_name)]


def test_add_with_tags_and_summary(ctx: CLIContext, tmp_path: Path) -> None:
    doc = _doc(tmp_path, "doc-a", "Alpha notes.")

    source_id = _added(ctx, doc, "--tag", "Alpha", "--tag", "beta", "--summary", "A short note.")

    assert source_tag_names(ctx.storage_adapter, source_id) == ["alpha", "beta"]
    row = ctx.storage_adapter.get_source(source_id, ctx.database_name)
    assert row is not None and row["summary"] == "A short note."


def test_add_site_with_tags_tags_every_page(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": f"# Intro\n\n{_BODY}", "install": f"# Install\n\n{_BODY}"})

    first = _invoke(ctx, ["add", ROOT, "--site", "--yes", "--tag", "alpha", *_OFFLINE])

    assert first.exit_code == 0, first.output
    pages = _site_pages(ctx)
    assert len(pages) == 2
    assert all(source_tag_names(ctx.storage_adapter, p) == ["alpha"] for p in pages)

    again = _invoke(ctx, ["add", ROOT, "--site", "--yes", "--tag", "beta", *_OFFLINE])

    assert again.exit_code == 0, again.output
    assert "its pages now carry the tags alpha, beta" in again.output
    assert all(source_tag_names(ctx.storage_adapter, p) == ["alpha", "beta"] for p in pages)
    sites, _total = ctx.storage_adapter.list_sites(ctx.database_name, page=1, page_size=10)
    # Stored on the site, so a later refresh tags its new pages too.
    assert sites[0]["options"]["tags"] == ["alpha", "beta"]


def test_site_refresh_tags_a_new_page_with_the_stored_tags(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"intro": f"# Intro\n\n{_BODY}"})
    first = _invoke(ctx, ["add", ROOT, "--site", "--yes", "--tag", "alpha", *_OFFLINE])
    assert first.exit_code == 0, first.output

    _serve(fake_site, {"intro": f"# Intro\n\n{_BODY}", "added": f"# Added\n\n{_BODY}"})
    refreshed = _invoke(ctx, ["site", "refresh", ROOT, *_OFFLINE])

    assert refreshed.exit_code == 0, refreshed.output
    pages = _site_pages(ctx)
    assert len(pages) == 2
    assert all(source_tag_names(ctx.storage_adapter, p) == ["alpha"] for p in pages)


def test_add_site_refuses_a_summary(ctx: CLIContext) -> None:
    result = _invoke(ctx, ["add", ROOT, "--site", "--yes", "--summary", "One line.", *_OFFLINE])

    assert result.exit_code == 1
    assert "Error: a site's pages do not share one summary" in result.output


def test_add_bad_tag_refused(ctx: CLIContext, tmp_path: Path) -> None:
    result = _invoke(
        ctx, ["add", str(_doc(tmp_path, "doc-a", "Alpha.")), *_OFFLINE, "--tag", "Work Docs"]
    )

    assert result.exit_code == 1
    assert _last_line(result.output) == BAD_TAG_REFUSAL
    assert ctx.storage_adapter.count_sources(database_name=ctx.database_name) == 0


def test_add_markup_like_bad_tag_prints_as_written(ctx: CLIContext, tmp_path: Path) -> None:
    result = _invoke(
        ctx, ["add", str(_doc(tmp_path, "doc-a", "Alpha.")), *_OFFLINE, "--tag", "[/x]"]
    )

    assert result.exit_code == 1
    assert _last_line(result.output).startswith('Error: tag "[/x]" is not allowed')


def test_add_resume_with_labels_refused(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."))
    file_id = "if_" + "a" * 12  # the resume form of an argument

    for args in ([file_id, "--tag", "alpha"], ["--resume", "--summary", "One line."]):
        result = _invoke(ctx, ["add", *args, *_OFFLINE])
        assert result.exit_code == 1
        assert _last_line(result.output) == (
            "Error: --tag and --summary label a new upload; "
            "'chaoscypher source tag' changes an existing document's tags."
        )
    assert source_tag_names(ctx.storage_adapter, source_id) == []


def test_list_filters_by_tag_and_shows_tags_and_summary(ctx: CLIContext, tmp_path: Path) -> None:
    a = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."), "--tag", "alpha", "--summary", "About A.")
    b = _added(ctx, _doc(tmp_path, "doc-b", "Both."), "--tag", "alpha", "--tag", "beta")
    c = _added(ctx, _doc(tmp_path, "doc-c", "None."))

    def ids(*args: str) -> set[str]:
        result = _invoke(ctx, ["list", "--format", "json", *args])
        assert result.exit_code == 0, result.output
        return {row["id"] for row in _json(result.output)}

    assert ids() == {a, b, c}
    assert ids("--tag", "alpha") == {a, b}
    assert ids("--tag", "alpha", "--tag", "beta") == {a, b}
    assert ids("--tag", "alpha", "--tag", "beta", "--match", "all") == {b}
    assert ids("--tag", "gamma") == set()

    rows = _json(_invoke(ctx, ["list", "--format", "json"]).output)
    by_id = {row["id"]: row for row in rows}
    assert by_id[a]["tags"] == ["alpha"] and by_id[a]["summary"] == "About A."
    assert by_id[b]["tags"] == ["alpha", "beta"] and by_id[b]["summary"] is None
    assert by_id[c]["tags"] == []

    table = _invoke(ctx, ["list", "--tag", "alpha"])
    assert table.exit_code == 0, table.output
    assert "Tags" in table.output and "Summary" in table.output
    assert "alpha, beta" in table.output and "About A." in table.output
    assert "doc-c" not in table.output


def test_list_refuses_a_bad_match(ctx: CLIContext) -> None:
    result = _invoke(ctx, ["list", "--tag", "alpha", "--match", "most"])

    assert result.exit_code != 0
    assert "most" in result.output


def test_tag_add_remove_replace_clear(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."))

    added = _invoke(ctx, ["tag", source_id, "--add", "Alpha", "--add", "beta"])
    assert added.exit_code == 0, added.output
    assert f"Tags of {source_id}: alpha, beta" in added.output

    removed = _invoke(ctx, ["tag", source_id, "--remove", "alpha", "--remove", "gamma"])
    assert removed.exit_code == 0, removed.output
    assert f"Tags of {source_id}: beta" in removed.output

    replaced = _invoke(ctx, ["tag", source_id, "--replace", "gamma", "--replace", "delta"])
    assert replaced.exit_code == 0, replaced.output
    assert f"Tags of {source_id}: delta, gamma" in replaced.output

    cleared = _invoke(ctx, ["tag", source_id, "--clear"])
    assert cleared.exit_code == 0, cleared.output
    assert f"Tags of {source_id}: none" in cleared.output
    assert source_tag_names(ctx.storage_adapter, source_id) == []


def test_tag_with_no_option_prints_the_tags(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."), "--tag", "beta", "--tag", "alpha")

    result = _invoke(ctx, ["tag", source_id])

    assert result.exit_code == 0, result.output
    assert _last_line(result.output) == f"Tags of {source_id}: alpha, beta"
    assert source_tag_names(ctx.storage_adapter, source_id) == ["alpha", "beta"]


def test_tag_clear_with_replace_refused(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."), "--tag", "alpha")

    result = _invoke(ctx, ["tag", source_id, "--clear", "--replace", "beta"])

    assert result.exit_code == 1
    assert result.output.strip().splitlines()[-1] == (
        "Error: --clear cannot be combined with --replace"
    )
    assert "Usage:" not in result.output
    assert source_tag_names(ctx.storage_adapter, source_id) == ["alpha"]


def test_tag_replace_with_add_refused(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."), "--tag", "alpha")

    for args in (["--replace", "beta", "--add", "gamma"], ["--clear", "--remove", "alpha"]):
        result = _invoke(ctx, ["tag", source_id, *args])
        assert result.exit_code == 1
        assert _last_line(result.output) == REPLACE_REFUSAL
    assert source_tag_names(ctx.storage_adapter, source_id) == ["alpha"]


def test_tag_bad_name_refused(ctx: CLIContext, tmp_path: Path) -> None:
    source_id = _added(ctx, _doc(tmp_path, "doc-a", "Alpha."))

    result = _invoke(ctx, ["tag", source_id, "--add", " Work Docs "])

    assert result.exit_code == 1
    assert _last_line(result.output) == BAD_TAG_REFUSAL
    assert source_tag_names(ctx.storage_adapter, source_id) == []


def test_tag_unknown_source_refused(ctx: CLIContext) -> None:
    result = _invoke(ctx, ["tag", "if_nosuchsource", "--add", "alpha"])

    assert result.exit_code == 1
    assert _last_line(result.output).startswith("Error: ")


def test_search_with_tag_returns_only_tagged(
    ctx: CLIContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hybrid search with vector search off: its keyword arm alone, over chunks and nodes.
    monkeypatch.setattr(
        "chaoscypher_cli.commands.source.search._get_embedding_callback", lambda _ctx: None
    )
    tagged = _added(ctx, _doc(tmp_path, "doc-a", "Zebrafish habitats."), "--tag", "alpha")
    _added(ctx, _doc(tmp_path, "doc-b", "Zebrafish habitats."), "--tag", "beta")

    def sources(*args: str) -> set[str]:
        result = _invoke(ctx, ["search", "zebrafish", "-f", "json", *args])
        assert result.exit_code == 0, result.output
        if "No results found." in result.output:
            return set()
        return {hit["properties"]["source_id"] for hit in _json(result.output)}

    assert len(sources()) == 2
    assert sources("--tag", "alpha") == {tagged}
    assert sources("--tag", "alpha", "--tag", "beta", "--match", "all") == set()
    nothing = _invoke(ctx, ["search", "zebrafish", "--tag", "gamma"])
    assert nothing.exit_code == 0, nothing.output
    assert "No results found." in nothing.output


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_search_passes_the_tag_scope_to_every_mode(mode: str) -> None:
    ctx = MagicMock()
    hits = AsyncMock(return_value=[])
    setattr(ctx.search_repository, f"{mode}_search", hits)
    ctx.embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.1]))

    with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
        result = CliRunner().invoke(
            search, ["q", "--mode", mode, "--tag", "Alpha", "--tag", "beta", "--match", "all"]
        )

    assert result.exit_code == 0, result.output
    assert hits.await_args.kwargs["tags"] == ["alpha", "beta"]
    assert hits.await_args.kwargs["tag_match"] is TagMatch.ALL


def test_help_lists_tag_and_summarize() -> None:
    result = CliRunner().invoke(source_group, ["--help"])

    assert result.exit_code == 0, result.output
    lines = [" ".join(line.split()) for line in result.output.splitlines()]
    assert any(line.startswith("tag Add, remove or replace a document's tags") for line in lines), (
        result.output
    )
    assert any(
        line.startswith("summarize Write each document's one-line summary") for line in lines
    ), result.output
    tag_help = CliRunner().invoke(tag, ["--help"])
    for option in ("--add", "--remove", "--replace", "--clear", "--database"):
        assert option in tag_help.output
