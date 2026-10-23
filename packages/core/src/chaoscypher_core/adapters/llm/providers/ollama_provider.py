# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama LLM Provider using LangChain.

Local LLM provider for Ollama models (e.g., Llama, Qwen, Mistral).
Supports chat completion, streaming, and thinking mode.
"""

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

import httpx
import structlog
from langchain_ollama import ChatOllama
from ollama._types import ResponseError

from chaoscypher_core.adapters.llm.providers.base import (
    BaseLLMProvider,
    extract_provider_timings,
    extract_streaming_finish_reason,
    extract_streaming_usage,
    normalize_finish_reason,
)
from chaoscypher_core.adapters.llm.utils import (
    convert_to_langchain_messages,
    format_tool_calls_response,
)
from chaoscypher_core.exceptions import LLMError, ToolCallingNotSupportedError
from chaoscypher_core.plugins.base import PluginMetadata
from chaoscypher_core.utils.decoding import (
    effective_repeat_penalty,
    effective_seed,
    effective_temperature,
)
from chaoscypher_core.utils.stream_errors import (
    OLLAMA_PATTERNS,
    classify_llm_exception,
    error_chunk,
)


# Pattern to match <think>...</think> tags (including newlines)
_THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Pattern to extract content from <think>...</think> tags
_THINK_TAG_EXTRACT_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL)


if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = structlog.get_logger(__name__)


OLLAMA_RUNTIME_OPTIONS: tuple[str, ...] = ("num_batch", "num_thread", "num_parallel")
"""The per-card performance options the provider requests (from the ``ollama_<name>``
settings), beside ``num_ctx``, which rows record on their own. Not all reach Ollama:
see :data:`OLLAMA_OPTIONS_NOT_SENT`."""

OLLAMA_OPTIONS_NOT_SENT: frozenset[str] = frozenset(
    name for name in OLLAMA_RUNTIME_OPTIONS if name not in ChatOllama.model_fields
)
"""The :data:`OLLAMA_RUNTIME_OPTIONS` ``ChatOllama`` drops without a word (audit wave 5).

Read from ``ChatOllama``'s own fields (``extra="ignore"``): langchain-ollama 1.x
has ``num_thread`` but no ``num_batch`` or ``num_parallel``, so those two are
requested and never sent (``num_parallel`` is a server setting,
``OLLAMA_NUM_PARALLEL``, not a request option at all). Sending ``num_batch``
would take a per-call ``options`` dict that replaces every option
``ChatOllama`` builds (temperature, seed, ``num_ctx`` ...), and it can change
the numerics of a temperature-0 answer, so it is left unsent rather than
started silently. A record of these values must say so:
:func:`ollama_runtime_options_record`.
"""


OLLAMA_CALL_OPTIONS: tuple[str, ...] = ("repeat_penalty",)
"""The options a single call sends only when a :func:`~chaoscypher_core.utils.decoding.decoding_override`
names them (the extraction retry ladder's second rung, audit wave 5); every other
call leaves them to the Modelfile's default (``parameters`` in the build record)."""


def ollama_call_options() -> dict[str, Any]:
    """The :data:`OLLAMA_CALL_OPTIONS` a call made now sends: the overridden value, or None (not sent).

    One reader for the provider, which sends each that is not None, and for a
    row's record (:func:`ollama_runtime_options_record`), so what is recorded
    is what is sent.
    """
    return {"repeat_penalty": effective_repeat_penalty()}


def ollama_runtime_options_record(source: Any) -> dict[str, dict[str, Any]]:
    """What a row records about each option: the requested value and whether it was sent.

    ``{"num_batch": {"value": 512, "sent": False}, "num_thread": {"value": 8, "sent": True}, ...}``:
    ``sent`` is False for an option ``ChatOllama`` drops
    (:data:`OLLAMA_OPTIONS_NOT_SENT`) and for an unset one. The per-call
    options (:data:`OLLAMA_CALL_OPTIONS`) follow, as a call made now would
    send them: ``{"repeat_penalty": {"value": None, "sent": False}}`` outside
    an override.
    """
    requested = {**ollama_runtime_options(source), **ollama_call_options()}
    return {
        name: {"value": value, "sent": value is not None and name not in OLLAMA_OPTIONS_NOT_SENT}
        for name, value in requested.items()
    }


def ollama_runtime_options(source: Any) -> dict[str, Any]:
    """The :data:`OLLAMA_RUNTIME_OPTIONS` as the provider requests them: the value, or None when unset.

    ``source`` is the provider's config mapping (``ollama_num_batch`` ...) or
    an ``LLMSettings`` with the same attribute names. The provider builds its
    ``ChatOllama`` from this, and a benchmark row records the same call's
    result (``extras["model_build"]``, audit wave 4). ``ChatOllama`` drops
    ``num_batch`` and ``num_parallel`` (:data:`OLLAMA_OPTIONS_NOT_SENT`): those
    are requested, not sent; :func:`ollama_runtime_options_record` says which.
    A falsy value (0, None) is not requested.
    """

    def get(name: str) -> Any:
        """One ``ollama_<name>`` value from a mapping or an object."""
        key = f"ollama_{name}"
        return source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)

    return {name: (get(name) or None) for name in OLLAMA_RUNTIME_OPTIONS}


def ollama_runtime_config(llm: Any) -> dict[str, Any]:
    """The ``ollama_<name>`` config entries a provider is built with, copied from ``LLMSettings``.

    One copy for the single-instance factory and the load balancer's
    per-instance config (they kept two hand-written lists, audit wave 4):
    :data:`OLLAMA_RUNTIME_OPTIONS` plus ``num_ctx``, under the keys
    :func:`ollama_runtime_options` reads back, so what a provider sends is
    what the settings say by construction.
    """
    return {
        f"ollama_{name}": getattr(llm, f"ollama_{name}")
        for name in (*OLLAMA_RUNTIME_OPTIONS, "num_ctx")
    }


MODEL_BUILD_KEYS: tuple[str, ...] = (
    "digest",
    "server_version",
    "parameters",
    *OLLAMA_RUNTIME_OPTIONS,
    *OLLAMA_CALL_OPTIONS,
)
"""The keys of a row's ``extras["model_build"]`` (:func:`ollama_model_build`)."""

MODEL_BUILD_TIMEOUT_SECONDS = 5.0
"""How long :func:`ollama_model_build` waits for each Ollama API call."""


def _parameter_value(text: str) -> Any:
    """One Modelfile ``PARAMETER`` value: an int, a float, else the string without its quotes."""
    for cast_to in (int, float):
        try:
            return cast_to(text)
        except ValueError:
            continue
    return text.strip().strip('"')


def parse_modelfile_parameters(text: str | None) -> dict[str, Any]:
    """``/api/show``'s ``parameters`` text as ``{name: value}``; a repeated name (``stop``) is a list.

    Ollama reports the Modelfile defaults one per line, the name padded to a
    column (``repeat_penalty                 1``, ``stop "<|im_end|>"``).
    Those that still apply at temperature 0 (``repeat_penalty``,
    ``top_k``, ``top_p``, ``min_p``, ``num_keep``, the stop list) change the
    answer while the row's own settings stay the same.
    """
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        name, _, value = line.strip().partition(" ")
        if not name:
            continue
        parsed = _parameter_value(value.strip())
        if name in out:
            prior = out[name]
            out[name] = [*(prior if isinstance(prior, list) else [prior]), parsed]
        else:
            out[name] = parsed
    return out


def _same_model(listed: str, model: str) -> bool:
    """Whether an ``/api/tags`` name is ``model`` (an untagged name means ``:latest``)."""

    def tagged(name: str) -> str:
        """The name with its tag, ``:latest`` when it has none."""
        return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"

    return tagged(listed) == tagged(model)


async def ollama_model_build(
    base_url: str,
    model: str,
    *,
    options: Mapping[str, Any] | None = None,
    timeout: float = MODEL_BUILD_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """What an Ollama server says about the model a row ran: its build, the server and the defaults.

    Audit wave 4 (2026-10-06): an ``ollama pull`` or a server upgrade between
    a baseline and a variant changed the weights, the template or the
    Modelfile defaults without any row field moving, so the change read as
    the variant's effect. Returns :data:`MODEL_BUILD_KEYS`: the model's
    ``digest`` (``/api/tags``), the ``server_version`` (``/api/version``),
    the Modelfile ``parameters`` (``/api/show``,
    :func:`parse_modelfile_parameters`) and the per-card ``options`` the
    provider sends (:func:`ollama_runtime_options`, then the per-call
    :data:`OLLAMA_CALL_OPTIONS`). Does not raise on a
    server problem: a fact the server cannot report (unreachable, an older
    API, an unknown model, a body that is not JSON) is None. (The test
    suites' Ollama guard raises a ``RuntimeError``, which is let through on
    purpose: a unit test fakes this call.)
    """
    build: dict[str, Any] = {
        "digest": None,
        "server_version": None,
        "parameters": None,
        **{
            name: (options or {}).get(name)
            for name in (*OLLAMA_RUNTIME_OPTIONS, *OLLAMA_CALL_OPTIONS)
        },
    }
    root = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            for path, fill in (
                ("/api/version", _fill_version),
                ("/api/tags", _fill_digest),
                ("/api/show", _fill_parameters),
            ):
                try:
                    if path == "/api/show":
                        response = await client.post(f"{root}{path}", json={"model": model})
                    else:
                        response = await client.get(f"{root}{path}")
                    if response.status_code == 200:
                        fill(build, response.json(), model)
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    logger.warning(
                        "ollama_model_build_unavailable",
                        path=path,
                        model=model,
                        error_type=type(exc).__name__,
                    )
    except httpx.HTTPError as exc:  # pragma: no cover - client construction does no I/O
        logger.warning("ollama_model_build_unavailable", model=model, error=str(exc)[:200])
    return build


def _fill_version(build: dict[str, Any], payload: Any, _model: str) -> None:
    """``/api/version``: ``{"version": "0.12.3"}``."""
    if isinstance(payload, dict) and payload.get("version"):
        build["server_version"] = str(payload["version"])


def _fill_digest(build: dict[str, Any], payload: Any, model: str) -> None:
    """``/api/tags``: ``{"models": [{"name", "model", "digest", ...}]}``; the model's digest."""
    models = payload.get("models") if isinstance(payload, dict) else None
    for entry in models or []:
        if not isinstance(entry, dict):
            continue
        names = {str(entry.get("name") or ""), str(entry.get("model") or "")}
        if any(n and _same_model(n, model) for n in names) and entry.get("digest"):
            build["digest"] = str(entry["digest"])
            return


def _fill_parameters(build: dict[str, Any], payload: Any, _model: str) -> None:
    """``/api/show``: ``{"parameters": "...", "modelfile", "template", "details", ...}``."""
    if isinstance(payload, dict):
        build["parameters"] = parse_modelfile_parameters(payload.get("parameters"))


@dataclass(frozen=True)
class CallDecoding:
    """The seed and per-call options one ``chat()`` sends, resolved when it is called.

    Audit wave 6: the temperature was read in ``chat()`` and the seed when the
    request was built, which for a stream is when it is first iterated: a
    stream consumed after its :func:`~chaoscypher_core.utils.decoding.decoding_override`
    block closed sent the old seed beside the new temperature. ``chat()``
    resolves this before any await and every request it builds reads it.

    Attributes:
        seed: The seed sent (None: Ollama picks one per request).
        repeat_penalty: The ``options.repeat_penalty`` sent; None: none (the
            Modelfile's default applies).
    """

    seed: int | None
    repeat_penalty: float | None


def stream_error_chunk(exc: BaseException, model: str | None) -> dict[str, Any]:
    """The error chunk a streaming failure yields: the one classifier's verdict as a chunk.

    :func:`~chaoscypher_core.utils.stream_errors.classify_llm_exception`
    under Ollama's words, so the stream and the non-streaming :meth:`OllamaProvider.chat`
    classify a failure the same way (a 404 fatal, a 429/503 or a dropped
    connection retryable, a crashed runner retryable once) and a consumer reads
    it back with :func:`~chaoscypher_core.utils.stream_errors.error_from_stream_chunk`.
    """
    return error_chunk(
        classify_llm_exception(exc, provider="ollama", model=model, patterns=OLLAMA_PATTERNS)
    )


def _metadata_chunk(chunk: Any, previous: Any) -> Any:
    """The chunk usage and timings are read from: the last one carrying metadata.

    langchain-ollama 1.1 puts the token counts and ``done_reason`` on the
    final Ollama message and then yields one more, empty chunk with no
    metadata at all; reading the last chunk gave every streamed call
    ``usage {}`` and no provider timings (2026-10-05: benchmark rows with
    zero output tokens, truncation undetectable on streams).
    """
    if getattr(chunk, "usage_metadata", None) or getattr(chunk, "response_metadata", None):
        return chunk
    return previous if previous is not None else chunk


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider using LangChain.

    Supports:
    - Local Ollama models (Llama, Qwen, Mistral, etc.)
    - Streaming and non-streaming chat
    - Tool calling
    - Thinking mode (Qwen3)
    """

    _METADATA: ClassVar[PluginMetadata] = PluginMetadata(
        plugin_id="ollama",
        name="Ollama",
        description="Local Ollama server (LangChain).",
        version="1.0.0",
        author="Chaos Cypher, Inc.",
        category="llm_provider",
        builtin=True,
        tags=["streaming", "tool_calling", "embeddings", "thinking"],
        origin="builtin",
    )

    @property
    def metadata(self) -> PluginMetadata:
        """Return the plugin metadata declared on the class."""
        return self._METADATA

    def __init__(self, config: dict[str, Any]):
        """Initialize the instance.

        Args:
            config: Provider configuration containing API keys and model settings.

        """
        super().__init__(config)
        self.stream_chunk_timeout: float = float(config["stream_chunk_timeout"])
        self.base_url = config["base_url"]
        self.chat_model = config["ollama_chat_model"]

        # Store defaults for instance creation
        self._default_temperature = config.get("ai_temperature")
        self._default_max_tokens = config.get("ai_max_tokens")
        self._health_check_timeout: float = float(config["ollama_health_check_timeout"])
        self._recovery_delay: float = float(config["ollama_recovery_delay"])

        # Cache for ChatOllama instances:
        # (temperature, max_tokens, reasoning, seed, repeat_penalty) -> instance
        self._llm_cache: dict[
            tuple[float | None, int | None, bool, int | None, float | None], BaseChatModel
        ] = {}

        # Cached httpx client for health-check requests; lazy-initialised on
        # first call to check_health() so no event loop is needed at __init__
        # time. Timeout is sourced from settings (ollama_health_check) via
        # config["ollama_health_check_timeout"] which is already stored in
        # self._health_check_timeout.
        self._health_client: httpx.AsyncClient | None = None

        # Track whether the current model supports reasoning/thinking mode.
        # Flips to False after the first "does not support thinking" error,
        # so subsequent calls skip the wasteful try→fail→retry cycle.
        self._model_supports_reasoning: bool = True

        # Initialize LangChain model
        self.llm = self._init_llm()

    def _call_decoding(self) -> CallDecoding:
        """The seed and per-call options a call made now sends (the active overrides, else configured)."""
        return CallDecoding(
            seed=effective_seed(self.config.get("seed")),
            repeat_penalty=ollama_call_options()["repeat_penalty"],
        )

    def _default_decoding(self) -> CallDecoding:
        """The decoding of the default instance: the configured seed, no per-call options."""
        return CallDecoding(seed=self.config.get("seed"), repeat_penalty=None)

    async def check_health(self) -> bool:
        """Check if Ollama is responsive.

        Makes a lightweight request to the Ollama API to verify it's running
        and accepting requests. Used before retrying failed requests to avoid
        wasting tokens on immediate retries when Ollama is overloaded.

        Returns:
            True if Ollama is healthy and responsive, False otherwise.

        """
        try:
            if self._health_client is None:
                self._health_client = httpx.AsyncClient(
                    timeout=self._health_check_timeout,
                )
            response = await self._health_client.get(f"{self.base_url}/api/tags")
            is_healthy = response.status_code == 200
            if not is_healthy:
                logger.warning(
                    "ollama_health_check_failed",
                    status_code=response.status_code,
                    base_url=self.base_url,
                )
            return is_healthy
        except Exception as e:
            logger.warning(
                "ollama_health_check_error",
                error_type=type(e).__name__,
                error_message=str(e),
                base_url=self.base_url,
            )
            return False

    def _init_llm(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning: bool = True,
        *,
        decoding: CallDecoding | None = None,
    ) -> BaseChatModel:
        """Initialize LangChain ChatOllama.

        Args:
            temperature: Optional temperature override. Uses default if None.
            max_tokens: Optional max_tokens override. Uses default if None.
            reasoning: Whether to enable reasoning/thinking mode. Defaults to True
                for thinking-capable models (e.g., Qwen3). Set to False for models
                that don't support thinking (e.g., Llama, Mistral instruct models).
            decoding: The seed and per-call options to send; None: the
                configured seed and none (the default instance).

        Returns:
            Configured ChatOllama instance.

        """
        # Use provided params or fall back to defaults
        temp = temperature if temperature is not None else self._default_temperature
        tokens = max_tokens if max_tokens is not None else self._default_max_tokens

        # Base kwargs
        kwargs = {
            "model": self.chat_model,
            "base_url": self.base_url,
        }

        if temp is not None:
            kwargs["temperature"] = temp
        if tokens is not None:
            kwargs["num_predict"] = tokens
        # A pinned seed makes repeated runs reproducible; None lets Ollama pick
        # one per request. A deliberate re-ask (regenerate, a validation retry)
        # sends its resampled seed through ``decoding_override`` (audit wave 5),
        # resolved by chat() when it is called (audit wave 6).
        sent = decoding if decoding is not None else self._default_decoding()
        if sent.seed is not None:
            kwargs["seed"] = sent.seed
        # Only an overridden repeat penalty is sent (the retry ladder's second
        # rung); every other call keeps the Modelfile's default.
        if sent.repeat_penalty is not None:
            kwargs["repeat_penalty"] = sent.repeat_penalty

        # Ollama-specific performance options
        # These can be passed to improve GPU utilization
        # (the per-card three through the helper a benchmark row records them with)
        if self.config.get("ollama_num_ctx"):
            kwargs["num_ctx"] = self.config.get("ollama_num_ctx")
        kwargs.update({k: v for k, v in ollama_runtime_options(self.config).items() if v})

        # Always pass `reasoning` explicitly. LangChain's ChatOllama defaults to None,
        # which maps to `think=null` on the Ollama API and means "use the model default" —
        # so thinking-capable models (Qwen3, deepseek-r1, …) keep thinking even when the
        # caller asked us to disable it. Passing reasoning=False sends `think=false` and
        # actually disables thinking. reasoning=True enables it via additional_kwargs.
        # Non-thinking models (Llama, Mistral instruct) raise on reasoning=True; the
        # caller falls back via _invoke_with_thinking_fallback in that case.
        kwargs["reasoning"] = reasoning

        # Silence timeout on every socket read, streaming or not. Measured
        # 2026-09-23: olmo-3.1:32b sat 75 minutes on its first chunk at 100%
        # GPU with zero bytes on the wire; the per-chunk stream timeout never
        # ran because the non-streaming path does not go through it. httpx
        # enforces this one on both paths, and a progressing generation never
        # trips it. Same setting the cloud providers already honour.
        request_timeout = self.config.get("llm_request_timeout")
        if request_timeout is not None:
            kwargs["client_kwargs"] = {"timeout": float(request_timeout)}

        logger.debug(
            "ollama_initialized",
            provider="ollama",
            model=self.chat_model,
            base_url=self.base_url,
            temperature=temp,
            max_tokens=tokens,
        )
        return ChatOllama(**kwargs)

    def _get_cached_llm(
        self,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        reasoning: bool = True,
        decoding: CallDecoding | None = None,
    ) -> BaseChatModel:
        """Get or create a cached ChatOllama instance for the given parameters.

        Ollama 0.6.x doesn't support dynamic temperature/max_tokens via bind(),
        so we cache separate ChatOllama instances for different parameter combinations.

        Args:
            temperature: Optional temperature override.
            max_tokens: Optional max_tokens override.
            reasoning: Whether to enable reasoning/thinking mode.
            decoding: The seed and per-call options the call sends (resolved
                by ``chat()``); None: resolved now.

        Returns:
            ChatOllama instance configured with the requested parameters.

        """
        # The seed and options this call sends: the configured ones unless overridden
        sent = decoding if decoding is not None else self._call_decoding()
        # No overrides + default reasoning = use default instance
        if (
            temperature is None
            and max_tokens is None
            and reasoning
            and sent == self._default_decoding()
        ):
            if self.llm is None:
                raise RuntimeError("LLM not initialized")
            return self.llm

        cache_key = (temperature, max_tokens, reasoning, sent.seed, sent.repeat_penalty)
        if cache_key in self._llm_cache:
            return self._llm_cache[cache_key]

        # Create and cache new instance
        logger.debug(
            "ollama_llm_instance_created",
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning=reasoning,
        )
        llm = self._init_llm(
            temperature=temperature, max_tokens=max_tokens, reasoning=reasoning, decoding=sent
        )
        self._llm_cache[cache_key] = llm
        return llm

    def _extract_thinking_from_tags(self, content: str) -> tuple[str, str | None]:
        """Extract and strip <think>...</think> tags from content.

        Returns the cleaned content and extracted thinking text (if any).
        Validates thinking content to discard garbage (dots/spaces/punctuation).

        Args:
            content: Raw content potentially containing think tags.

        Returns:
            Tuple of (cleaned_content, thinking_text_or_none).

        """
        if not content or "<think>" not in content:
            return content, None

        # Extract thinking content from <think> tags
        thinking = None
        think_matches = _THINK_TAG_EXTRACT_PATTERN.findall(content)
        if think_matches:
            extracted = "\n".join(match.strip() for match in think_matches)
            if extracted:
                thinking = extracted
                logger.debug(
                    "ollama_thinking_extracted_from_tags",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                )

        original_length = len(content)
        cleaned = _THINK_TAG_PATTERN.sub("", content).strip()
        if len(cleaned) < original_length:
            logger.debug(
                "ollama_think_tags_stripped",
                provider="ollama",
                model=self.chat_model,
                original_length=original_length,
                stripped_length=len(cleaned),
            )

        # Validate thinking — discard if just dots/spaces/punctuation
        if thinking:
            scrubbed = thinking.strip().replace(".", "").replace(" ", "").replace(",", "")
            if not scrubbed:
                logger.debug(
                    "ollama_thinking_discarded_garbage",
                    provider="ollama",
                    model=self.chat_model,
                    original_thinking=thinking[:50],
                )
                thinking = None

        return cleaned, thinking

    def _get_llm_for_request(
        self,
        temperature: float | None,
        max_tokens: int | None,
        *,
        enable_thinking: bool = True,
        decoding: CallDecoding | None = None,
    ) -> BaseChatModel:
        """Get the right LLM instance for a request, respecting reasoning support.

        Args:
            temperature: Optional temperature override.
            max_tokens: Optional max_tokens override.
            enable_thinking: Whether to enable thinking/reasoning mode.
                When False, returns an LLM instance without reasoning
                so the model doesn't generate thinking tokens.
            decoding: The seed and per-call options (resolved by ``chat()``);
                None: resolved now.

        Returns:
            ChatOllama instance, with or without reasoning depending on model support.

        """
        if not enable_thinking or not self._model_supports_reasoning:
            return self._get_cached_llm(temperature, max_tokens, reasoning=False, decoding=decoding)
        return self._get_cached_llm(temperature, max_tokens, decoding=decoding)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        stream: bool = False,
        enable_thinking: bool = False,
        high_priority: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Send chat request to Ollama using LangChain.

        The whole decoding (temperature, seed, repeat penalty) is resolved
        here, before any await, so a stream iterated after the caller's
        ``decoding_override`` block closed still sends what the call was made
        under (audit wave 6).
        """
        # a regenerate raises the temperature of the calls that name none (decoding_override)
        temperature = effective_temperature(temperature)
        decoding = self._call_decoding()
        # Convert messages to LangChain format
        lc_messages = convert_to_langchain_messages(messages)

        try:
            if stream:
                return cast(
                    "dict[str, Any]",
                    self._wrap_stream_with_semaphore(
                        self._stream_chat(
                            lc_messages,
                            tools,
                            enable_thinking,
                            temperature,
                            max_tokens,
                            decoding=decoding,
                        ),
                        high_priority=high_priority,
                    ),
                )

            # Non-streaming: optionally wrap with semaphore
            logger.debug(
                "ollama_request_sent",
                provider="ollama",
                model=self.chat_model,
                tools=bool(tools),
                enable_thinking=enable_thinking,
            )
            if self.enable_priority:
                async with self.semaphore.acquire(high_priority=high_priority):
                    result = await self._make_sync_request(
                        lc_messages,
                        tools,
                        enable_thinking,
                        temperature,
                        max_tokens,
                        decoding=decoding,
                    )
            else:
                result = await self._make_sync_request(
                    lc_messages, tools, enable_thinking, temperature, max_tokens, decoding=decoding
                )
            logger.debug(
                "ollama_response_received",
                provider="ollama",
                model=self.chat_model,
                content_length=len(result.get("content", "")),
                has_tool_calls=bool(result.get("tool_calls")),
                has_thinking=bool(result.get("thinking")),
            )
            return result

        except LLMError:
            # Re-raise our own errors as-is (e.g. ToolCallingNotSupportedError
            # from the thinking fallback) so downstream fatal/no-retry
            # classification keeps working — matches the other providers.
            raise
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e) if str(e) else repr(e)
            logger.exception(
                "ollama_chat_failed",
                provider="ollama",
                model=self.chat_model,
                error_type=error_type,
                error_message=error_msg,
            )

            # One classifier for this path and the stream (audit wave 5): a 429 or
            # 503 here was a non-retryable LLMError while the stream retried it.
            # Connection and timeout failures are transient (an Ollama restart, a
            # busy GPU, a host waking from suspend): the retryable LLMServiceError
            # the cloud providers raise, so the queue retries the chunk.
            docker_url = "http://host.docker.internal:11434"
            raise classify_llm_exception(
                e,
                provider="ollama",
                model=self.chat_model,
                patterns=OLLAMA_PATTERNS,
                connection_reason=(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Please ensure Ollama is running and accessible. "
                    f"If using Docker, try '{docker_url}'."
                ),
            ) from e

    def _is_tool_calling_error(self, error: Exception, has_tools: bool) -> bool:
        """Check if an error indicates tool calling is not supported.

        Args:
            error: The exception that occurred
            has_tools: Whether tools were included in the request

        Returns:
            True if this appears to be a tool calling compatibility issue

        """
        if not has_tools:
            return False

        error_str = str(error).lower()

        # Known error patterns that DEFINITIVELY indicate tool calling is not supported
        # NOTE: "unexpected end of json input" is NOT included here because it's a
        # generic server error that can happen for many reasons (timeout, memory, etc.)
        # and many models that DO support tools can trigger this error under load
        tool_error_patterns = [
            "does not support tools",
            "tool calling not supported",
            "function calling not supported",
            "tools are not supported",
            "invalid tool",
            "unknown tool",
        ]

        return any(pattern in error_str for pattern in tool_error_patterns)

    async def _invoke_with_thinking_fallback(
        self,
        llm: Any,
        messages: list,
        enable_thinking: bool,
        has_tools: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        decoding: CallDecoding | None = None,
    ) -> Any:
        """Invoke LLM with automatic fallback for models that don't support thinking.

        Tries with reasoning=True first (for models like Qwen3 that support thinking).
        If the model doesn't support it, automatically retries without reasoning by
        creating a new LLM instance without the reasoning parameter.

        Args:
            llm: The LangChain LLM (possibly with tools bound)
            messages: LangChain messages
            enable_thinking: Whether thinking mode was requested
            has_tools: Whether tools are being used in this request
            tools: Original tools list (needed to rebind on fallback LLM)
            temperature: Temperature setting (needed to create fallback LLM)
            max_tokens: Max tokens setting (needed to create fallback LLM)
            decoding: The call's seed and per-call options (for the fallback LLM)

        Returns:
            LangChain response object

        Raises:
            ToolCallingNotSupportedError: If the model doesn't support tool calling

        """
        # Handle reasoning/thinking mode:
        # NOTE: We intentionally DON'T bind reasoning=True because:
        # 1. It can suppress native <think> tag output in models like qwen3
        # 2. LangChain's reasoning_content extraction is unreliable (returns garbage)
        # Instead, we let models output <think> tags naturally and extract from content
        try:
            llm_with_reasoning = llm
            logger.debug(
                "ollama_invoke_thinking_via_tags",
                model=self.chat_model,
                enable_thinking=enable_thinking,
            )
            return await llm_with_reasoning.ainvoke(messages)
        except ResponseError as e:
            error_str = str(e)

            # Check if this is a "does not support thinking" error
            if "does not support thinking" in error_str:
                # Remember so future calls skip the reasoning attempt entirely
                self._model_supports_reasoning = False
                logger.info(
                    "ollama_reasoning_not_supported_fallback",
                    model=self.chat_model,
                    error=error_str,
                    action="reasoning_disabled_for_future_calls",
                )
                # Create a NEW LLM instance without reasoning (the original has reasoning=True baked in)
                llm_no_reasoning = self._get_cached_llm(
                    temperature, max_tokens, reasoning=False, decoding=decoding
                )

                # Rebind tools if the original request had tools
                if tools:
                    llm_no_reasoning = llm_no_reasoning.bind_tools(tools, tool_choice="any")

                # Small delay to let Ollama recover from the failed request
                await asyncio.sleep(self._recovery_delay)

                try:
                    return await llm_no_reasoning.ainvoke(messages)
                except ResponseError as e2:
                    # Check if the retry also failed due to tool calling issues
                    if self._is_tool_calling_error(e2, has_tools):
                        raise ToolCallingNotSupportedError(
                            model=self.chat_model, provider="ollama"
                        ) from e2
                    raise

            # Check for tool calling errors on first attempt
            if self._is_tool_calling_error(e, has_tools):
                raise ToolCallingNotSupportedError(model=self.chat_model, provider="ollama") from e

            # Re-raise other errors
            raise

    async def _make_sync_request(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        enable_thinking: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        decoding: CallDecoding | None = None,
    ) -> dict[str, Any]:
        """Make synchronous (non-streaming) request using LangChain."""
        decoding = decoding if decoding is not None else self._call_decoding()
        llm_to_use = self._get_llm_for_request(
            temperature, max_tokens, enable_thinking=enable_thinking, decoding=decoding
        )

        # Bind tools if provided
        # Use tool_choice="any" to force the model to use native tool calling
        # rather than outputting JSON as text content
        has_tools = bool(tools)
        if tools:
            llm_to_use = llm_to_use.bind_tools(tools, tool_choice="any")

        llm_with_tools = llm_to_use

        # Enable thinking mode via LangChain's reasoning parameter
        # reasoning=True extracts thinking to additional_kwargs['reasoning_content']
        # Not all models support this - we try with reasoning first, fall back without
        response = await self._invoke_with_thinking_fallback(
            llm_with_tools,
            lc_messages,
            enable_thinking,
            has_tools=has_tools,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            decoding=decoding,
        )

        # Extract tool calls if present
        tool_calls = None
        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_calls = format_tool_calls_response(response.tool_calls)

        # Extract usage/token info, finish_reason, and provider-native timings if available
        usage = {}
        provider_timings: dict[str, Any] = {}
        raw_finish_reason: str | None = None
        if hasattr(response, "response_metadata"):
            metadata = response.response_metadata
            usage = {
                "prompt_tokens": metadata.get("prompt_eval_count", 0),
                "completion_tokens": metadata.get("eval_count", 0),
                "total_tokens": metadata.get("prompt_eval_count", 0)
                + metadata.get("eval_count", 0),
            }
            provider_timings = extract_provider_timings(response)
            # Ollama exposes the stop reason on ainvoke() via done_reason in response_metadata
            raw_finish_reason = metadata.get("done_reason")

        # Extract thinking from response
        # LangChain exposes thinking via reasoning parameter in additional_kwargs['reasoning_content']
        thinking = None
        if hasattr(response, "additional_kwargs") and response.additional_kwargs:
            thinking = response.additional_kwargs.get("reasoning_content")
            if thinking:
                logger.debug(
                    "thinking_extracted",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                )

        # Get content from response
        content = response.content

        # Extract and strip <think>...</think> tags from content
        # For qwen3 and similar models, <think> tags are the authoritative source
        content, tag_thinking = self._extract_thinking_from_tags(content)
        if tag_thinking:
            thinking = tag_thinking  # Prefer tags over reasoning_content

        # Debug logging for empty content with non-zero completion tokens
        # Only warn if there are NO tool calls - empty content WITH tool calls is expected
        # (when using tool calling, the response goes to tool_calls, not content)
        if not content and usage.get("completion_tokens", 0) > 0 and not tool_calls:
            additional_kwargs = (
                response.additional_kwargs
                if hasattr(response, "additional_kwargs") and response.additional_kwargs
                else {}
            )
            logger.warning(
                "ollama_empty_content_with_tokens",
                provider="ollama",
                model=self.chat_model,
                completion_tokens=usage.get("completion_tokens", 0),
                has_thinking=bool(thinking),
                additional_kwargs_keys=list(additional_kwargs.keys()),
                response_type=type(response).__name__,
            )

            # Qwen3 models may put all output into reasoning_content when reasoning=False
            # but the model still decides to "think". Fall back to reasoning content as main content.
            if thinking and not content:
                logger.info(
                    "ollama_using_thinking_as_content_fallback",
                    provider="ollama",
                    model=self.chat_model,
                    thinking_length=len(thinking),
                    reason="content_empty_but_thinking_present",
                )
                content = thinking
                thinking = None  # Don't return thinking separately since we used it as content

        return {
            "content": content,
            "thinking": thinking,
            "tool_calls": tool_calls,
            "model": self.chat_model,
            "provider": "ollama",
            "usage": usage,
            "provider_timings": provider_timings,
            "finish_reason": normalize_finish_reason(raw_finish_reason),
        }

    async def _astream_with_timeout(
        self,
        llm: Any,
        messages: list,
        timeout: float | None = None,
    ) -> Any:
        """Wrap LLM astream with per-chunk timeout to detect dead connections.

        Applies a timeout between successive streaming chunks. If no data
        arrives within the timeout period, raises ``asyncio.TimeoutError``
        instead of hanging indefinitely (which happens when the Ollama model
        expires or the connection drops silently).

        Args:
            llm: LangChain chat model instance.
            messages: LangChain messages to send.
            timeout: Seconds to wait for each chunk. Defaults to STREAM_CHUNK_TIMEOUT.

        Yields:
            AIMessageChunk objects from the LLM stream.

        Raises:
            asyncio.TimeoutError: If no chunk arrives within the timeout period.

        """
        chunk_timeout = timeout or self.stream_chunk_timeout
        ait = llm.astream(messages).__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(ait.__anext__(), timeout=chunk_timeout)
                yield chunk
            except StopAsyncIteration:
                break
            except TimeoutError:
                logger.exception(
                    "ollama_stream_chunk_timeout",
                    model=self.chat_model,
                    timeout_seconds=chunk_timeout,
                    hint="Ollama may have stopped responding (model expired, OOM, or connection lost)",
                )
                raise

    async def _stream_chat(  # noqa: C901, PLR0912 - streaming chunk dispatcher; each branch handles a distinct Ollama event type
        self,
        lc_messages: list,
        tools: list[dict] | None,
        enable_thinking: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        decoding: CallDecoding | None = None,
    ) -> Any:
        """Stream chat responses using LangChain.

        ``decoding`` is what ``chat()`` resolved when it was called; this body
        runs only when the stream is first iterated, possibly after the
        caller's override block closed (audit wave 6). None: resolved now.
        """
        decoding = decoding if decoding is not None else self._call_decoding()
        llm_to_use = self._get_llm_for_request(
            temperature, max_tokens, enable_thinking=enable_thinking, decoding=decoding
        )

        # Bind tools if provided
        if tools:
            llm_to_use = llm_to_use.bind_tools(tools, tool_choice="any")

        # Streaming accumulators
        accumulated_content = ""
        accumulated_thinking = ""
        tool_calls = None
        usage: dict[str, int] = {}
        last_chunk = None
        raw_finish_reason: str | None = None

        try:
            logger.debug(
                "ollama_stream_thinking_via_tags",
                model=self.chat_model,
                enable_thinking=enable_thinking,
            )
            first_chunk_logged = False
            async for chunk in self._astream_with_timeout(llm_to_use, lc_messages):
                last_chunk = _metadata_chunk(chunk, last_chunk)
                # Ollama emits ``done_reason`` on the final chunk's
                # response_metadata. Capture as we go so
                # ``raw_finish_reason`` reflects the last seen value.
                chunk_finish = extract_streaming_finish_reason(chunk)
                if chunk_finish:
                    raw_finish_reason = chunk_finish
                if not first_chunk_logged:
                    first_chunk_logged = True
                    logger.debug(
                        "ollama_stream_first_chunk",
                        provider="ollama",
                        model=self.chat_model,
                        chunk_type=type(chunk).__name__,
                        has_additional_kwargs=hasattr(chunk, "additional_kwargs"),
                        additional_kwargs_keys=list(chunk.additional_kwargs.keys())
                        if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs
                        else [],
                        content_preview=chunk.content[:100]
                        if hasattr(chunk, "content") and chunk.content
                        else None,
                    )

                # Extract thinking from LangChain's reasoning parameter
                if hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
                    chunk_thinking = chunk.additional_kwargs.get("reasoning_content")
                    if chunk_thinking:
                        accumulated_thinking += chunk_thinking
                        if accumulated_thinking.strip().replace(".", "").replace(" ", ""):
                            yield {"type": "thinking_delta", "accumulated": accumulated_thinking}

                # Extract content delta
                delta_content = chunk.content if hasattr(chunk, "content") else ""
                if delta_content:
                    accumulated_content += delta_content
                    yield {
                        "type": "content",
                        "delta": delta_content,
                        "accumulated": accumulated_content,
                    }

                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    tool_calls = format_tool_calls_response(chunk.tool_calls)

            # Finalize: extract think tags and usage
            final_content, tag_thinking = self._extract_thinking_from_tags(accumulated_content)
            if tag_thinking:
                accumulated_thinking = tag_thinking
            elif accumulated_thinking:
                # Validate accumulated reasoning_content
                _, validated = self._extract_thinking_from_tags(
                    f"<think>{accumulated_thinking}</think>"
                )
                accumulated_thinking = validated or ""

            usage = extract_streaming_usage(last_chunk) or usage
            provider_timings = extract_provider_timings(last_chunk)

            logger.info(
                "ollama_stream_complete",
                provider="ollama",
                model=self.chat_model,
                content_length=len(final_content) if final_content else 0,
                thinking_length=len(accumulated_thinking) if accumulated_thinking else 0,
                has_tool_calls=bool(tool_calls),
            )

            yield {
                "type": "done",
                "content": final_content,
                "thinking": accumulated_thinking if accumulated_thinking else None,
                "tool_calls": tool_calls,
                "usage": usage,
                "provider_timings": provider_timings,
                "model": self.chat_model,
                "provider": "ollama",
                "finish_reason": normalize_finish_reason(raw_finish_reason),
            }

        except ResponseError as e:
            if "does not support thinking" in str(e):
                async for chunk in self._stream_with_fallback(
                    lc_messages,
                    tools,
                    temperature,
                    max_tokens,
                    accumulated_content,
                    tool_calls,
                    usage,
                    decoding=decoding,
                ):
                    yield chunk
            else:
                logger.exception(
                    "ollama_streaming_failed",
                    provider="ollama",
                    model=self.chat_model,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                yield stream_error_chunk(e, self.chat_model)

        except Exception as e:
            logger.exception(
                "ollama_streaming_failed",
                provider="ollama",
                model=self.chat_model,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            yield stream_error_chunk(e, self.chat_model)

    async def _stream_with_fallback(
        self,
        lc_messages: list,
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
        accumulated_content: str,
        tool_calls: list[dict] | None,
        usage: dict[str, int],
        *,
        decoding: CallDecoding | None = None,
    ) -> Any:
        """Retry streaming without reasoning after a 'does not support thinking' error.

        Args:
            lc_messages: LangChain messages.
            tools: Tools to bind (if any).
            temperature: Temperature override.
            max_tokens: Max tokens override.
            accumulated_content: Content accumulated before failure.
            tool_calls: Tool calls accumulated before failure.
            usage: Usage dict accumulated before failure.
            decoding: The call's seed and per-call options; None: resolved now.

        Yields:
            Streaming chunks and final done chunk.

        """
        self._model_supports_reasoning = False
        logger.info(
            "ollama_stream_reasoning_not_supported_fallback",
            model=self.chat_model,
            action="reasoning_disabled_for_future_calls",
        )

        llm_no_reasoning = self._get_cached_llm(
            temperature, max_tokens, reasoning=False, decoding=decoding
        )
        if tools:
            llm_no_reasoning = llm_no_reasoning.bind_tools(tools, tool_choice="any")

        await asyncio.sleep(self._recovery_delay)

        last_chunk = None
        raw_finish_reason: str | None = None
        async for chunk in self._astream_with_timeout(llm_no_reasoning, lc_messages):
            last_chunk = _metadata_chunk(chunk, last_chunk)
            chunk_finish = extract_streaming_finish_reason(chunk)
            if chunk_finish:
                raw_finish_reason = chunk_finish
            delta_content = chunk.content if hasattr(chunk, "content") else ""
            if delta_content:
                accumulated_content += delta_content
                yield {
                    "type": "content",
                    "delta": delta_content,
                    "accumulated": accumulated_content,
                }
            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                tool_calls = format_tool_calls_response(chunk.tool_calls)

        usage = extract_streaming_usage(last_chunk) or usage
        provider_timings = extract_provider_timings(last_chunk)
        final_content, fallback_thinking = self._extract_thinking_from_tags(accumulated_content)

        yield {
            "type": "done",
            "content": final_content,
            "thinking": fallback_thinking,
            "tool_calls": tool_calls,
            "usage": usage,
            "provider_timings": provider_timings,
            "model": self.chat_model,
            "provider": "ollama",
            "finish_reason": normalize_finish_reason(raw_finish_reason),
        }
