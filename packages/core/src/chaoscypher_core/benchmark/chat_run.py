# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""What a grounded-chat row runs with, decided once and recorded on the row.

Three producers ask the chat questions: the CLI's chat stage
(``GraphRAGChatDataset``), the MCP bridge's chat suite and the pin builder
(``pin_retrieval.py``). Until 2026-10-06 each decided the GraphRAG levers its
own way: the chat stage applied ``SWEEP_SET`` when the run's context connected
and then let a matching profile overwrite it, the pin builder applied
``SWEEP_SET`` and never resolved profiles, and the MCP suite did neither. A
row recorded none of it, so two runs whose prompts differed (the answer
policy, a sweep variant, a profile) looked identical apart from the app
version (audit H5, H11, R10).

:func:`resolve_run_graphrag` is the one resolution every producer calls:
the chat model's profile first, then the sweep's overrides on top, validated
by the settings model. :func:`run_provenance` is what every row records
about it, and :func:`retrieved_entity_ids` the entity ids a retrieval
showed the model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_origin


if TYPE_CHECKING:
    from chaoscypher_core.settings import GraphRAGSettings


SWEEP_SET_ENV = "SWEEP_SET"
"""The environment variable a sweep sets to run a context-shape variant (``graphrag.<field>=<v>;...``)."""

_PROMPT_SAMPLE_QUESTION = "Who is the sample entity's sister?"
_PROMPT_SAMPLE_CONTEXT = "- Sample Entity: a sample.\n[CHUNK C0 | sample.txt]\n[S1] A sample."


def graphrag_field_kinds() -> dict[str, Any]:
    """Field name -> plain type for ``GraphRAGSettings`` (a dict field is dict; Literal and Optional fold to str)."""
    from chaoscypher_core.settings import GraphRAGSettings

    kinds: dict[str, Any] = {}
    for name, field in GraphRAGSettings.model_fields.items():
        ann = field.annotation
        if ann in (int, float, bool):
            kinds[name] = ann
        elif get_origin(ann) is dict:
            kinds[name] = dict
        else:
            kinds[name] = str
    return kinds


def parse_sweep_set(spec: str | None, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """``graphrag.<field>=<value>;...`` into typed values; an unknown key or bad value is an error.

    ``fields`` maps a bare field name to its plain type (default:
    :func:`graphrag_field_kinds`); ``true``/``false`` parse as bools and a
    dict field takes a JSON object (``graphrag.shape_levers={"path": {"vector_reserve": 2}}``).
    A dict lever replaces the field as a whole: the object is the field's new
    value, never merged into the old one, so a sweep of ``shape_levers`` that
    names one shape drops the shipped bundles of the others.

    The profile fields (``profiles``, ``profile_rules``:
    :data:`~chaoscypher_core.benchmark.pins.RESOLUTION_GRAPHRAG_FIELDS`) are
    refused: the model's profile is resolved before the sweep applies
    (:func:`resolve_run_graphrag`), so a swept profile would change nothing
    and still read as a variant. ``router`` and ``shape_levers`` are read per
    question, after the sweep, and stay sweepable.

    Raises:
        ValueError: Naming the item that cannot be applied (the message starts ``SWEEP_SET``).
    """
    from chaoscypher_core.benchmark.pins import RESOLUTION_GRAPHRAG_FIELDS

    kinds = graphrag_field_kinds() if fields is None else fields
    out: dict[str, Any] = {}
    for item in (part.strip() for part in (spec or "").split(";") if part.strip()):
        key, sep, raw = item.partition("=")
        if not sep or not key.startswith("graphrag.") or not raw:
            msg = f"SWEEP_SET item {item!r} must look like graphrag.<field>=<value>"
            raise ValueError(msg)
        name = key[len("graphrag.") :]
        if name not in kinds:
            msg = f"SWEEP_SET: no GraphRAG setting {name!r} (have {', '.join(sorted(kinds))})"
            raise ValueError(msg)
        if name in RESOLUTION_GRAPHRAG_FIELDS:
            msg = (
                f"SWEEP_SET: {name} chooses a model's profile, which is resolved before the "
                "sweep applies: set the levers themselves (graphrag.<lever>=<value>)"
            )
            raise ValueError(msg)
        kind = kinds[name]
        if kind is bool:
            if raw.lower() not in ("true", "false"):
                msg = f"SWEEP_SET: {name} wants true/false, got {raw!r}"
                raise ValueError(msg)
            out[name] = raw.lower() == "true"
        elif kind is int:
            out[name] = int(raw)
        elif kind is float:
            out[name] = float(raw)
        elif kind is dict:
            out[name] = _json_object(name, raw)
        else:
            out[name] = raw
    return out


def _json_object(name: str, raw: str) -> dict[str, Any]:
    """A dict lever's SWEEP_SET value parsed as JSON; anything but a JSON object is an error."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        return value
    msg = f"SWEEP_SET: {name} is a dict lever and takes a JSON object, got {raw!r}"
    raise ValueError(msg)


def resolve_run_graphrag(
    graphrag: GraphRAGSettings,
    *,
    model: str | None,
    domain: str | None = None,
    sweep_set: str | None = None,
) -> GraphRAGSettings:
    """The GraphRAG settings one chat model runs with: its profile, then the sweep's overrides.

    The profile (:func:`~chaoscypher_core.graphrag_profiles.resolve_graphrag_profile`)
    is applied first and ``sweep_set`` last, so a variant sets what it names
    for every model of the panel, profile or not (until 2026-10-06 a profile
    silently overwrote a ``SWEEP_SET`` lever on the models it matched). The
    result is built through the settings model, so a value the field
    rejects (``triple_selection=conected``) is an error, not a new
    fingerprint. With no profile match and no overrides the settings come
    back as they are.

    Raises:
        ValueError: From :func:`parse_sweep_set` or from the settings model.
    """
    from chaoscypher_core.graphrag_profiles import resolve_graphrag_profile
    from chaoscypher_core.settings import GraphRAGSettings

    resolved = resolve_graphrag_profile(graphrag, model=model, domain=domain)
    overrides = parse_sweep_set(sweep_set)
    if not overrides:
        return resolved
    return GraphRAGSettings.model_validate({**resolved.model_dump(), **overrides})


def resolved_levers(graphrag: GraphRAGSettings) -> dict[str, Any]:
    """Every GraphRAG lever value, as JSON, without the profile definitions that chose them."""
    from chaoscypher_core.benchmark.pins import RESOLUTION_GRAPHRAG_FIELDS

    dumped = graphrag.model_dump(mode="json")
    return {k: v for k, v in sorted(dumped.items()) if k not in RESOLUTION_GRAPHRAG_FIELDS}


def lever_signature(levers: dict[str, Any] | None) -> str:
    """A short hash of a row's resolved levers; ``"unrecorded"`` for a row from before they were kept."""
    if not levers:
        return "unrecorded"
    blob = json.dumps(levers, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def prompt_template_sha256(graphrag: GraphRAGSettings) -> str:
    """A hash of the exact prompt the settings ask with, on a fixed question and context.

    Computed from :func:`~chaoscypher_core.benchmark.chat_prompt.grounded_chat_prompt`
    with the settings' citation rule and answer policy, so an edit to the
    template, the citation rule or the policy text moves it.
    """
    from chaoscypher_core.benchmark.chat_prompt import grounded_chat_prompt

    prompt = grounded_chat_prompt(
        _PROMPT_SAMPLE_QUESTION,
        _PROMPT_SAMPLE_CONTEXT,
        ask_for_citations=bool(graphrag.ask_for_citations),
        answer_policy=bool(graphrag.answer_policy),
    )
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


GRAPH_IDENTITY_KEYS: tuple[str, ...] = (
    "graph_snapshot_sha256",
    "extraction_domain",
    "corpus_sha256",
    "resolution",
    "extraction_recipe",
)
"""What a chat row records about *the graph it read*, and what every comparison holds to one value.

The sha256 of the snapshot's bytes, and what the graph's slot record says
shaped it: the extraction domain, the corpus bytes, the identity resolver's
recipe and the extraction recipe (audit wave 5: only pinned rows carried the
graph's sha256, from the pins, so two unpinned rows read from two graphs
compared as one measurement). Read from the slot record (``slot.json``) or
the pack's manifest, never from the dataset: an adopted slot names the
dataset's domain under today's key while its graph is the pack's.
"""

GRAPH_PROVENANCE_KEYS: tuple[str, ...] = (
    *GRAPH_IDENTITY_KEYS,
    "graph_cache_key",
    "graph_adopted_from",
)
"""Everything a chat row records about its graph: :data:`GRAPH_IDENTITY_KEYS` plus where it was served from.

The cache slot's key and the pack an adopted slot was seeded from are
recorded but not compared: the key scheme has changed under one graph
(a pack's export-time key is not today's) and how a graph reached a slot is
not the graph; the snapshot's sha256 and the identity fields are.
"""


def graph_identity(
    record: Mapping[str, Any] | None,
    *,
    snapshot_sha256: str | None,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """A chat row's graph provenance (:data:`GRAPH_PROVENANCE_KEYS`) from a slot record or a pack manifest.

    ``record`` is the slot's ``slot.json`` (the CLI) or the reference pack's
    manifest (the MCP suite), which name the same fields. A slot that
    ``install.py --adopt-current-recipe`` seeded records today's key fields
    plus ``adopted_from`` and the pack's own record (``adopted_record``):
    the row names the pack's, since that is the graph it read. A field the
    record does not hold is None (unknown), never filled in from elsewhere.
    """
    rec: Mapping[str, Any] = record or {}
    adopted = rec.get("adopted_from")
    own_record = rec.get("adopted_record")
    own: Mapping[str, Any] = own_record if adopted and isinstance(own_record, Mapping) else rec

    def text(name: str) -> str | None:
        """One identity field as a string, None when absent or empty."""
        value = own.get(name)
        return str(value) if value else None

    return {
        "graph_snapshot_sha256": snapshot_sha256 or None,
        "extraction_domain": text("extraction_domain"),
        "corpus_sha256": text("corpus_sha256"),
        "resolution": text("resolution"),
        "extraction_recipe": text("extraction_recipe"),
        "graph_cache_key": cache_key or None,
        "graph_adopted_from": str(adopted) if adopted else None,
    }


def run_provenance(
    graphrag: GraphRAGSettings,
    *,
    sweep_set: str | None = None,
    profile: str | None = None,
    retrieval_fingerprint: str | None = None,
    pins: dict[str, Any] | None = None,
    pack: str | None = None,
    resolution: str | None = None,
    extraction_recipe: str | None = None,
    queries_file: str | None = None,
    num_ctx: int | None = None,
    ai_max_tokens: int | None = None,
    loop_guard: dict[str, int] | None = None,
    graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What a chat row records about the settings it ran with (``extras["run_settings"]``).

    Args:
        graphrag: The resolved settings (:func:`resolve_run_graphrag`).
        sweep_set: The ``SWEEP_SET`` string the run applied, or None.
        profile: The profile name the chat model matched, or None.
        retrieval_fingerprint: The pins fingerprint of these settings.
        pins: The served pins' provenance (``RetrievalPins.provenance``), or None.
        pack: The reference pack the graph came from, when known.
        resolution: The identity resolver's recipe the graph was resolved with, when known.
        extraction_recipe: The extraction recipe the graph was built under, when known.
        queries_file: The question file a holdout run asked, None for the dataset's own.
        num_ctx: The context window the chat model ran with, when the provider has one
            (:func:`chat_context_window` of the *candidate's* provider settings).
        ai_max_tokens: The settings' output cap the chat call's ``num_predict`` never exceeds.
        loop_guard: The repetition guard's thresholds (:func:`loop_guard_thresholds`).
        graph: The graph the row read (:func:`graph_identity`); its
            :data:`GRAPH_PROVENANCE_KEYS` are recorded, every one of them
            (None when unknown). Its ``resolution`` and ``extraction_recipe``
            win over the separate arguments, which a caller without a slot
            record passes.

    Returns:
        A JSON-ready dict; ``levers`` holds every resolved lever.
    """
    levers = resolved_levers(graphrag)
    graph_fields = {key: (graph or {}).get(key) for key in GRAPH_PROVENANCE_KEYS}
    graph_fields["resolution"] = graph_fields["resolution"] or resolution
    graph_fields["extraction_recipe"] = graph_fields["extraction_recipe"] or extraction_recipe
    return {
        "levers": levers,
        "lever_signature": lever_signature(levers),
        "profile": profile,
        "answer_policy": bool(graphrag.answer_policy),
        "ask_for_citations": bool(graphrag.ask_for_citations),
        "sweep_set": sweep_set or None,
        "prompt_sha256": prompt_template_sha256(graphrag),
        "retrieval_fingerprint": retrieval_fingerprint,
        "pins_file": (pins or {}).get("file"),
        "pins_fingerprint": (pins or {}).get("fingerprint"),
        "pack": pack or (pins or {}).get("pack"),
        "queries_file": queries_file,
        "num_ctx": num_ctx,
        "ai_max_tokens": ai_max_tokens,
        "loop_guard": loop_guard,
        **graph_fields,
    }


def queries_file_sha256(path: str | Path | None) -> str | None:
    """The sha256 of a question file's bytes, as a chat row records it (``extras["queries_sha256"]``).

    The file name alone did not say which fixture content asked and scored a
    row (audit wave 3 of 2026-10-06): a fixture edit between two runs left
    both rows naming one file. None when no file is named or it is gone.
    """
    if not path:
        return None
    file = Path(path).expanduser()
    if not file.is_file():
        return None
    return hashlib.sha256(file.read_bytes()).hexdigest()


def loop_guard_thresholds(settings: Any) -> dict[str, int]:
    """The chat repetition guard's thresholds, read where the guard reads them.

    :func:`~chaoscypher_core.streaming.chat.loop.repetition_guard` builds the
    guard the chat call runs with; a row records its two thresholds so two
    rows that stopped answers at different repeat counts are told apart
    (audit wave 2).
    """
    from chaoscypher_core.streaming.chat.loop import repetition_guard

    guard = repetition_guard(settings)
    return {"max_consecutive": guard.max_consecutive, "max_occurrences": guard.max_occurrences}


def chat_context_window(llm: Any, provider: str | None = None) -> int | None:
    """The chat model's context window when the provider exposes one (Ollama's ``num_ctx``), else None.

    Args:
        llm: The ``LLMSettings`` the chat call runs with.
        provider: The candidate chat model's provider, when ``llm`` is the run's
            shared settings rather than the candidate's copy (whose
            ``chat_provider`` is the candidate's). Until 2026-10-06 (audit wave
            2) the row read the shared settings' provider, so an Ollama
            candidate on settings whose chat provider was another recorded no
            window, and a cloud candidate recorded Ollama's.
    """
    if (provider or getattr(llm, "chat_provider", None)) != "ollama":
        return None
    value = getattr(llm, "ollama_num_ctx", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def retrieved_entity_ids(retrieved: dict[str, Any]) -> list[str]:
    """Ids of the entity lines a retrieval showed the model: seed entities, then related ones.

    The rungs return entities under ``graph_context`` (``seed_entities``,
    ``related_entities``); the top-level ``entities`` key both chat
    producers read until 2026-10-06 is filled by none of them, so every
    row's ``retrieved_entity_ids`` was empty (audit R10). The chunk-only
    rungs have none.
    """
    gc = retrieved.get("graph_context") or {}
    ids: list[str] = []
    for key in ("seed_entities", "related_entities"):
        for entity in gc.get(key) or []:
            eid = entity.get("id") if isinstance(entity, dict) else None
            if eid is not None and str(eid) not in ids:
                ids.append(str(eid))
    return ids


SHAPE_ROW_KEYS: tuple[str, ...] = ("shape", "shape_reason", "shape_source")
"""What a chat row's question records about the shape its retrieval ran with (``retrieval_stats``)."""


def retrieval_shape(retrieved: dict[str, Any]) -> dict[str, str | None]:
    """The :data:`SHAPE_ROW_KEYS` of a retrieval's ``retrieval_stats``, each a string or None.

    The graphrag rung's stats carry the shape the router chose (``shape``),
    why (``shape_reason``) and who decided it (``shape_source``: ``rules``,
    ``llm`` or ``default``); the hybrid and naive rungs route nothing, so
    their rows record None for all three. Both chat producers (the CLI's
    chat stage and the MCP suite) write these keys on every question.
    """
    stats = retrieved.get("retrieval_stats") or {}
    return {key: None if stats.get(key) is None else str(stats[key]) for key in SHAPE_ROW_KEYS}


__all__ = [
    "GRAPH_IDENTITY_KEYS",
    "GRAPH_PROVENANCE_KEYS",
    "SHAPE_ROW_KEYS",
    "SWEEP_SET_ENV",
    "chat_context_window",
    "graph_identity",
    "graphrag_field_kinds",
    "lever_signature",
    "loop_guard_thresholds",
    "parse_sweep_set",
    "prompt_template_sha256",
    "queries_file_sha256",
    "resolve_run_graphrag",
    "resolved_levers",
    "retrieval_shape",
    "retrieved_entity_ids",
    "run_provenance",
]
