# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Drive an MCP benchmark suite through an agent CLI, one task per isolated session.

The harness track measures the client's own model. This driver keeps that
honest: the run is created and finished here, directly on the bridge, so the
model never sees the suite's arguments or results, and each task runs in a
fresh client session started in an empty directory with only the ChaosCypher
MCP server loaded. The bridge returns no verdicts until ``finish`` and refuses
a second answer for a task. One session per task also means each question is
answered fresh, as the local runs do.

The bridge is client-agnostic; what differs per client is how tightly its
session can be restricted, and the row records that in ``client_settings``:

- ``claude-code``: ``claude -p`` with ``--strict-mcp-config`` and exactly two
  tools allowed (``get_benchmark_task``, ``submit_benchmark_output``); file
  reading, shell and web are denied.
- ``codex``: ``codex exec`` ephemeral, ``--ignore-user-config`` (only our MCP
  server), read-only sandbox. Codex cannot be denied its shell, so not reading
  files is an instruction there, not a guarantee.

Usage (from the repository, with the reference pack exported)::

    uv run python scripts/benchmark/run_mcp_suite.py --suite chat
        --reference war_and_peace_book1 --client claude-code
        --model claude-sonnet-5 --effort high
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TASK_PROMPT = (
    "You are the model under test in a benchmark. Call get_benchmark_task with "
    'run_id "{run_id}". Answer its user_prompt exactly as the task\'s answer_format '
    "says (under its system_prompt when one is given), producing only the answer. "
    "Then call submit_benchmark_output with that answer verbatim as output_text "
    "(task_id and stage from the task). Do exactly one task and stop. Use no other "
    "tool, read no files, and do not explain yourself."
)
TASK_TOOLS = "mcp__chaoscypher__get_benchmark_task,mcp__chaoscypher__submit_benchmark_output"
DENIED_TOOLS = "Read,Write,Edit,MultiEdit,NotebookEdit,Bash,Glob,Grep,LS,WebSearch,WebFetch,Agent,Task,TodoWrite"


ISOLATION = {
    "claude-code": "one session per task, two tools allowed, empty cwd, own MCP config only",
    "codex": "one ephemeral session per task, read-only sandbox, own MCP config only; shell not denied",
}
VERSION_CMDS = {"claude-code": ["claude"], "codex": ["codex"]}


def _version(cmd: list[str]) -> str:
    """The client's version string, or 'unknown' when it cannot be read."""
    out = subprocess.run([*cmd, "--version"], capture_output=True, text=True, check=False)
    text = (out.stdout or out.stderr).strip()
    return text.split()[-1] if text else "unknown"


def _mcp_config(path: Path, server_cmd: list[str]) -> Path:
    cfg = {"mcpServers": {"chaoscypher": {"command": server_cmd[0], "args": server_cmd[1:]}}}
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _claude_task_cmd(
    run_id: str, *, model: str, effort: str, mcp_cfg: Path, server_cmd: list[str]
) -> list[str]:
    """A Claude Code print-mode session that can only fetch and submit one task."""
    del server_cmd  # the server is named in the MCP config file
    return [
        "claude",
        "-p",
        TASK_PROMPT.format(run_id=run_id),
        "--model",
        model,
        "--effort",
        effort,
        "--mcp-config",
        str(mcp_cfg),
        "--strict-mcp-config",
        "--allowedTools",
        TASK_TOOLS,
        "--disallowedTools",
        DENIED_TOOLS,
        "--output-format",
        "json",
    ]


def _codex_task_cmd(
    run_id: str, *, model: str, effort: str, mcp_cfg: Path, server_cmd: list[str]
) -> list[str]:
    """A Codex exec session: ephemeral, our MCP server only, read-only sandbox."""
    del mcp_cfg  # Codex takes the server on the command line
    return [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "-s",
        "read-only",
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={json.dumps(effort)}",
        "-c",
        f"mcp_servers.chaoscypher.command={json.dumps(server_cmd[0])}",
        "-c",
        f"mcp_servers.chaoscypher.args={json.dumps(server_cmd[1:])}",
        TASK_PROMPT.format(run_id=run_id),
    ]


TASK_CMDS = {"claude-code": _claude_task_cmd, "codex": _codex_task_cmd}


def _run_task(
    run_id: str,
    *,
    client: str,
    model: str,
    effort: str,
    mcp_cfg: Path,
    server_cmd: list[str],
    cwd: Path,
    timeout: int,
) -> tuple[int, str]:
    cmd = TASK_CMDS[client](
        run_id, model=model, effort=effort, mcp_cfg=mcp_cfg, server_cmd=server_cmd
    )
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


async def _main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--suite", required=True)
    ap.add_argument("--reference", help="Reference pack name (chat suite)")
    ap.add_argument(
        "--client",
        choices=sorted(TASK_CMDS),
        default="claude-code",
        help="Which agent CLI answers the tasks",
    )
    ap.add_argument(
        "--model", required=True, help="The client's model id, e.g. claude-sonnet-5 or gpt-5.5"
    )
    ap.add_argument("--effort", default="high")
    ap.add_argument("--label", help="Leaderboard row label")
    ap.add_argument("--only", nargs="*", help="Task ids to run instead of the whole suite")
    ap.add_argument(
        "--server-cmd", default="chaoscypher mcp", help="Command that starts the stdio MCP server"
    )
    ap.add_argument("--task-timeout", type=int, default=600, help="Seconds per task session")
    ap.add_argument(
        "--max-stalls",
        type=int,
        default=3,
        help="Consecutive sessions with no progress before giving up",
    )
    ap.add_argument("--resume", help="Run id to continue instead of starting a new run")
    args = ap.parse_args()

    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings
    from chaoscypher_core.mcp.benchmark import BenchmarkBridge

    # The same engine settings the CLI's MCP server runs on, so the driver and
    # the server share one data dir, one state directory and one reference pack.
    bridge = BenchmarkBridge(build_engine_settings(get_settings()))
    client_settings: dict[str, Any] = {
        "effort": args.effort,
        "client_version": _version(VERSION_CMDS[args.client]),
        "isolation": ISOLATION[args.client],
    }
    if args.client == "claude-code":
        client_settings["thinking"] = "adaptive"
    if args.resume:
        run_id = args.resume
    else:
        started = await bridge.start(
            args.suite,
            args.client,
            args.model,
            label=args.label,
            only=args.only,
            client_settings=client_settings,
            reference=args.reference,
        )
        if not started.get("success"):
            print(json.dumps(started, indent=1))
            return 2
        run_id = started["run_id"]
        print(f"run {run_id}: {started['total']} tasks", flush=True)

    with tempfile.TemporaryDirectory(prefix="cc-mcp-bench-") as tmp:
        cwd = Path(tmp) / "work"
        cwd.mkdir()
        mcp_cfg = _mcp_config(Path(tmp) / "mcp.json", args.server_cmd.split())
        stalls = 0
        while True:
            progress = await bridge.progress(run_id)
            submitted, total = progress.get("submitted", 0), progress.get("total", 0)
            if progress.get("pending_count", total - submitted) == 0:
                break
            code, out = _run_task(
                run_id,
                client=args.client,
                model=args.model,
                server_cmd=args.server_cmd.split(),
                effort=args.effort,
                mcp_cfg=mcp_cfg,
                cwd=cwd,
                timeout=args.task_timeout,
            )
            after = await bridge.progress(run_id)
            if after.get("submitted", 0) > submitted:
                stalls = 0
                print(f"  {after['submitted']}/{total}", flush=True)
            else:
                stalls += 1
                print(
                    f"  no progress (exit {code}); stall {stalls}/{args.max_stalls}\n{out[-600:]}",
                    file=sys.stderr,
                    flush=True,
                )
                if stalls >= args.max_stalls:
                    print(f"giving up; resume with --resume {run_id}", file=sys.stderr)
                    return 3

    finished = await bridge.finish(run_id)
    print(json.dumps(finished, indent=1))
    return 0 if finished.get("success") else 4


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
