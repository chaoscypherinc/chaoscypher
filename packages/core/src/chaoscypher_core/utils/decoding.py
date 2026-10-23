# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Resampling under pinned decoding: a retry asks with another seed, never another temperature.

Decoding is pinned by default (temperature 0, seed 42, 2026-10-06), so asking
the same thing again gives the same output: a chat *regenerate* returned the
same answer and the schema extractor's validation retries repeated the same
failed output (audit wave 5). Every caller that asks again on purpose takes
its seed from :func:`resample_decoding` (attempt 0 is the configured seed, so
first answers stay reproducible) and sends it for the duration of the call
with :func:`seed_override`, which the Ollama provider reads when its
``chat()`` is called (the only provider that sends a seed).

A validation retry never touches the temperature: it stays as deterministic
as the first try, so it is reproducible and cacheable (a provider decoding at
temperature 0 decodes greedily, where the seed has no effect: such a retry
differs only because its prompt does, the extractor appending the validation
errors). A chat *regenerate* is a user asking for a different answer, so it
also raises the temperature to ``chat.regenerate_temperature`` when the
configured one is lower (:func:`turn_decoding`; Denis, 2026-10-06); the
first answer and ordinary turns keep the pinned decoding. The temperature is
sent through :func:`decoding_override`, which ``LLMProvider.chat`` reads
(:func:`effective_temperature`) for a call that names none.

The same override carries a repeat penalty (:func:`effective_repeat_penalty`):
the extraction retry ladder's second rung asks at a small temperature, where a
loop can repeat without one (audit wave 5). The Ollama provider sends it as
``options.repeat_penalty`` only when one is overridden, so every other call
keeps the Modelfile's default; no cloud provider takes one.

A provider resolves all of these once, when its ``chat()`` is called, before
any await: a stream iterated after the override block has closed still sends
the decoding the call was made under (audit wave 6: the Ollama provider read
the temperature in ``chat()`` and the seed when the stream started).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Iterator


_UNSET = object()

_SEED_OVERRIDE: ContextVar[object] = ContextVar("llm_seed_override", default=_UNSET)
"""The seed the current call sends instead of the configured one (:func:`seed_override`)."""

_TEMPERATURE_OVERRIDE: ContextVar[object] = ContextVar("llm_temperature_override", default=None)
"""The temperature a call that names none sends (:func:`decoding_override`); None: the configured."""

_REPEAT_PENALTY_OVERRIDE: ContextVar[object] = ContextVar(
    "llm_repeat_penalty_override", default=None
)
"""The repeat penalty a call sends (:func:`decoding_override`); None: none sent (the model's default)."""


@dataclass(frozen=True)
class TurnDecoding:
    """How one chat turn decodes: the seed, the temperature and which re-ask it is.

    Attributes:
        seed: The seed sent (:func:`resample_decoding`).
        temperature: The temperature sent; the configured one on a first answer.
        attempt: 0 for the first answer, ``n`` for the n-th regenerate.
    """

    seed: int | None
    temperature: float | None
    attempt: int

    def record(self) -> dict[str, Any]:
        """What the message records (``extra_metadata["decoding"]``)."""
        return {"seed": self.seed, "temperature": self.temperature, "attempt": self.attempt}


def turn_decoding(
    *,
    base_seed: int | None,
    configured_temperature: float | None,
    regenerate_temperature: float,
    attempt: int,
) -> TurnDecoding:
    """The decoding of chat turn ``attempt``: pinned for the first answer, resampled for a regenerate.

    Attempt 0 keeps the configured seed and temperature. Attempt ``n`` asks
    with ``base_seed + n`` at ``max(configured, regenerate_temperature)``: at
    a pinned temperature of 0 a new seed alone gives the same answer.
    """
    seed = resample_decoding(base_seed, attempt)
    if attempt == 0:
        return TurnDecoding(seed=seed, temperature=configured_temperature, attempt=0)
    temperature = max(configured_temperature or 0.0, regenerate_temperature)
    return TurnDecoding(seed=seed, temperature=temperature, attempt=attempt)


def resample_decoding(base_seed: int | None, attempt: int) -> int | None:
    """The seed attempt ``attempt`` asks with: ``base_seed + attempt``.

    Attempt 0 is the first answer (the configured seed, unchanged); attempt
    ``n`` is the n-th deliberate re-ask (a regenerate, a validation retry).
    A ``base_seed`` of None means the provider picks a seed per request,
    which already resamples, so None stays None.

    Raises:
        ValueError: For a negative attempt.
    """
    if attempt < 0:
        msg = f"attempt must be >= 0, got {attempt}"
        raise ValueError(msg)
    if base_seed is None:
        return None
    return base_seed + attempt


@contextmanager
def seed_override(seed: int | None) -> Iterator[None]:
    """Send ``seed`` on every provider call made inside the block (:func:`decoding_override`)."""
    with decoding_override(seed=seed):
        yield


@contextmanager
def decoding_override(
    decoding: TurnDecoding | None = None,
    *,
    seed: int | None | object = _UNSET,
    temperature: float | None | object = _UNSET,
    repeat_penalty: float | None | object = _UNSET,
) -> Iterator[None]:
    """Send this decoding on every provider call made inside the block (this task and its children).

    Either a chat turn's ``decoding`` (its seed, and its temperature on a
    re-ask: a first answer, attempt 0, keeps whatever temperature its calls
    name or the configured one), or the parts one by one. A part left out
    keeps what an enclosing block set (an inner override never cancels an
    outer one it does not name).

    Args:
        decoding: A chat turn's :class:`TurnDecoding`; not with the keywords.
        seed: The seed sent instead of the configured one (None: no seed, the
            provider picks one per request).
        temperature: The temperature a call that names none sends (None: the
            configured one).
        repeat_penalty: The repeat penalty sent (``options.repeat_penalty``
            on Ollama; None: none sent, the model's default applies).

    Raises:
        TypeError: When ``decoding`` and a keyword are both given.
    """
    if decoding is not None:
        if any(part is not _UNSET for part in (seed, temperature, repeat_penalty)):
            msg = "decoding_override takes a TurnDecoding or keywords, not both"
            raise TypeError(msg)
        seed = decoding.seed
        temperature = decoding.temperature if decoding.attempt else None
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []
    for var, value in (
        (_SEED_OVERRIDE, seed),
        (_TEMPERATURE_OVERRIDE, temperature),
        (_REPEAT_PENALTY_OVERRIDE, repeat_penalty),
    ):
        if value is not _UNSET:
            tokens.append((var, var.set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def effective_temperature(requested: float | None) -> float | None:
    """The temperature a call sends: the one it names, else the active override, else None (configured)."""
    if requested is not None:
        return requested
    return cast("float | None", _TEMPERATURE_OVERRIDE.get())


def effective_seed(configured: int | None) -> int | None:
    """The seed a provider sends now: the active :func:`seed_override`, else ``configured``."""
    override = _SEED_OVERRIDE.get()
    if override is _UNSET:
        return configured
    return cast("int | None", override)


def effective_repeat_penalty() -> float | None:
    """The repeat penalty a provider sends now: the active :func:`decoding_override`'s, else None."""
    return cast("float | None", _REPEAT_PENALTY_OVERRIDE.get())


SEED_HONOURING_PROVIDERS: frozenset[str] = frozenset({"ollama"})
"""The LLM providers whose adapter passes the settings' ``seed`` to the model.

One definition for what a benchmark row records (``seed_honoured``), what
the completion cache keys and flags (a seeded hit is the deterministic
answer) and what the retry ladder resamples with. The Ollama adapter sets
the ``seed`` option; the OpenAI, Anthropic and Gemini adapters send none. A
test reads every built-in provider's source and fails when one starts (or
stops) sending it without this set following.
"""


def seed_honoured(provider_type: str | None) -> bool:
    """Whether ``provider_type``'s adapter sends the seed (:data:`SEED_HONOURING_PROVIDERS`)."""
    return provider_type in SEED_HONOURING_PROVIDERS


__all__ = [
    "SEED_HONOURING_PROVIDERS",
    "TurnDecoding",
    "decoding_override",
    "effective_repeat_penalty",
    "effective_seed",
    "effective_temperature",
    "resample_decoding",
    "seed_honoured",
    "seed_override",
    "turn_decoding",
]
