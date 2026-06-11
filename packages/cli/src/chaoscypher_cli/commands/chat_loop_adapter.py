# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI adapters for the shared chat tool loop.

The CLI runs the same :func:`chaoscypher_core.streaming.chat.loop.run_chat_tool_loop`
as the web worker; these adapters supply the console-specific transports:

- :class:`RichConsoleSink` renders loop events with the CLI's existing
  terminal treatment (streamed deltas through the citation-resolving
  ``_StreamWriter``, dim arrow lines for tool execution, yellow warnings).
- :class:`PromptApprovalBroker` turns ``tool_approval_required`` into an
  interactive y/N confirm (fail-closed: EOF, interrupt, or anything but an
  explicit yes denies the call).
- :class:`CliMessageBuilder` satisfies the loop's ``chat_service`` seam;
  the CLI does not persist chats, so buffered messages are discarded.
"""

import asyncio
import contextlib
from typing import Any

from rich.prompt import Confirm


class CliMessageBuilder:
    """Loop ``chat_service`` stub: builds message dicts, persists nothing."""

    def build_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a message dict shaped like ChatService.build_message's."""
        return {
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "extra_metadata": extra_metadata or {},
        }


class RichConsoleSink:
    """ChatEventSink rendering loop events to the CLI console.

    Content deltas stream through a fresh ``_StreamWriter`` per LLM phase so
    citation markers resolve against the tool results gathered so far (the
    writer holds a citation map snapshot; a new phase rebuilds it from the
    live ``messages`` list).
    """

    def __init__(self, console: Any, messages: list[dict[str, Any]]) -> None:
        """Bind the sink to the console and the turn's live message history.

        Args:
            console: The CLI's rich Console.
            messages: The turn's mutable message list (tool results are
                appended by the loop; used to rebuild citation data).

        """
        self._console = console
        self._messages = messages
        self._writer: Any = None
        self.streamed_any = False

    def _open_writer(self) -> Any:
        """Return the phase's stream writer, building it on first delta."""
        from chaoscypher_cli.commands.chat import _build_citation_data, _StreamWriter

        if self._writer is None:
            self._writer = _StreamWriter(citation_data=_build_citation_data(self._messages))
        return self._writer

    def _close_writer(self, *, newline: bool = False) -> None:
        """Flush and drop the current writer (ends the LLM phase)."""
        from chaoscypher_cli.commands.chat import _write_raw

        if self._writer is not None:
            self._writer.close()
            self._writer = None
            if newline and self.streamed_any:
                _write_raw("\n")

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Render one loop event; never raises (rendering is best-effort)."""
        with contextlib.suppress(Exception):
            self._render(event_type, payload)

    def _render(self, event_type: str, payload: dict[str, Any]) -> None:
        """Dispatch one event to its console treatment (unknown types ignored)."""
        from chaoscypher_cli.commands.chat import _summarize_args

        if event_type == "content":
            delta = payload.get("delta", "")
            if delta:
                self._open_writer().write(delta)
                self.streamed_any = True
        elif event_type in ("tool_calls", "cached_tool_calls"):
            # Tool phase begins: flush the streamed text line.
            self._close_writer(newline=True)
        elif event_type == "tool_start":
            args = payload.get("arguments") or {}
            self._console.print(
                f"  [dim]→ {payload.get('tool', '?')}({_summarize_args(args)})[/dim]"
            )
        elif event_type == "tool_approval_required":
            args = payload.get("arguments") or {}
            self._close_writer(newline=True)
            self._console.print(
                f"  [yellow]Approval required:[/yellow] "
                f"{payload.get('tool_name', '?')}({_summarize_args(args)})"
            )
        elif event_type == "tool_rejected":
            self._console.print(
                f"  [dim]✗ {payload.get('tool_name', '?')} denied "
                f"({payload.get('decision', 'reject')})[/dim]"
            )
        elif event_type == "warning":
            self._close_writer(newline=True)
            self._console.print(f"[yellow]Warning:[/yellow] {payload.get('message', '')}")
        elif event_type == "error":
            self._close_writer(newline=True)
            self._console.print(f"[red]Error:[/red] {payload.get('error', 'Unknown error')}")

    def finish(self) -> None:
        """Close any open writer at the end of the turn (final newline)."""
        self._close_writer(newline=True)


class PromptApprovalBroker:
    """ApprovalBroker that asks the user interactively in the terminal."""

    def __init__(self, console: Any) -> None:
        """Bind the broker to the console used for prompting.

        Args:
            console: The CLI's rich Console.

        """
        self._console = console
        self._pending: dict[str, str] = {}

    async def request(
        self,
        chat_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int,
    ) -> None:
        """Remember the tool name for the upcoming prompt."""
        self._pending[tool_call_id] = tool_name

    async def wait(self, chat_id: str, tool_call_id: str, timeout_s: float) -> str:
        """Prompt y/N; anything but an explicit yes denies (fail-closed)."""
        tool_name = self._pending.pop(tool_call_id, "this tool")
        try:
            approved = await asyncio.to_thread(
                Confirm.ask,
                f"  Allow [bold]{tool_name}[/bold] to run?",
                console=self._console,
                default=False,
            )
        except EOFError, KeyboardInterrupt, Exception:
            return "reject"
        return "approve" if approved else "reject"


__all__ = ["CliMessageBuilder", "PromptApprovalBroker", "RichConsoleSink"]
