# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher CLI - Knowledge Graph Platform.

Organized by user intent:

1. Package Manager (docker-like):
   - pull, push (root level)
   - lexicon/ (login, logout, whoami, search, list, info, remove)

2. Runtime (like docker run):
   - serve, run, compose

3. Builder:
   - init, source/, graph/

4. Graph building:
   - graph/ (node, link, template, workflow, package)
"""

import os
import sys
import warnings


def _configure_console_encoding() -> None:
    """Force UTF-8 on Windows stdout/stderr before any Rich Console exists.

    The default Windows console code page is cp1252, which has no mapping
    for routine Rich glyphs (``…`` ``✓`` ``─``) or content we put in
    migration descriptions (``→`` U+2192). Rich's LegacyWindowsTerm
    writes through ``sys.stdout``, so when ``sys.stdout.encoding`` is
    cp1252 and a non-cp1252 char comes through, Python raises
    ``UnicodeEncodeError`` and the whole command aborts mid-render —
    that's the failure mode that crashed ``chaoscypher db migrate status``
    on Windows before this hook existed.

    ``errors="replace"`` makes any future unmappable character degrade to
    a ``?`` glyph instead of aborting. POSIX shells default to UTF-8
    already, so this is a Windows-only concern.

    Best-effort: ``sys.stdout`` / ``sys.stderr`` may have been replaced
    with a non-``TextIOWrapper`` (pytest capture, embedders, redirection
    to a binary pipe). We swallow ``AttributeError`` / ``OSError`` rather
    than refuse to start the CLI over an encoding tweak.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):  # type: ignore[unreachable]
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError, OSError:
            continue


_configure_console_encoding()


# Suppress noisy third-party warnings before any chaoscypher_core /
# langchain import runs (configure_logging in _preconfigure_logging will
# transitively import 184 langchain/langgraph modules — one of which fires
# a PendingDeprecationWarning at import time). The langchain filter has to
# be applied AFTER langchain_core's __init__ runs because
# surface_langchain_deprecation_warnings() forces a "default" action on
# LangChain* categories; we therefore import langchain_core eagerly here
# so we control the order.
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
try:
    import langchain_core  # noqa: F401 — triggers surface_langchain_deprecation_warnings()
    from langchain_core._api.deprecation import (
        LangChainDeprecationWarning,
        LangChainPendingDeprecationWarning,
    )

    warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except Exception:
    # langchain_core not installed (fresh dev env, test harness) — skip.
    pass


def _preconfigure_logging() -> None:
    """Configure structlog with a quiet WARNING default before any submodule import.

    Why this runs unconditionally:
        Before this hook, only the ``mcp`` subcommand pre-configured
        logging; every other command relied on ``context.py:190`` to
        call ``configure_logging`` lazily during ``connect()``. Any
        log line emitted before that call (``settings_file_not_found``,
        ``settings_loaded``, etc.) went through structlog's default
        config, which dumps INFO/DEBUG to stdout — leaking debug lines
        on every ``chaoscypher --help``, ``chaoscypher source list``, etc.

    Why it works:
        ``configure_logging`` has a process-wide idempotency guard
        (``logging._chaoscypher_logging_configured``). The very first
        call wins; ``context.py:190`` becomes a no-op. Operators who
        want verbose logs set ``LOG_LEVEL=INFO`` (or ``DEBUG``) in the
        environment — both paths honour the env var, so the level the
        operator picks is the level they get.

    MCP stdio transport caveat (Bug 15, May 2026):
        The MCP server reserves stdout for JSON-RPC; structlog noise on
        stdout breaks stricter clients. For ``chaoscypher mcp …``, route
        to ``sys.stderr``.

    Best-effort: if the chaoscypher_core import fails (broken install,
    test harness), swallow and let the CLI start normally.
    """
    try:
        from chaoscypher_core.utils.logging import configure_logging

        configure_logging(
            log_level=os.getenv("LOG_LEVEL", "WARNING"),
            stream=sys.stderr if "mcp" in sys.argv[1:] else None,
        )
    except Exception:
        pass


_preconfigure_logging()

import click

from chaoscypher_cli import __version__
from chaoscypher_cli.lazy import LazyGroup


# =============================================================================
# Lazy-loaded commands (heavy imports - only load when executed)
# =============================================================================
# Format: "cmd": ("import.path:attr", "Short help text")
# Help text enables fast --help without loading the command module
LAZY_COMMANDS = {
    # Setup wizard
    "setup": (
        "chaoscypher_cli.commands.setup:setup",
        "Configure LLM provider for extraction and chat",
    ),
    # Package management (docker-like UX)
    "pull": ("chaoscypher_cli.commands.lexicon.pull:pull", "Download a package from Lexicon Hub"),
    "push": ("chaoscypher_cli.commands.lexicon.push:push", "Upload a package to Lexicon Hub"),
    # Runtime commands
    "serve": (
        "chaoscypher_cli.commands.runtime.serve:serve",
        "Start the local API server",
    ),
    "compose": (
        "chaoscypher_cli.commands.compose:compose",
        "Multi-package orchestration and composition",
    ),
    # Builder commands
    "source": (
        "chaoscypher_cli.commands.source:source",
        "Add, list, search, and manage document sources",
    ),
    # Groups
    "graph": ("chaoscypher_cli.commands.graph:graph", "Build and manage knowledge graphs"),
    "lexicon": (
        "chaoscypher_cli.commands.lexicon:lexicon",
        "Lexicon Hub - login, search, manage packages",
    ),
    # AI chat
    "chat": ("chaoscypher_cli.commands.chat:chat", "Chat with AI using your knowledge graph"),
    # Database management
    "db": ("chaoscypher_cli.commands.db:db", "Manage databases (create, list, delete, reset)"),
    # Configuration
    "config": ("chaoscypher_cli.commands.config_cmd:config", "View and manage CLI configuration"),
    "completions": (
        "chaoscypher_cli.commands.completions:completions",
        "Generate shell completion script (bash, zsh, fish)",
    ),
    # MCP server
    "mcp": ("chaoscypher_cli.mcp.command:mcp", "Start MCP server over stdio"),
    # System health
    "health": ("chaoscypher_cli.commands.health:health", "Check system health status"),
    "doctor": (
        "chaoscypher_cli.commands.doctor:doctor",
        "Run a comprehensive system diagnostic sweep",
    ),
    "diagnostics": (
        "chaoscypher_cli.commands.diagnostics:diagnostics",
        "Export diagnostic bundle for bug reports",
    ),
    # Benchmarking
    "benchmark": (
        "chaoscypher_cli.commands.benchmark:benchmark",
        "Run and inspect the extraction benchmark",
    ),
    # Schema migrations
    "upgrade": (
        "chaoscypher_cli.commands.upgrade:upgrade_command",
        "Apply pending Alembic migrations (alembic upgrade head)",
    ),
    # Orchestration template renderer (called by entrypoint.sh)
    "render-orchestration": (
        "chaoscypher_cli.commands.render_orchestration:render_orchestration_command",
        "Render nginx/supervisord/valkey configs from current Pydantic settings",
    ),
}


# Subcommands that bypass the first-run setup gate. These are either
# bootstrap commands (setup, completions), DB-free read-only diagnostics
# (health, doctor, diagnostics, config), or schema-housekeeping that
# must run before any feature command can ever succeed (db, upgrade,
# render-orchestration). `--help` / `--version` are handled separately
# via sys.argv inspection.
_FIRST_RUN_SAFE_SUBCOMMANDS = frozenset(
    {
        "setup",
        "health",
        "doctor",
        "diagnostics",
        "config",
        "db",
        "upgrade",
        "completions",
        "render-orchestration",
    }
)


# Subcommands that are allowed to run while the DB is blocked on a
# tier-2 migration. The migrate subcommand is obviously needed so the
# user can resolve the block; setup/health/diagnostics are read-only
# or don't touch the live schema.
_UPGRADE_SAFE_SUBCOMMANDS = frozenset(
    {
        "db",
        "setup",
        "health",
        "doctor",
        "diagnostics",
        "config",
        "benchmark",
        "upgrade",
        "render-orchestration",
        # `mcp` starts its own degraded maintenance-mode server when the DB is
        # blocked (see chaoscypher_cli/mcp/command.py), so it must NOT be
        # exited(2) here — that drop is exactly the opaque -32000 we're fixing.
        "mcp",
    }
)


def _extract_database_override(argv: list[str]) -> str | None:
    """Find the ``--database``/``-d`` override in a raw arg list.

    Returns the database name the user supplied via a subcommand option,
    or ``None`` if no override is present (in which case callers should
    fall back to ``settings.yaml``'s ``current_database``). Accepts both
    space-separated forms (``--database foo``, ``-d foo``) and
    equals-joined (``--database=foo``, ``-d=foo``); the equals form is
    the only one Click actually generates internally but operators type
    both.

    The guard runs at parent-group time, before Click has parsed the
    subcommand's option schema, so we cannot ask Click "what was
    --database resolved to" — we inspect ``sys.argv`` directly. False
    positives are bounded: a value of literally ``"--database"`` inside
    an unrelated string argument would not match because we look for
    the flag as a token, not a substring.
    """
    for i, tok in enumerate(argv):
        if tok in ("--database", "-d") and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith("--database="):
            return tok.split("=", 1)[1]
        if tok.startswith("-d="):
            return tok.split("=", 1)[1]
    return None


def _upgrade_guard(ctx: click.Context) -> None:
    """Refuse to run DB-touching commands while the upgrade state is blocked.

    Surfaces a clear, actionable message pointing at
    ``chaoscypher db migrate`` instead of letting the downstream
    command crash with a schema mismatch. Tolerant of missing
    infrastructure (no DB yet, migration helpers not importable, etc.)
    so fresh installs and limited environments aren't blocked by a
    gate that can't read its own state.
    """
    # `--help` / `-h` are DB-free no-ops — Click will print help and exit
    # before any handler body runs, so gating them just hides command
    # discovery behind a migration the user can't make sense of yet. Same
    # for shell-completion parses, which Click flags via resilient_parsing.
    if ctx.resilient_parsing or any(arg in ("--help", "-h") for arg in sys.argv[1:]):
        return

    invoked = ctx.invoked_subcommand
    if invoked is None or invoked in _UPGRADE_SAFE_SUBCOMMANDS:
        return
    try:
        from chaoscypher_cli.engine_config import read_current_database
        from chaoscypher_core.database.engine import get_db_path
        from chaoscypher_core.database.migrations.state import get_upgrade_state
    except Exception:
        return  # Can't import — fresh install or dev context. Let the command run.

    try:
        # If the invocation overrides the database via `--database <name>` /
        # `-d <name>`, gate against THAT database — not whatever
        # settings.yaml calls "current". Otherwise `chaoscypher source list
        # --database fresh_db` would refuse to run whenever the default DB
        # is mid-migration, which is exactly the situation a fresh
        # workspace is meant to escape.
        db_name = (
            _extract_database_override(sys.argv[1:])
            or os.environ.get("CHAOSCYPHER_DATABASE")
            or read_current_database()
            or "default"
        )
        db_path = get_db_path(db_name)
        state = get_upgrade_state(db_path)
    except Exception:
        return  # No DB yet or unreadable state; don't block the command.

    if state.ready:
        return

    click.echo(
        click.style(
            "This database is waiting on a schema upgrade before it can be used.",
            fg="yellow",
            bold=True,
        ),
        err=True,
    )
    if state.message:
        click.echo(f"  {state.message}", err=True)
    click.echo(
        "\nRun one of:\n"
        "  chaoscypher db migrate status    — see what's pending\n"
        "  chaoscypher db migrate apply     — apply the pending migrations\n"
        "  chaoscypher db migrate rollback  — restore from pre-upgrade backup",
        err=True,
    )
    ctx.exit(2)


def _stale_cli_yaml_notice(ctx: click.Context) -> None:
    """Warn once (per invocation) about a leftover, no-longer-read cli.yaml.

    The 2026-06 config unification retired ``cli.yaml`` — all configuration
    now lives in ``settings.yaml``. A cli.yaml left behind by an older install
    is silently ignored; this prints a single dim stderr line so the operator
    knows the file is dead and can delete it. Stateless: the note prints while
    the file exists and disappears once it's removed.

    Bypassed for ``--help`` / shell-completion parses (mirroring the other
    guards) so command discovery isn't cluttered.
    """
    if ctx.resilient_parsing or any(arg in ("--help", "-h", "--version") for arg in sys.argv[1:]):
        return
    try:
        from chaoscypher_cli.utils.paths import get_config_dir

        stale = get_config_dir() / "cli.yaml"
        if not stale.exists():
            return
    except Exception:
        return  # best-effort UX, never block the CLI

    click.echo(
        click.style(
            f"chaoscypher: note: {stale} is no longer read and is ignored "
            "(config unification); your settings live in settings.yaml — the "
            "old file can be deleted.",
            dim=True,
        ),
        err=True,
    )


def _first_run_gate(ctx: click.Context) -> None:
    """Auto-route a fresh `pipx install` user into the setup wizard.

    A user with no engine configuration in ``settings.yaml`` (and no
    ``CHAOSCYPHER_LLM_PROVIDER`` env override) will otherwise
    hit "LLM Required" the moment they run ``chaoscypher source add
    doc.pdf`` — confusing first-run UX. When we detect that signature,
    interactively offer to run ``chaoscypher setup`` first; in
    non-interactive mode, print an actionable message and exit 2.

    Bypassed for:
        * ``--help`` / ``-h`` / ``--version`` (let Click render those).
        * Shell-completion parses (``ctx.resilient_parsing``).
        * Bootstrap / read-only subcommands (see
          ``_FIRST_RUN_SAFE_SUBCOMMANDS``).
        * Any invocation where setup is already complete (``settings.yaml``
          records ``setup_completed`` / an ``llm.chat_provider``, or the
          ``CHAOSCYPHER_LLM_PROVIDER`` env var is set).

    Best-effort: if the config helpers can't import (broken install,
    test harness), let the CLI continue — the gate is friendly UX, not
    a correctness invariant.
    """
    if ctx.resilient_parsing or any(arg in ("--help", "-h", "--version") for arg in sys.argv[1:]):
        return

    invoked = ctx.invoked_subcommand
    if invoked is None or invoked in _FIRST_RUN_SAFE_SUBCOMMANDS:
        return

    try:
        from chaoscypher_cli.engine_config import is_setup_completed, settings_yaml_path
    except Exception:
        return

    try:
        if is_setup_completed():
            return
        settings_path = settings_yaml_path()
    except Exception:
        return

    # First run signature confirmed.
    is_tty = sys.stdin.isatty() and sys.stderr.isatty()

    click.echo(
        click.style(
            "It looks like this is your first time running Chaos Cypher.",
            fg="cyan",
            bold=True,
        ),
        err=True,
    )
    click.echo(
        "No engine configuration was found at "
        f"{settings_path}, and no LLM provider has been set up yet.",
        err=True,
    )
    click.echo("", err=True)

    if not is_tty:
        click.echo(
            "Run `chaoscypher setup` to configure an LLM provider before "
            f"using `chaoscypher {invoked}`.",
            err=True,
        )
        ctx.exit(2)

    if not click.confirm(
        click.style("Run `chaoscypher setup` now?", fg="cyan"),
        default=True,
        err=True,
    ):
        click.echo(
            f"Skipped. Run `chaoscypher setup` when you're ready, then re-run `chaoscypher {invoked}`.",
            err=True,
        )
        ctx.exit(2)

    # Hand control to the setup wizard. We exit afterwards rather than
    # continue with the original subcommand — the user's original argv
    # may reference a file that doesn't exist yet, a DB that hasn't been
    # created, etc., and the setup wizard's "Next steps" block already
    # shows them what to run next.
    from chaoscypher_cli.commands.setup import setup as setup_cmd

    ctx.invoke(setup_cmd)
    ctx.exit(0)


@click.group(
    cls=LazyGroup,
    lazy_subcommands=LAZY_COMMANDS,
    context_settings={"max_content_width": 120},
)
@click.version_option(version=__version__, prog_name="chaoscypher")
@click.pass_context
def main(ctx: click.Context) -> None:
    """Chaos Cypher CLI - Knowledge Graph Platform."""
    _upgrade_guard(ctx)
    _first_run_gate(ctx)
    _stale_cli_yaml_notice(ctx)


if __name__ == "__main__":
    main()
