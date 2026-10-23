# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``source add URL --site`` and ``source site list|show|refresh|delete``, end to end.

A real CLI context on a temporary data directory and the fake web site
(``chaoscypher_core.testing.fake_web``) behind the real, hardened fetcher:
no network. Pages run the CLI's own pipeline with ``--skip-extract
--skip-embeddings`` (no model), so a page ends committed with its chunks.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from chaoscypher_cli.commands.source.add import add
from chaoscypher_cli.commands.source.site import site as site_group
from chaoscypher_core.models import SiteStatus, SourceStatus


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext
    from chaoscypher_core.testing.fake_web import FakeSite


ROOT = "https://docs.example.com"
_MD = "text/markdown; charset=utf-8"
_BODY = "This page explains one part of the product in enough words to import. "
_OFFLINE = ["--skip-extract", "--skip-embeddings"]


def _page(title: str) -> str:
    return f"# {title}\n\n{_BODY}{title}.\n"


def _serve(site: FakeSite, pages: dict[str, str]) -> None:
    site.routes.clear()
    site.page(ROOT, "Example Docs", [])
    links = "".join(f"- [{name.title()}]({ROOT}/{name}.md)\n" for name in pages)
    site.add(f"{ROOT}/llms.txt", f"# Example Docs\n\n{links}", content_type=_MD)
    for name, body in pages.items():
        site.add(f"{ROOT}/{name}.md", body, content_type=_MD)


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


def _invoke(ctx: CLIContext, args: list[str], *, user_input: str | None = None) -> Any:
    with (
        patch("chaoscypher_cli.context.get_context", return_value=ctx),
        patch("chaoscypher_cli.commands.source.site.get_context", return_value=ctx),
        # Wide enough that no table cell wraps an address the assertions read.
        patch("chaoscypher_cli.commands.source.site.console", Console(width=240)),
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
        # The commands themselves: the lazy ``source`` group reads sys.argv.
        command = add if args[0] == "add" else site_group
        return CliRunner().invoke(command, args[1:], input=user_input, catch_exceptions=False)


def _site(ctx: CLIContext) -> dict[str, Any]:
    sites, total = ctx.storage_adapter.list_sites(ctx.database_name, page=1, page_size=10)
    assert total == 1
    return dict(sites[0])


def _pages(ctx: CLIContext, site_id: str) -> dict[str, dict[str, Any]]:
    return {
        str(p["origin_url"]): dict(p)
        for p in ctx.storage_adapter.list_site_pages(site_id, ctx.database_name)
    }


# --------------------------------------------------------------------------- add --site


def test_add_site_shows_what_it_found_then_imports_every_page(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install"), "api": _page("API")})

    result = _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])

    assert result.exit_code == 0, result.output
    assert "Example Docs (https://docs.example.com/): 3 pages found by llms.txt." in result.output
    assert "Fetched 3 pages: 3 new." in result.output
    assert f"[3/3] OK {ROOT}/api.md" in result.output
    assert "Example Docs: 3 of 3 pages done" in result.output
    site = _site(ctx)
    assert site["status"] == SiteStatus.IMPORTED and site["discovery_method"] == "llms_txt"
    # A site's pages are never parked one by one for domain confirmation.
    assert site["options"]["auto_confirm"] is True
    pages = _pages(ctx, site["id"])
    assert sorted(pages) == [f"{ROOT}/{n}.md" for n in ("api", "install", "intro")]
    # Without extraction the CLI pipeline still commits each page (its chunks).
    assert {p["status"] for p in pages.values()} == {SourceStatus.COMMITTED}


def test_add_site_asks_first_and_a_no_imports_nothing(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install")})

    result = _invoke(ctx, ["add", ROOT, "--site", *_OFFLINE], user_input="n\n")

    assert result.exit_code == 0, result.output
    assert "2 pages found by llms.txt" in result.output
    assert "Import every page as one site?" in result.output
    assert "Cancelled" in result.output
    assert ctx.storage_adapter.list_sites(ctx.database_name, page=1, page_size=10)[1] == 0


def test_add_site_refuses_an_unsafe_address(ctx: CLIContext, fake_site: FakeSite) -> None:
    result = _invoke(
        ctx, ["add", "https://intranet.example.com/docs", "--site", "--yes", *_OFFLINE]
    )

    assert result.exit_code == 1
    assert "blocked by the URL safety policy" in result.output
    assert fake_site.requested == []


def test_add_site_says_when_nothing_is_found(ctx: CLIContext, fake_site: FakeSite) -> None:
    fake_site.add(f"{ROOT}/robots.txt", "User-agent: *\nDisallow: /\n", content_type="text/plain")

    result = _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])

    assert result.exit_code == 1
    assert "No pages found at https://docs.example.com" in result.output


def test_add_site_takes_one_address(ctx: CLIContext) -> None:
    result = _invoke(ctx, ["add", ROOT, f"{ROOT}/other", "--site", "--yes", *_OFFLINE])

    assert result.exit_code == 1
    assert "--site takes one site address" in result.output


# --------------------------------------------------------------------------- site commands


def test_site_list_and_show(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)

    listed = _invoke(ctx, ["site", "list"])
    shown = _invoke(ctx, ["site", "show", "example docs"])

    assert listed.exit_code == 0, listed.output
    assert "Example Docs" in listed.output and "2 of 2" in listed.output
    assert shown.exit_code == 0, shown.output
    assert site["id"] in shown.output and "Found by: llms.txt" in shown.output
    assert f"{ROOT}/intro.md" in shown.output and f"{ROOT}/install.md" in shown.output


def test_site_refresh_reimports_only_what_changed_and_marks_the_gone(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    _serve(
        fake_site,
        {"same": _page("Same"), "edited": _page("Edited"), "removed": _page("Removed")},
    )
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    before = _pages(ctx, site["id"])

    _serve(
        fake_site,
        {"same": _page("Same"), "edited": _page("Edited, with more words"), "added": _page("A")},
    )
    result = _invoke(ctx, ["site", "refresh", ROOT, *_OFFLINE])

    assert result.exit_code == 0, result.output
    assert "Checking Example Docs for updates." in result.output
    assert "Checked 3 pages: 1 new, 1 changed, 1 unchanged, 1 gone from the site." in (
        result.output
    )
    after = _pages(ctx, site["id"])
    assert after[f"{ROOT}/edited.md"]["id"] == before[f"{ROOT}/edited.md"]["id"]
    assert after[f"{ROOT}/edited.md"]["status"] == SourceStatus.COMMITTED
    assert after[f"{ROOT}/removed.md"]["site_gone_at"] is not None
    shown = _invoke(ctx, ["site", "show", site["id"]])
    assert "(gone from site)" in shown.output


def test_site_refresh_waits_while_a_run_is_under_way(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    ctx.storage_adapter.update_site(site["id"], ctx.database_name, {"status": "refreshing"})

    result = _invoke(ctx, ["site", "refresh", site["id"], *_OFFLINE])

    assert result.exit_code == 1
    assert "already being imported or checked" in result.output


def test_site_delete_keeps_the_pages_unless_asked(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    page_ids = [p["id"] for p in _pages(ctx, site["id"]).values()]

    kept = _invoke(ctx, ["site", "delete", ROOT, "--yes"])

    assert kept.exit_code == 0, kept.output
    assert "its 2 pages stay as sources" in kept.output
    assert all(ctx.storage_adapter.get_source(i, ctx.database_name) for i in page_ids)

    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    again = _site(ctx)
    new_ids = [p["id"] for p in _pages(ctx, again["id"]).values()]
    dropped = _invoke(ctx, ["site", "delete", again["id"], "--delete-pages", "--yes"])

    assert dropped.exit_code == 0, dropped.output
    assert not any(ctx.storage_adapter.get_source(i, ctx.database_name) for i in new_ids)


def _go_stale(ctx: CLIContext, site_id: str, status: str) -> None:
    """The run's process was killed: its status stayed, its start is past the timeout."""
    from datetime import UTC, datetime, timedelta

    from chaoscypher_core.operations.sources.site_runs import run_stale_after_seconds

    old = datetime.now(UTC) - timedelta(seconds=run_stale_after_seconds() + 60)
    ctx.storage_adapter.update_site(
        site_id, ctx.database_name, {"status": status, "run_started_at": old}
    )


def test_site_refresh_runs_a_site_whose_run_went_stale(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    _go_stale(ctx, site["id"], SiteStatus.REFRESHING)

    result = _invoke(ctx, ["site", "refresh", site["id"], *_OFFLINE])

    assert result.exit_code == 0, result.output
    assert _site(ctx)["status"] == SiteStatus.IMPORTED


def test_add_site_twice_points_to_refresh(ctx: CLIContext, fake_site: FakeSite) -> None:
    """One check for every surface: the same scope in the same database is one site."""
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)

    again = _invoke(ctx, ["add", f"{ROOT}/index.html", "--site", "--yes", *_OFFLINE])

    assert again.exit_code == 1, again.output
    assert "already imported" in again.output
    assert f"chaoscypher source site refresh {site['id']}" in again.output
    assert _site(ctx)["id"] == site["id"]  # still one site


def test_site_refresh_uses_the_sites_stored_options(ctx: CLIContext, fake_site: FakeSite) -> None:
    """A refresh runs changed pages with the import's choices; a flag passed overrides one."""
    from chaoscypher_cli.sources.pipeline import PipelineResult, SourcePipeline

    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", "--quick", *_OFFLINE])
    _serve(fake_site, {"intro": _page("Intro, edited")})
    seen: list[dict[str, Any]] = []

    def _record(_self: Any, *, file_id: str, **kwargs: Any) -> PipelineResult:
        seen.append(kwargs)
        return PipelineResult(file_id=file_id, filename="p.md", success=True, status="committed")

    with patch.object(SourcePipeline, "run", _record):
        plain = _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings"])

        assert plain.exit_code == 0, plain.output
        assert seen[0]["skip_extract"] is True  # stored: imported with --skip-extract
        assert seen[0]["extraction_depth"] == "quick"  # stored: imported with --quick

        _serve(fake_site, {"intro": _page("Intro, edited again")})
        deeper = _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--full"])

    assert deeper.exit_code == 0, deeper.output
    assert seen[1]["extraction_depth"] == "full"  # passed: overrides the stored depth
    assert seen[1]["skip_extract"] is True


def test_site_refresh_remembers_the_pages_left_out(ctx: CLIContext, fake_site: FakeSite) -> None:
    """--exclude replaces the site's list, a plain refresh keeps it, --include-all clears it."""
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    _serve(fake_site, {"intro": _page("Intro"), "faq": _page("FAQ"), "api": _page("API")})

    first = _invoke(
        ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--exclude", f"{ROOT}/faq/"]
    )
    assert first.exit_code == 0, first.output
    assert sorted(_pages(ctx, site["id"])) == [f"{ROOT}/api.md", f"{ROOT}/intro.md"]
    assert _site(ctx)["excluded_pages"] == [f"{ROOT}/faq"]

    again = _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings"])
    assert again.exit_code == 0, again.output
    assert f"{ROOT}/faq.md" not in _pages(ctx, site["id"])
    shown = _invoke(ctx, ["site", "show", ROOT])
    assert "1 page left out" in shown.output and f"{ROOT}/faq" in shown.output

    back = _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--include-all"])
    assert back.exit_code == 0, back.output
    assert f"{ROOT}/faq.md" in _pages(ctx, site["id"])
    assert _site(ctx)["excluded_pages"] == []

    both = _invoke(ctx, ["site", "refresh", ROOT, "--include-all", "--exclude", f"{ROOT}/api"])
    assert both.exit_code != 0
    assert "--include-all" in both.output


def test_site_refresh_refuses_an_address_that_names_no_page(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    """A typo is refused plainly; it never empties the list and brings every page back."""
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--exclude", f"{ROOT}/faq"])

    refused = _invoke(ctx, ["site", "refresh", ROOT, "--exclude", "docs.example.com/intro"])

    assert refused.exit_code == 1
    assert "Error:" in refused.output and "docs.example.com/intro" in refused.output
    assert "Traceback" not in refused.output
    assert _site(ctx)["excluded_pages"] == [f"{ROOT}/faq"]


def test_site_refresh_names_the_pages_it_brings_back_and_marks_the_left_out(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    _serve(fake_site, {"intro": _page("Intro"), "api": _page("API")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--exclude", f"{ROOT}/api"])

    shown = _invoke(ctx, ["site", "show", ROOT])
    [api_line] = [line for line in shown.output.splitlines() if f"{ROOT}/api.md" in line]
    assert "left out, not checked for updates" in api_line
    [intro_line] = [line for line in shown.output.splitlines() if f"{ROOT}/intro.md" in line]
    assert "left out" not in intro_line

    back = _invoke(ctx, ["site", "refresh", ROOT, "--skip-embeddings", "--include-all"])
    assert back.exit_code == 0, back.output
    assert f"Bringing back 1 page: {ROOT}/api" in back.output
    assert "deleted from the site are imported again" in back.output


def test_site_show_names_a_few_left_out_pages(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    many = [f"{ROOT}/p{n}" for n in range(8)]
    ctx.storage_adapter.update_site(_site(ctx)["id"], ctx.database_name, {"excluded_pages": many})

    shown = _invoke(ctx, ["site", "show", ROOT])

    limit = ctx.settings.web.site_pages_named_max
    assert f"8 pages left out: {', '.join(many[:limit])} and {8 - limit} more" in shown.output


def test_site_delete_with_its_pages_leaves_the_list_alone(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    from chaoscypher_cli.sources import deletion

    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    calls: list[dict[str, Any]] = []
    real = deletion.delete_source_cascade

    def _record(c: Any, source_id: str, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return real(c, source_id, **kwargs)

    with patch.object(deletion, "delete_source_cascade", _record):
        done = _invoke(ctx, ["site", "delete", ROOT, "--delete-pages", "--yes"])

    assert done.exit_code == 0, done.output
    assert calls == [{"leave_out_of_site": False}]


def test_site_delete_waits_while_a_run_is_under_way(ctx: CLIContext, fake_site: FakeSite) -> None:
    _serve(fake_site, {"intro": _page("Intro")})
    _invoke(ctx, ["add", ROOT, "--site", "--yes", *_OFFLINE])
    site = _site(ctx)
    ctx.storage_adapter.update_site(site["id"], ctx.database_name, {"status": "importing"})

    refused = _invoke(ctx, ["site", "delete", site["id"], "--yes"])

    assert refused.exit_code == 1
    assert "being imported or checked" in refused.output
    assert ctx.storage_adapter.get_site(site["id"], ctx.database_name) is not None

    _go_stale(ctx, site["id"], SiteStatus.IMPORTING)
    assert _invoke(ctx, ["site", "delete", site["id"], "--yes"]).exit_code == 0


def test_skip_duplicates_never_takes_a_page_from_its_site(
    ctx: CLIContext, fake_site: FakeSite
) -> None:
    """``--skip-duplicates`` leaves the user's own source alone and imports the page anyway."""
    _serve(fake_site, {"intro": _page("Intro"), "install": _page("Install")})
    mine = _invoke(ctx, ["add", f"{ROOT}/intro.md", *_OFFLINE])
    assert mine.exit_code == 0, mine.output
    own, total = ctx.storage_adapter.list_sources(page=1, page_size=10)
    assert total == 1

    result = _invoke(ctx, ["add", ROOT, "--site", "--yes", "--skip-duplicates", *_OFFLINE])

    assert result.exit_code == 0, result.output
    site = _site(ctx)
    pages = _pages(ctx, site["id"])
    assert sorted(pages) == [f"{ROOT}/install.md", f"{ROOT}/intro.md"]
    assert pages[f"{ROOT}/intro.md"]["id"] != own[0]["id"]
    kept = ctx.storage_adapter.get_source(own[0]["id"], ctx.database_name)
    assert kept is not None and kept["site_id"] is None


def test_an_unknown_site_is_refused(ctx: CLIContext) -> None:
    result = _invoke(ctx, ["site", "show", "nothing like it"])

    assert result.exit_code == 1
    assert "No site matches" in result.output
