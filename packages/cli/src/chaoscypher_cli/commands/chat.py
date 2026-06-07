# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301
# D301 is suppressed here because the Click @click.command docstring uses
# the ``\x08`` (ASCII backspace) escape to mark non-reflowing paragraphs —
# the canonical Click pattern. Adding the ``r`` prefix would defeat the
# escape (it becomes literal four-character ``\x08``) and surface as
# garbled help output to users.

"""Chat command - Interactive LLM conversation with knowledge graph tool calling.

Provides a conversational interface using a local or remote LLM with
tool calling support to query the knowledge graph — matching the
capabilities of the web UI chat.

Example:
    chaoscypher chat "What nodes are in the graph?"
    chaoscypher chat --context doc-123 "Summarize this document"
    chaoscypher chat --system "Be brief" "List all templates"
    chaoscypher chat  # Interactive mode
"""

import asyncio
import json
import re
import sys
from typing import Any

import click
import structlog
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from chaoscypher_cli.context import get_context
from chaoscypher_core.services.chat.engine.constants import MAX_TOOL_ITERATIONS


logger = structlog.get_logger(__name__)
console = Console()

# Maximum characters of context text injected into the system prompt.
# Prevents oversized prompts when documents are large.
_MAX_SYSTEM_PROMPT_CONTEXT_CHARS = 4000


@click.command()
@click.argument("message", required=False)
@click.option("--context", "-c", "context_id", help="Node or document ID to use as context")
@click.option("--source", "-s", "sources", multiple=True, help="Scope to source ID (repeatable)")
@click.option(
    "--tag", "-t", "tags", multiple=True, help="Scope to all sources with this tag (repeatable)"
)
@click.option("--system", "-S", help="Custom system prompt")
@click.option("--database", "-d", default="default", help="Database name")
def chat(
    message: str | None,
    context_id: str | None,
    sources: tuple[str, ...],
    tags: tuple[str, ...],
    system: str | None,
    database: str,
) -> None:
    """Chat with the AI using your knowledge graph.

    \x08
    If MESSAGE is provided, send it as a single message.
    If MESSAGE is omitted, enter interactive chat mode.

    The LLM automatically uses tools to search nodes, get relationships,
    and query the knowledge graph — just like the web UI.

    \x08
    Scope chat to specific sources or tags:
        chaoscypher chat --source src-123 "Summarize this document"
        chaoscypher chat --tag research --tag notes "Compare topics"

    \x08
    Example:
        chaoscypher chat "What nodes are in the graph?"
        chaoscypher chat --context node-123 "Explain this"
        chaoscypher chat --system "Be brief" "Who is the most connected node?"
        chaoscypher chat  # Interactive mode
    """
    try:
        ctx = get_context(database_name=database)

        # Check LLM availability
        if not ctx.has_llm:
            console.print(
                Panel(
                    "[yellow]LLM Not Available[/yellow]\n\n"
                    "Chat requires an LLM provider.\n\n"
                    "To enable:\n"
                    "  1. Install Ollama: https://ollama.ai\n"
                    "  2. Start Ollama: ollama serve\n"
                    "  3. Pull a model: ollama pull llama3.2\n\n"
                    "Or configure OpenAI/Anthropic/Gemini via:\n"
                    "  chaoscypher config set llm.provider openai",
                    title="LLM Required",
                    border_style="yellow",
                )
            )
            sys.exit(1)

        # Resolve source scope from --source and --tag flags
        source_ids = _resolve_source_scope(ctx, sources, tags)

        # Build context from node if provided
        context_text = None
        if context_id:
            context_text = _get_context_text(ctx, context_id)
            if context_text:
                console.print(f"[dim]Using context from: {context_id}[/dim]\n")
            else:
                console.print(f"[yellow]Warning:[/yellow] Could not find context: {context_id}")

        # Resolve source names for system prompt
        source_names = _get_source_names(ctx, source_ids) if source_ids else None

        # Build system prompt and tools
        system_prompt = _build_system_prompt(context_text, system, source_names=source_names)
        tool_executor, tools = _create_tool_infrastructure(ctx, source_ids=source_ids)

        if message:
            _send_message(ctx, message, system_prompt, tools, tool_executor)
        else:
            _interactive_chat(ctx, system_prompt, tools, tool_executor, source_ids=source_ids)

    except KeyboardInterrupt:
        console.print("\n[dim]Chat ended.[/dim]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _resolve_source_scope(
    ctx: Any,
    sources: tuple[str, ...],
    tags: tuple[str, ...],
) -> list[str] | None:
    """Resolve --source and --tag flags into a list of source IDs.

    Args:
        ctx: CLI context
        sources: Source IDs from --source flags
        tags: Tag IDs from --tag flags

    Returns:
        List of source IDs or None if no scope
    """
    source_ids = list(sources)

    if tags:
        tag_source_ids = ctx.storage_adapter.get_source_ids_by_tag_ids(
            list(tags), ctx.database_name
        )
        source_ids = list(set(source_ids + tag_source_ids))

    if not source_ids:
        return None

    console.print(f"[dim]Scoped to {len(source_ids)} source(s)[/dim]")
    return source_ids


def _get_source_names(ctx: Any, source_ids: list[str]) -> list[str]:
    """Get display names for source IDs.

    Args:
        ctx: CLI context
        source_ids: Source IDs to look up

    Returns:
        List of source names/titles
    """
    names = []
    for sid in source_ids:
        source = ctx.storage_adapter.get_source(sid, ctx.database_name)
        if source:
            names.append(source.get("title", source.get("filename", sid)))
        else:
            names.append(sid)
    return names


def _get_context_text(ctx: Any, context_id: str) -> str | None:
    """Get text content from a node or document.

    Args:
        ctx: CLI context
        context_id: Node ID or document ID

    Returns:
        Context text or None
    """
    # Try to get from graph nodes
    try:
        node = ctx.node_service.get_node(context_id)
        if node:
            parts = []
            if node.get("name"):
                parts.append(f"Name: {node['name']}")
            if node.get("description"):
                parts.append(f"Description: {node['description']}")
            if node.get("properties"):
                parts.append(f"Properties: {node['properties']}")
            return "\n".join(parts)
    except Exception:
        logger.debug("context_node_lookup_failed", context_id=context_id)

    # Try to get from source_processing files
    try:
        file_record = ctx.storage_adapter.get_file(context_id, ctx.database_name)
        if file_record:
            chunks = ctx.storage_adapter.list_chunks(
                ctx.database_name, source_id=context_id, include_content=True
            )
            if chunks:
                return "\n\n".join(c.get("content", "") for c in chunks[:5])
    except Exception:
        logger.debug("context_file_lookup_failed", context_id=context_id)

    return None


def _build_system_prompt(
    context_text: str | None = None,
    custom: str | None = None,
    source_names: list[str] | None = None,
) -> str:
    """Build the system prompt for the chat.

    Args:
        context_text: Optional context text to include
        custom: Optional custom system prompt to use instead of the default
        source_names: Optional list of source names for scope info

    Returns:
        System prompt string
    """
    from chaoscypher_core.services.chat.engine.constants import SYSTEM_PROMPT

    base = custom or SYSTEM_PROMPT
    if context_text:
        base = f"{base}\n\nAdditional context:\n---\n{context_text[:_MAX_SYSTEM_PROMPT_CONTEXT_CHARS]}\n---"
    if source_names:
        scope_section = (
            "\n\nSOURCE SCOPE\n"
            "This conversation is scoped to the following sources. "
            "Only use information from these sources when answering:\n"
            + "\n".join(f"- {name}" for name in source_names)
        )
        base = f"{base}{scope_section}"
    return base


def _create_tool_infrastructure(
    ctx: Any,
    source_ids: list[str] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Create tool executor and tool schemas for chat.

    Args:
        ctx: CLI context
        source_ids: Optional source scope filter

    Returns:
        Tuple of (tool_executor, tools)
    """
    from chaoscypher_core.services.chat.engine.constants import ESSENTIAL_TOOL_NAMES
    from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
        get_essential_tool_schemas,
    )

    scope = {"source_ids": source_ids} if source_ids else None

    # Create LLM chat callback wrapping the CLI's direct LLM provider
    llm_provider = ctx.llm_provider

    async def llm_chat_callback(
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Direct LLM chat via CLI provider."""
        kwargs: dict[str, Any] = {"messages": messages, "stream": False}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await llm_provider.chat(**kwargs)
        return {"content": response.content}

    # Create embedding callback for semantic/hybrid search
    _embedding_service = ctx.embedding_service

    async def embedding_callback(text: str) -> Any:
        """Local embedding via EmbeddingService."""
        return await _embedding_service.embed(text)

    tool_executor = ToolExecutorService(
        graph_repository=ctx.graph_repository,
        search_repository=ctx.search_repository,
        indexing_repository=ctx.storage_adapter,
        embedding_callback=embedding_callback,
        llm_chat_callback=llm_chat_callback if llm_provider else None,
        engine_settings=ctx.settings,
        scope=scope,
        source_storage=ctx.storage_adapter,
    )
    tools = get_essential_tool_schemas(ESSENTIAL_TOOL_NAMES)

    return tool_executor, tools


async def _chat_with_tools(
    llm_provider: Any,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_executor: Any,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> str:
    """Run the LLM with streaming and multi-step tool calling.

    Streams text tokens directly to the console as they arrive.
    When the LLM returns tool calls, executes them and loops until
    a final text response (no tool calls) or the iteration limit.

    Args:
        llm_provider: LLM provider instance
        messages: Conversation messages (mutated in place)
        tools: Tool schemas in OpenAI function calling format
        tool_executor: ToolExecutorService for executing tool calls
        max_iterations: Safety limit on tool-calling rounds

    Returns:
        Final assistant text content (already displayed to console)
    """
    content = ""
    streamed = False

    for _ in range(max_iterations):
        response = await llm_provider.chat(messages=messages, tools=tools, stream=True)

        # Build citation data from tool results accumulated so far
        citation_data = _build_citation_data(messages)

        # Consume streaming response
        if response.is_stream and response.stream:
            content, tool_calls, streamed = await _consume_stream(
                response.stream, citation_data=citation_data
            )
        else:
            # Non-streaming fallback (provider doesn't support streaming)
            content = response.content
            tool_calls = response.tool_calls or []
            streamed = False

        if not tool_calls:
            if streamed:
                _write_raw("\n")  # End the streamed line
            return content

        # Newline after any streamed text before tool indicators
        if streamed:
            _write_raw("\n")

        # Append assistant message with tool_calls
        assistant_msg: dict = {"role": "assistant", "content": content or ""}
        assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # Execute each tool call and append results
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            arguments = func.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}

            console.print(f"  [dim]\u2192 {tool_name}({_summarize_args(arguments)})[/dim]")
            result = await tool_executor.execute_tool(tool_name, arguments)

            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(result, default=str),
                    "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                }
            )

        streamed = False

    if streamed:
        _write_raw("\n")
    return content


async def _consume_stream(
    stream: Any,
    citation_data: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, list[Any], bool]:
    """Consume an LLM streaming response, printing text as it arrives.

    Entity references like ``[[node:ID|Label]]`` are transformed to
    bold labels in real-time. Chunk citations ``[[cite:C0:S1|file]]``
    are resolved to sentence text and rendered as terminal blockquotes.

    Args:
        stream: Async generator yielding chunk dicts
        citation_data: Chunk alias → data mapping for citation resolution

    Returns:
        Tuple of (accumulated_content, tool_calls, did_stream_any_text)
    """
    writer = _StreamWriter(citation_data=citation_data)
    content = ""
    tool_calls: list = []
    did_stream = False

    async for chunk in stream:
        chunk_type = chunk.get("type", "")

        if chunk_type == "content":
            delta = chunk.get("delta", "")
            if delta:
                writer.write(delta)
                did_stream = True
            content = chunk.get("accumulated", content)

        elif chunk_type == "done":
            content = chunk.get("content", content)
            tool_calls = chunk.get("tool_calls") or []

        elif chunk_type == "error":
            writer.close()
            error_msg = chunk.get("error", "Unknown error")
            if did_stream:
                _write_raw("\n")
            console.print(f"[red]Error:[/red] {error_msg}")

    writer.close()
    return content, tool_calls, did_stream


# Matches [[node:ID|Label]] and [[edge:ID|Label]] entity references
_ENTITY_REF_RE = re.compile(
    r"\[\[(node|edge)[_:\-./\s][a-zA-Z_]*[a-f0-9-]+\|([^\]]+)\]\]",
    re.IGNORECASE,
)

# Matches [[cite:ALIAS_OR_ID:Sn|label]] chunk citations
_CITE_RE = re.compile(
    r"\[\[cite:([A-Za-z0-9-]+)[:#](S\d+(?:[,;]\s*S\d+)*)(?:\|([^\]]+))?\]\]",
    re.IGNORECASE,
)


def _build_citation_data(
    messages: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build mapping from chunk aliases to chunk data for citation resolution.

    Extracts chunk alias, original content, sentence offsets, and filename
    from tool result messages so citations can be resolved during streaming.

    Args:
        messages: Conversation messages including tool results.

    Returns:
        Mapping of alias (e.g. "C0") to chunk data dict.
    """
    citation_data: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not content:
            continue
        try:
            data = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError, TypeError:
            continue
        if not isinstance(data, dict):
            continue
        for key in ("chunks", "related_chunks"):
            chunk_list = data.get(key)
            if not isinstance(chunk_list, list):
                continue
            for chunk in chunk_list:
                if not isinstance(chunk, dict) or "chunk_alias" not in chunk:
                    continue
                alias = chunk["chunk_alias"].upper()
                meta = chunk.get("chunk_metadata") or {}
                citation_data[alias] = {
                    "original_content": chunk.get("original_content", ""),
                    "sentence_offsets": meta.get("sentence_offsets", [])
                    if isinstance(meta, dict)
                    else [],
                    "filename": chunk.get("filename", "source"),
                }
    return citation_data


def _resolve_citation_text(
    alias: str,
    sentence_refs: str,
    citation_data: dict[str, dict[str, Any]],
) -> tuple[str | None, str]:
    """Resolve a citation alias and sentence refs to actual text.

    Args:
        alias: Chunk alias (e.g. "C0") or UUID.
        sentence_refs: Comma-separated sentence refs like "S1,S3".
        citation_data: Chunk alias → data mapping.

    Returns:
        Tuple of (sentence_text or None, filename).
    """
    chunk = citation_data.get(alias.upper())
    if not chunk:
        return None, "source"

    filename = chunk.get("filename", "source")
    offsets = chunk.get("sentence_offsets", [])
    original = chunk.get("original_content", "")
    if not (offsets and original):
        return None, filename

    indices = [int(s) - 1 for s in re.findall(r"S(\d+)", sentence_refs)]
    sentences = [
        original[offsets[idx]["start"] : offsets[idx]["end"]]
        for idx in indices
        if 0 <= idx < len(offsets)
    ]
    return (" ".join(sentences) if sentences else None), filename


class _StreamWriter:
    """Streaming text writer that transforms references in real-time.

    Buffers text only when a potential ``[[`` reference is detected.
    Entity references become bold labels; chunk citations are resolved
    to sentence text and rendered as terminal blockquotes.
    """

    def __init__(
        self,
        citation_data: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Initialize with an optional citation lookup table."""
        self._buffer = ""
        self._citation_data = citation_data or {}

    def write(self, delta: str) -> None:
        """Write a text delta, transforming references.

        Args:
            delta: Text chunk from the LLM stream
        """
        self._buffer += delta
        self._flush_safe()

    def close(self) -> None:
        """Flush any remaining buffered text."""
        if self._buffer:
            _write_raw(self._transform(self._buffer))
            self._buffer = ""

    def _flush_safe(self) -> None:
        """Flush text that can't be part of an incomplete reference."""
        last_open = self._buffer.rfind("[[")
        if last_open != -1 and "]]" not in self._buffer[last_open:]:
            safe = self._buffer[:last_open]
            if safe:
                _write_raw(self._transform(safe))
            self._buffer = self._buffer[last_open:]
        else:
            _write_raw(self._transform(self._buffer))
            self._buffer = ""

    def _transform(self, text: str) -> str:
        """Transform entity references and citations in text.

        Args:
            text: Text potentially containing references

        Returns:
            Text with references replaced
        """
        if "[[" not in text:
            return text
        return _transform_entity_refs(self._transform_citations(text))

    def _transform_citations(self, text: str) -> str:
        """Replace citation markers with resolved sentence text.

        Args:
            text: Text potentially containing [[cite:...]] markers

        Returns:
            Text with citations resolved to blockquotes or labels
        """
        if not _CITE_RE.search(text):
            return text

        def _replace(match: re.Match[str]) -> str:
            alias = match.group(1)
            sentence_refs = match.group(2)
            label = match.group(3) or "source"

            sentence_text, filename = _resolve_citation_text(
                alias, sentence_refs, self._citation_data
            )
            if not sentence_text:
                # Can't resolve — show filename reference
                if console.is_terminal:
                    return f"\033[2m[{label}]\033[0m"
                return f"[{label}]"

            # Render as a terminal blockquote
            display_label = label if label != "source" else filename
            if console.is_terminal:
                lines = ["\n"]
                lines.extend(
                    f"\033[2;3m  \u2502 {line}\033[0m\n" for line in sentence_text.split("\n")
                )
                lines.append(f"\033[2m  \u2514\u2500 {display_label}\033[0m\n")
                return "".join(lines)
            lines = ["\n"]
            for line in sentence_text.split("\n"):
                lines.append(f"  | {line}\n")
            lines.append(f"  -- {display_label}\n")
            return "".join(lines)

        return _CITE_RE.sub(_replace, text)


def _transform_entity_refs(text: str) -> str:
    r"""Replace entity references with bold labels.

    ``[[node:node_abc123|Pierre]]`` becomes ``\\033[1mPierre\\033[0m``.

    Args:
        text: Text potentially containing entity references

    Returns:
        Text with references replaced
    """
    if console.is_terminal:
        return _ENTITY_REF_RE.sub(lambda m: f"\033[1m{m.group(2)}\033[0m", text)
    return _ENTITY_REF_RE.sub(lambda m: m.group(2), text)


def _write_raw(text: str) -> None:
    """Write raw text to console output without Rich formatting.

    Args:
        text: Text to write
    """
    console.file.write(text)
    console.file.flush()


def _summarize_args(args: dict) -> str:
    """Summarize tool call arguments for display.

    Args:
        args: Tool call arguments dict

    Returns:
        Short summary string
    """
    if not args:
        return ""
    parts = []
    for key, value in list(args.items())[:3]:
        v = str(value)
        if len(v) > 40:
            v = v[:37] + "..."
        parts.append(f"{key}={v}")
    return ", ".join(parts)


def _send_message(
    ctx: Any, message: str, system_prompt: str, tools: list[Any], tool_executor: Any
) -> None:
    """Send a single message and display the response.

    Args:
        ctx: CLI context
        message: User message
        system_prompt: System prompt
        tools: Tool schemas
        tool_executor: ToolExecutorService instance
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    content = asyncio.run(_chat_with_tools(ctx.llm_provider, messages, tools, tool_executor))

    if not content:
        console.print("[dim]No response received.[/dim]")


def _interactive_chat(
    ctx: Any,
    system_prompt: str,
    tools: list[Any],
    tool_executor: Any,
    source_ids: list[str] | None = None,
) -> None:
    """Run an interactive chat session.

    Args:
        ctx: CLI context
        system_prompt: System prompt
        tools: Tool schemas
        tool_executor: ToolExecutorService instance
        source_ids: Optional source scope filter
    """
    scope_hint = ""
    if source_ids:
        scope_hint = "\nType '/scope' to view current source scope.\n"

    console.print(
        Panel(
            "[bold]Interactive Chat Mode[/bold]\n\n"
            "Type your messages and press Enter to send.\n"
            "Type 'exit' or 'quit' to end the chat.\n"
            f"Type 'clear' to start a new conversation.{scope_hint}",
            title="Chaos Cypher Chat",
            border_style="cyan",
        )
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

            if not user_input.strip():
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                console.print("[dim]Goodbye![/dim]")
                break

            if user_input.lower() == "clear":
                messages = [{"role": "system", "content": system_prompt}]
                console.print("[dim]Conversation cleared.[/dim]")
                continue

            if user_input.lower() == "/scope":
                if source_ids:
                    names = _get_source_names(ctx, source_ids)
                    console.print("\n[dim]Current scope:[/dim]")
                    for name in names:
                        console.print(f"  [dim]- {name}[/dim]")
                else:
                    console.print("[dim]No source scope — all sources accessible.[/dim]")
                continue

            if user_input.lower() == "help":
                scope_help = "  /scope - Show current source scope\n" if source_ids else ""
                console.print(
                    "\n[dim]Commands:[/dim]\n"
                    "  exit, quit, q - End the chat\n"
                    "  clear - Start a new conversation\n"
                    f"{scope_help}"
                    "  help - Show this help message\n"
                )
                continue

            # Add user message
            messages.append({"role": "user", "content": user_input})

            # Get response with tool calling
            console.print("\n[bold green]Assistant[/bold green]")

            content = asyncio.run(
                _chat_with_tools(ctx.llm_provider, messages, tools, tool_executor)
            )

            if not content:
                console.print("[dim]No response received.[/dim]")

            # Add assistant message to history (without tool call details)
            messages.append({"role": "assistant", "content": content or ""})

        except KeyboardInterrupt:
            console.print("\n[dim]Chat ended.[/dim]")
            break
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            continue
