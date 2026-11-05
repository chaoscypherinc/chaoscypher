# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The summary model's wiring: the real provider behind a document summary.

:mod:`chaoscypher_core.services.sources.summaries` takes its provider through
a port (:class:`~chaoscypher_core.ports.llm.LLMProviderPort`); this module
builds the concrete one. Every caller that writes summaries (the Engine, the
queue's embed handler, the CLI's ``index_file`` and ``source summarize``) gets
its summary call from :func:`summary_chat`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.adapters.llm.provider import LLMProvider
from chaoscypher_core.services.sources import summaries


if TYPE_CHECKING:
    from chaoscypher_core.ports.llm import LLMProviderPort
    from chaoscypher_core.settings import EngineSettings


__all__ = ["summary_chat", "summary_llm_provider"]


def summary_llm_provider(settings: EngineSettings) -> LLMProvider:
    """A queue-free LLM provider for the summary: ``settings`` with no client retries.

    The summary is one try: the cloud clients' own retries (LangChain's
    ``max_retries``) are turned off on a copy of the settings; the caller's
    settings, and every other provider, keep the library default.
    """
    llm = settings.llm.model_copy(update={"client_max_retries": 0})
    return LLMProvider(settings=settings.model_copy(update={"llm": llm}))


def summary_chat(settings: EngineSettings) -> summaries.ChatCallback | None:
    """The summary call for ``settings``, or ``None`` when no model is configured.

    The provider is :func:`summary_llm_provider`'s, built on the first call
    and then reused: never the engine's or the CLI's own provider.
    """
    built: list[LLMProviderPort] = []

    def provider() -> LLMProviderPort:
        if not built:
            built.append(summary_llm_provider(settings))
        return built[0]

    return summaries.import_summary_chat(settings, provider)
