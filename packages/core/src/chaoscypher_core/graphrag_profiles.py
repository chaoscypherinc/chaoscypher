# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which GraphRAG levers a chat model runs with: a profile resolved per model, size band and domain.

The Phase 2 panel (2026-10-05) showed the levers split by model size: entity
aliases and evidence lines gained +4.5 and +3 on the models under 27B and
cost the 31B 3; more chunks cost the two largest models and nothing else.
One global default is a compromise, so ``GraphRAGSettings.profiles`` holds
named bundles of lever values and ``profile_rules`` says which bundle a chat
model gets, by exact model id, parameter-size band or corpus domain (a rule's
``domain`` matches the corpus's packs' label exactly or names one of its packs,
:func:`domain_matches`). The app
resolves it for the model it is about to call and the benchmark per row, so
every model is scored on the levers it would run with; the pins fingerprint
the resolved values, so each profile gets its own pins without more code.
"""

from __future__ import annotations

import re
from typing import Any

from chaoscypher_core.question_shapes import PRE_SHAPE_FIELDS
from chaoscypher_core.services.sources.engine.extraction.domains.packs import stored_name_packs
from chaoscypher_core.settings import (
    RESOLUTION_FIELDS,
    GraphRAGProfileRule,
    GraphRAGSettings,
    LLMSettings,
)


_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([bBmM])\b")


def parameter_size_b(text: str | None) -> float | None:
    """Billions of parameters from a size string ('30.5B', '9b', '800M'); None when absent.

    Reads what Ollama's ``/api/show`` reports as ``details.parameter_size``
    and what a model tag carries ('gemma4:31b', 'qwen3.5:9b').
    """
    if not text:
        return None
    m = _SIZE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    return value / 1000 if m.group(2).lower() == "m" else value


def chat_model_of(llm: LLMSettings) -> str:
    """The chat model the LLM settings name: ``<chat_provider>_chat_model``."""
    return str(getattr(llm, f"{llm.chat_provider}_chat_model", "") or "")


def rule_matches(
    rule: GraphRAGProfileRule,
    *,
    model: str | None,
    size_b: float | None,
    domain: str | None,
) -> bool:
    """Every condition the rule states holds for this model (a rule with none matches all)."""
    if rule.model is not None and (model is None or rule.model.lower() != model.lower()):
        return False
    if rule.min_params_b is not None and (size_b is None or size_b < rule.min_params_b):
        return False
    if rule.max_params_b is not None and (size_b is None or size_b >= rule.max_params_b):
        return False
    return rule.domain is None or domain_matches(rule.domain, domain)


def domain_matches(rule_domain: str, domain: str | None) -> bool:
    """Whether a rule's ``domain`` holds for a corpus extracted under ``domain`` (the packs' label).

    The corpus domain is the packs' label (``historical+literary``,
    ``domains.packs.packs_label``; one pack's label is its name, the floor's
    ``generic``). A rule matches the label exactly, or names one of its
    packs: ``domain: literary`` holds for ``literary`` and for
    ``historical+literary``. ``domain: generic`` holds only for the floor
    alone, which every corpus has but only a pack-less one is named by.
    Case is ignored.
    """
    if domain is None:
        return False
    wanted = rule_domain.lower()
    return wanted == domain.lower() or wanted in stored_name_packs(domain.lower())


def profile_for(
    settings: GraphRAGSettings,
    *,
    model: str | None,
    size_b: float | None = None,
    domain: str | None = None,
) -> str | None:
    """The name of the first rule's profile that matches, or None.

    ``size_b`` defaults to what the model tag says ('gemma4:31b' is 31).
    """
    if size_b is None:
        size_b = parameter_size_b(model)
    for rule in settings.profile_rules:
        if rule_matches(rule, model=model, size_b=size_b, domain=domain):
            return rule.profile
    return None


def resolve_graphrag_profile(
    settings: GraphRAGSettings,
    *,
    model: str | None,
    size_b: float | None = None,
    domain: str | None = None,
) -> GraphRAGSettings:
    """A copy of ``settings`` with the matching profile's levers applied; ``settings`` itself when none.

    The returned settings carry no profiles or rules of their own (they are
    resolved), so a resolved object resolves to itself. A profile naming a
    field that is not a lever, or a value the field rejects, raises
    ``ValueError`` from the settings model.
    """
    name = profile_for(settings, model=model, size_b=size_b, domain=domain)
    if name is None:
        return settings
    levers = settings.profiles.get(name)
    if levers is None:
        msg = f"GraphRAG profile {name!r} is named by a rule but not defined in profiles"
        raise ValueError(msg)
    bad = sorted(set(levers) & RESOLUTION_FIELDS)
    if bad:
        msg = f"GraphRAG profile {name!r} may not set {bad}"
        raise ValueError(msg)
    base: dict[str, Any] = settings.model_dump()
    base.update(levers)
    base["profiles"] = {}
    base["profile_rules"] = []
    return GraphRAGSettings(**base)


def apply_shape_levers(settings: GraphRAGSettings, shape: str) -> GraphRAGSettings:
    """The settings one question shape runs with: ``settings.shape_levers[shape]`` on top of them.

    Applied after :func:`resolve_graphrag_profile`, the same merge as a model
    profile, so a shape's bundle overrides the model's levers it names and
    keeps the rest. A shape without a bundle (``lookup``, or an unknown name)
    gets the settings back as they are; otherwise the result is a copy.

    Raises:
        ValueError: When the bundle sets a field a shape bundle may not
            (:data:`~chaoscypher_core.settings.RESOLUTION_FIELDS`, or
            :data:`~chaoscypher_core.question_shapes.PRE_SHAPE_FIELDS`: the
            settings refuse both when configured, so this guards settings
            built without validation), or
            an unknown lever or a value the field rejects (from the settings
            model, ``extra="forbid"``), so a typo is an error, not a new pin
            fingerprint.
    """
    levers = settings.shape_levers.get(shape) or {}
    if not levers:
        return settings
    bad = sorted(set(levers) & (RESOLUTION_FIELDS | set(PRE_SHAPE_FIELDS)))
    if bad:
        msg = f"GraphRAG shape_levers[{shape!r}] may not set {bad}"
        raise ValueError(msg)
    base: dict[str, Any] = settings.model_dump()
    base.update(levers)
    return GraphRAGSettings(**base)


__all__ = [
    "apply_shape_levers",
    "chat_model_of",
    "parameter_size_b",
    "profile_for",
    "resolve_graphrag_profile",
    "rule_matches",
]
