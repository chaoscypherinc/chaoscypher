# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction instruction probes as a benchmark suite.

Each probe takes the pipeline's two passes:

1. ``entities`` - the entity harvest (pass 1).
2. ``relationships`` - once the entities are in, the relationship harvest
   (pass 2) built from the parsed entity list, exactly as the pipeline's
   second pass would build it. When pass 1 keeps no entity the pipeline
   skips pass 2, and so does the run.

After the last pass the suite builds the same per-probe record
``ProbeDataset`` builds locally and scores it with :class:`ProbeScorer`.

The prompts are rendered by the extractor's own
:meth:`AIEntityExtractor.render_harvest_prompts` and
:meth:`AIEntityExtractor.render_relationship_prompt`, so they are
byte-identical to what a local run sends.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from chaoscypher_core.benchmark.probes import (
    Probe,
    completion_counters,
    load_probe_sections,
    prepare_probe_passage,
    probe_node_templates,
)
from chaoscypher_core.benchmark.scorers.probes import PROBE_SCORER_VERSION, ProbeScorer
from chaoscypher_core.benchmark.types import RawOutput
from chaoscypher_core.mcp.benchmark.errors import (
    ERR_OUT_OF_ORDER,
    ERR_UNKNOWN_PROBE,
    BenchmarkError,
)
from chaoscypher_core.mcp.benchmark.suites.base import (
    ScoreBundle,
    SubmitOutcome,
    SuiteContext,
    TaskRef,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
        HarvestLimits,
        HarvestPrompts,
    )
    from chaoscypher_core.settings import EngineSettings


PROBE_PACK_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "benchmark" / "data" / "probes"
)
"""The shipped probe fixture (manifest + section files + carriers)."""

PROBE_SEED = 42
"""Carrier splice seed; matches the local ``probes`` config."""

PROBE_PAD_TO_CHARS = 0
"""No filler padding; matches the local ``probes`` config."""

PROBE_SECTIONS: dict[str, Callable[[str], bool]] = {
    "probes": lambda section: section != "H",
    "probes-carrier": lambda section: section == "H",
    "probes-all": lambda _section: True,
}
"""Probe suite name -> which probe sections it runs (H is the carrier tier)."""

STAGES = ("entities", "relationships")


@dataclass(frozen=True)
class _ProbeContext:
    """Everything about one probe that does not depend on the model's answer."""

    probe: Probe
    passage: str
    probe_offset: int
    prompts: HarvestPrompts


@dataclass(frozen=True)
class _PassOutcome:
    """One pass's answer after the stream loop detector has seen it."""

    content: str
    finish_reason: str
    aborted_by_loop: bool
    input_tokens: int
    output_tokens: int


class ProbeSuite:
    """The extraction probes, restricted to the sections one suite name runs.

    Args:
        settings: Engine settings; the extraction settings resolve the system
            prompt, filtering and parser limits exactly as a local run does.
        pack_dir: Probe fixture directory.
        name: Suite name (``probes``, ``probes-carrier`` or ``probes-all``).
    """

    kind: str = "probes"
    stages: tuple[str, ...] = STAGES
    answer_format: str = (
        "ONLY the pipe-format lines the prompt asks for: E|/P| lines for stage "
        "'entities', R| lines for stage 'relationships'; no preamble, no code "
        "fences, no commentary."
    )
    reference: str | None = None

    def __init__(self, settings: EngineSettings, pack_dir: Path, *, name: str) -> None:
        """Bind settings, the fixture and the section filter; loading is deferred."""
        self.name = name
        self.settings = settings
        self.pack_dir = pack_dir
        self._keep = PROBE_SECTIONS[name]
        self._manifest: dict[str, Any] | None = None
        self._probes: dict[str, Probe] | None = None
        self._templates: dict[str, str] | None = None
        self._extractor: AIEntityExtractor | None = None
        self._limits: HarvestLimits | None = None
        self._contexts: dict[str, _ProbeContext] = {}

    # ------------------------------------------------------------------ #
    #  Fixture, templates and prompts
    # ------------------------------------------------------------------ #

    def _load_fixture(self) -> tuple[dict[str, Any], dict[str, Probe]]:
        """Load the manifest and every probe it lists (cached per suite)."""
        if self._manifest is None or self._probes is None:
            manifest = yaml.safe_load((self.pack_dir / "manifest.yaml").read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                msg = f"{self.pack_dir}/manifest.yaml must be a mapping"
                raise TypeError(msg)
            sections = [str(s) for s in manifest.get("sections") or []]
            probes = load_probe_sections(self.pack_dir, sections)
            self._manifest = manifest
            self._probes = {p.id: p for p in probes}
        return self._manifest, self._probes

    def _get_extractor(self) -> AIEntityExtractor:
        """Build the extractor used only to render prompts and parse answers."""
        if self._extractor is None:
            from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
                AIEntityExtractor,
            )

            self._extractor = AIEntityExtractor(settings=self.settings)
        return self._extractor

    def _get_limits(self) -> HarvestLimits:
        """Resolve filtering limits the way a local probe run does (no overrides)."""
        if self._limits is None:
            self._limits = self._get_extractor().resolve_harvest_limits()
        return self._limits

    def _get_templates(self) -> dict[str, str]:
        """Format the manifest domain's templates exactly as the local runner does."""
        if self._templates is None:
            from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
                get_domain_registry,
            )
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                format_extraction_templates,
            )

            manifest, _ = self._load_fixture()
            registry = get_domain_registry(self.settings, self.settings.current_database)
            domain = registry.get_domain(str(manifest.get("domain", "literary")))
            self._templates = format_extraction_templates(
                domain,
                examples_enabled=self.settings.llm.extraction_examples_enabled,
                examples_max_chars=self.settings.llm.extraction_examples_max_chars,
            )
        return self._templates

    def _exclusions(self, probe: Probe) -> list[Any] | None:
        """Build the probe's exclusion rules as the local runner does."""
        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            ExclusionRule,
        )

        return [ExclusionRule(**e) for e in probe.entity_exclusions] or None

    def _context(self, probe: Probe) -> _ProbeContext:
        """Prepare the passage and render the pass-1 prompts for ``probe``."""
        ctx = self._contexts.get(probe.id)
        if ctx is None:
            templates = self._get_templates()
            passage, offset = prepare_probe_passage(
                probe, self.pack_dir, seed=PROBE_SEED, pad_to_chars=PROBE_PAD_TO_CHARS
            )
            prompts = self._get_extractor().render_harvest_prompts(
                passage,
                probe_node_templates(probe, templates["node_templates"]),
                entity_examples=templates.get("entity_examples"),
                entity_exclusions=self._exclusions(probe),
                strict_entity_types=probe.strict_types,
            )
            ctx = _ProbeContext(probe=probe, passage=passage, probe_offset=offset, prompts=prompts)
            self._contexts[probe.id] = ctx
        return ctx

    async def _replay(
        self, text: str, system_prompt: str, user_prompt: str, reported_tokens: int | None
    ) -> _PassOutcome:
        """Run one pass's answer through the loop detector and count its tokens.

        Token counts fall back to the same estimates ``call_llm`` uses when a
        provider does not report usage; ``reported_tokens`` (from the client)
        replaces the output estimate when given.
        """
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            replay_harvest_output,
        )
        from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens

        content, finish_reason, aborted = await replay_harvest_output(
            text, self.settings.extraction, self._get_limits().max_entity_count
        )
        input_tokens = estimate_message_tokens(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        output_tokens = (
            int(reported_tokens) if reported_tokens is not None else estimate_tokens(content)
        )
        return _PassOutcome(
            content=content,
            finish_reason=finish_reason,
            aborted_by_loop=aborted,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _entity_pass(
        self, probe: Probe, stage_state: dict[str, Any]
    ) -> tuple[_PassOutcome, list[dict[str, Any]]]:
        """Replay pass 1 and return it with the entities pass 2 would list."""
        ctx = self._context(probe)
        outcome = await self._replay(
            stage_state["entities"],
            ctx.prompts.system_prompt,
            ctx.prompts.entity_prompt,
            stage_state.get("entities_tokens"),
        )
        harvest = await self._get_extractor().parse_entity_harvest(
            outcome.content,
            sentences=ctx.prompts.sentences,
            chunk_content=ctx.passage,
            limits=self._get_limits(),
            entity_exclusions=self._exclusions(probe),
            strict_entity_types=probe.strict_types,
            valid_entity_type_names=set(probe.entity_types) or None,
        )
        return outcome, harvest.entities

    async def _cached_entity_pass(
        self, probe: Probe, stage_state: dict[str, Any]
    ) -> tuple[_PassOutcome, list[dict[str, Any]]]:
        """Pass 1 and its filtered entities, from the run state's cache when present.

        The entities submit computes pass 1 once and stores the outcome and
        the kept entities in the probe's stage state (so they persist with
        the run); the relationships prompt and the record reuse them.
        """
        cached = stage_state.get("pass1")
        if cached is not None:
            return _PassOutcome(**cached), list(stage_state.get("pass1_entities") or [])
        outcome, entities = await self._entity_pass(probe, stage_state)
        stage_state["pass1"] = asdict(outcome)
        stage_state["pass1_entities"] = entities
        return outcome, entities

    def _relationship_prompt(self, probe: Probe, entities: list[dict[str, Any]]) -> str:
        """Render pass 2 for ``probe`` from its filtered pass-1 entities."""
        templates = self._get_templates()
        return self._get_extractor().render_relationship_prompt(
            entities,
            self._context(probe).prompts.numbered_text,
            templates["edge_templates"],
            relationship_examples=templates.get("relationship_examples"),
        )

    async def _build_record(self, probe: Probe, stage_state: dict[str, Any]) -> dict[str, Any]:
        """Build the per-probe record in the shape ``ProbeDataset._run_probe`` produces."""
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            combine_finish_reasons,
            format_raw_harvest_response,
        )

        ctx = self._context(probe)
        templates = self._get_templates()
        extractor = self._get_extractor()
        pass1, entities = await self._cached_entity_pass(probe, stage_state)
        pass2 = _PassOutcome("", "stop", False, 0, 0)
        if entities:
            pass2 = await self._replay(
                stage_state["relationships"] or "",
                ctx.prompts.system_prompt,
                self._relationship_prompt(probe, entities),
                stage_state.get("relationships_tokens"),
            )
        parsed = await extractor.parse_harvest_outputs(
            pass1.content,
            pass2.content,
            sentences=ctx.prompts.sentences,
            chunk_content=ctx.passage,
            numbered_text=ctx.prompts.numbered_text,
            edge_templates_formatted=templates["edge_templates"],
            limits=self._get_limits(),
            entity_exclusions=self._exclusions(probe),
            strict_entity_types=probe.strict_types,
            valid_entity_type_names=set(probe.entity_types) or None,
            relationship_examples=templates.get("relationship_examples"),
        )
        return {
            "id": probe.id,
            "entities": parsed.entities,
            "relationships": parsed.relationships,
            "input_tokens": pass1.input_tokens + pass2.input_tokens,
            "output_tokens": pass1.output_tokens + pass2.output_tokens,
            "latency_ms": 0,
            "finish_reason": combine_finish_reasons(pass1.finish_reason, pass2.finish_reason),
            "aborted_by_loop": pass1.aborted_by_loop or pass2.aborted_by_loop,
            "parser_lines_dropped": parsed.parser_lines_dropped,
            "invalid_relationship_count": parsed.invalid_relationship_count,
            "raw_llm_response": format_raw_harvest_response(pass1.content, pass2.content),
            "sentences": ctx.prompts.sentences,
            "relationship_instructions": extractor.render_relationship_prompt_template(
                templates["edge_templates"],
                relationship_examples=templates.get("relationship_examples"),
            ),
            "probe_offset": ctx.probe_offset,
            "carrier_cast": list(probe.carrier_cast),
        }

    def _run_probes(self, state: dict[str, Any]) -> list[Probe]:
        """Return the run's probes in run order."""
        _, probes = self._load_fixture()
        return [probes[pid] for pid in state["task_ids"] if pid in probes]

    # ------------------------------------------------------------------ #
    #  Suite contract
    # ------------------------------------------------------------------ #

    def run_info(self) -> dict[str, Any]:
        """The probe fixture's id and version, recorded on the run."""
        manifest, _ = self._load_fixture()
        return {
            "dataset_id": str(manifest.get("id", "probes")),
            "dataset_version": str(manifest.get("version", "")),
        }

    def tasks(self, only: list[str] | None) -> list[TaskRef]:
        """The suite's probes in fixture order, or the ``only`` subset."""
        _, probes = self._load_fixture()
        selected = [p for p in probes.values() if self._keep(p.section)]
        if only is not None:
            unknown = sorted(set(only) - {p.id for p in selected})
            if unknown:
                raise BenchmarkError(
                    ERR_UNKNOWN_PROBE, f"not in suite {self.name!r}: {', '.join(unknown)}"
                )
            selected = [p for p in selected if p.id in set(only)]
        return [TaskRef(p.id, {"section": p.section, "tier": p.tier}) for p in selected]

    def pending_stage(self, state: dict[str, Any], task_id: str) -> str | None:
        """Return the stage ``task_id`` is waiting on, or None when complete."""
        if task_id in state["records"]:
            return None
        if state["stages"].get(task_id, {}).get("entities") is None:
            return "entities"
        return "relationships"

    async def prompt(self, state: dict[str, Any], task_id: str, stage: str) -> dict[str, Any]:
        """The system prompt and the pass-1 or pass-2 user prompt."""
        _, probes = self._load_fixture()
        probe = probes[task_id]
        ctx = self._context(probe)
        if stage == "entities":
            user_prompt = ctx.prompts.entity_prompt
        else:
            _outcome, entities = await self._cached_entity_pass(probe, state["stages"][task_id])
            user_prompt = self._relationship_prompt(probe, entities)
        return {"system_prompt": ctx.prompts.system_prompt, "user_prompt": user_prompt}

    async def submit(
        self,
        state: dict[str, Any],
        task_id: str,
        stage: str,
        output_text: str,
        *,
        output_tokens: int | None,
        empty_answer: bool,
        truncated: bool,
    ) -> SubmitOutcome:
        """Store one pass; build the record once the probe is complete.

        The bridge only passes the stage the probe is waiting on, so each pass
        is answered once: pass 2 is built from the pass-1 answer as submitted.
        """
        _, probes = self._load_fixture()
        probe = probes[task_id]
        stages = state["stages"].setdefault(task_id, {"entities": None, "relationships": None})

        if stage == "entities":
            stages["entities"] = output_text
            stages["entities_tokens"] = output_tokens
            stages["relationships"] = None
            stages["relationships_tokens"] = None
            _outcome, entities = await self._cached_entity_pass(probe, stages)
            if entities:
                # Pass 2 is next for this probe.
                return SubmitOutcome(record=None)
        else:
            if stages.get("entities") is None:
                raise BenchmarkError(
                    ERR_OUT_OF_ORDER, f"submit the entities stage of {task_id!r} first"
                )
            stages["relationships"] = output_text
            stages["relationships_tokens"] = output_tokens

        return SubmitOutcome(record=await self._build_record(probe, stages))

    def verdict(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Score one probe record with the real scorer (a one-probe fixture)."""
        _, probes = self._load_fixture()
        raw = RawOutput(
            entities=[],
            relationships=[],
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            error=None,
            extras={"probes": [record], "selected_ids": [task_id]},
        )
        verdict = ProbeScorer().score(raw, [probes[task_id]]).metrics["verdicts"][0]
        return {"passed": bool(verdict["passed"]), "details": list(verdict["details"])}

    def score(self, state: dict[str, Any], records: list[dict[str, Any]]) -> ScoreBundle:
        """Score the run with :class:`ProbeScorer`; pending probes count as fails."""
        probes = self._run_probes(state)
        truncated, aborted = completion_counters(records)
        selected_ids = sorted(p.id for p in probes)
        raw = RawOutput(
            entities=[],
            relationships=[],
            latency_ms=0,
            input_tokens=sum(int(r.get("input_tokens", 0)) for r in records),
            output_tokens=sum(int(r.get("output_tokens", 0)) for r in records),
            error=None,
            chunks_truncated=truncated,
            chunks_aborted_by_loop=aborted,
            extras={"probes": records, "selected_ids": selected_ids},
        )
        score = ProbeScorer().score(raw, probes)
        return ScoreBundle(
            headline_score=score.headline_score,
            metrics=score.metrics,
            dataset_id=state["dataset_id"],
            dataset_kind=self.kind,
            dataset_version=state["dataset_version"],
            dataset_source="builtin" if self.pack_dir == PROBE_PACK_DIR else "user",
            config_name=state["suite"],
            scorer_version=PROBE_SCORER_VERSION,
            seed=PROBE_SEED,
            temperature=None,
            thinking=None,
            thinking_honoured=None,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            chunks_truncated=truncated,
            chunks_aborted_by_loop=aborted,
            extras=raw.extras,
        )


def probe_suite_factory(name: str) -> Callable[[SuiteContext], ProbeSuite]:
    """A registry factory for the probe suite called ``name``."""

    def _make(ctx: SuiteContext) -> ProbeSuite:
        """Build the probe suite; probe suites read no reference pack."""
        return ProbeSuite(ctx.settings, ctx.probe_pack_dir, name=name)

    return _make


__all__ = [
    "PROBE_PACK_DIR",
    "PROBE_PAD_TO_CHARS",
    "PROBE_SECTIONS",
    "PROBE_SEED",
    "STAGES",
    "ProbeSuite",
    "probe_suite_factory",
]
