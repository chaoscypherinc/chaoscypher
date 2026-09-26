# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ProbeDataset - instruction-following probes scored pass/fail.

Extraction is not one skill; it is ~24 explicit instructions across three
prompts plus a set of failure modes the parser and loop detector defend
against. Each probe is a hand-written passage constructed so the correct
extraction is unambiguous, plus checks a reader can verify by reading it.

Probes call ``AIEntityExtractor.extract_single_chunk`` with the real prompts,
the real settings pins (seed, temperature, thinking) and the real line parser,
so a pass means "the product could use this output" - we test the
instruction-following the product depends on, not a synthetic prompt.

Tiers (easy/medium/hard) change only the passage, never the instruction or
the checker, and are validated by outcome after each multi-model run: a probe
every model passes or fails is suspect and gets rewritten.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.dataset import DatasetSource, RawOutput
from chaoscypher_core.benchmark.probes import (
    Probe,
    completion_counters,
    load_probe_sections,
    pad_with_filler,
    prepare_probe_passage,
    probe_node_templates,
    splice_into_carrier,
)
from chaoscypher_core.benchmark.scorers.probes import ProbeScorer


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import DatasetScorer
    from chaoscypher_cli.benchmark.models import ModelConfig


logger = structlog.get_logger(__name__)


@dataclass
class ProbeDataset:
    """Runs every probe against one model and packs the results into RawOutput.

    ``RawOutput.extras["probes"]`` carries one record per probe with the parsed
    entities/relationships, the raw model text, the finish reason, token counts
    and the entity list the relationship pass actually saw. The paired
    :class:`ProbeScorer` evaluates the checks against those records.
    """

    id: str
    version: str
    domain: str
    probes: list[Probe]
    source: DatasetSource = "builtin"
    thinking: bool = False
    seed: int = 42
    temperature: float = 0.0
    pack_dir: Path = field(default_factory=Path)
    """Directory the manifest lives in; carrier paths resolve against it."""
    only: frozenset[str] | None = None
    """When set, run only these probe ids - lets a new probe be measured without
    re-running the whole suite."""
    pad_to_chars: int = 0
    """When > 0, append entity-free filler after each passage until the input
    reaches this many characters, so the model sees production-scale input.
    Production packs chunks into ~900-token groups (len//4 => ~3,600 chars);
    the median probe passage is ~275 chars. Probe sentences come first, so
    every sent_ref stays valid; the filler names nothing, so entity/relationship
    checks are unaffected. Lets size sensitivity be measured directly."""
    thinking_honoured: bool | None = field(default=None, init=False)
    kind: str = field(default="probes", init=False)
    scorer: DatasetScorer = field(default_factory=ProbeScorer, init=False)
    fixture: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Expose the probe list as the fixture so the scorer can see checks."""
        self.fixture = self.probes

    async def run(self, model: ModelConfig) -> RawOutput:
        """Run all probes against ``model``; never raise on a single probe."""
        # Reuse ExtractionDataset's temp-context builder so the settings pins
        # (seed, temperature, thinking, model name) and the thinking probe are
        # identical to the corpus benchmark. Deferred import: heavy.
        from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
        from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            format_extraction_templates,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            AIEntityExtractor,
        )

        self.thinking_honoured = None
        shim = ExtractionDataset.__new__(ExtractionDataset)
        # Only the fields _build_temp_context reads.
        shim.id = self.id
        shim.thinking = self.thinking
        shim.seed = self.seed
        shim.temperature = self.temperature

        t0 = time.perf_counter()
        records: list[dict[str, Any]] = []
        total_in = total_out = 0
        ctx = None
        try:
            if model.provider == "ollama":
                from chaoscypher_cli.benchmark.thinking_probe import (
                    probe_thinking,
                    thinking_honoured,
                )

                verdict = await probe_thinking(model.model)
                self.thinking_honoured = thinking_honoured(verdict, requested=self.thinking)

            ctx = shim._build_temp_context(model)  # noqa: SLF001 - shared builder
            registry = get_domain_registry(ctx.settings, ctx.database_name)
            domain = registry.get_domain(self.domain)
            templates = format_extraction_templates(
                domain,
                examples_enabled=ctx.settings.llm.extraction_examples_enabled,
                examples_max_chars=ctx.settings.llm.extraction_examples_max_chars,
            )
            extractor = AIEntityExtractor(settings=ctx.settings)

            selected = [p for p in self.probes if self.only is None or p.id in self.only]
            for probe in selected:
                rec = await self._run_probe(probe, extractor, templates, model)
                records.append(rec)
                total_in += rec.get("input_tokens", 0)
                total_out += rec.get("output_tokens", 0)
        except Exception as exc:
            logger.exception("probe_dataset_failed", model=model.model_id)
            truncated, aborted = completion_counters(records)
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=int((time.perf_counter() - t0) * 1000),
                input_tokens=total_in,
                output_tokens=total_out,
                error=f"{type(exc).__name__}: {exc}",
                chunks_truncated=truncated,
                chunks_aborted_by_loop=aborted,
                extras={"probes": records},
            )
        finally:
            if ctx is not None:
                self._teardown(ctx)

        truncated, aborted = completion_counters(records)
        return RawOutput(
            entities=[],
            relationships=[],
            latency_ms=int((time.perf_counter() - t0) * 1000),
            input_tokens=total_in,
            output_tokens=total_out,
            error=None,
            chunks_truncated=truncated,
            chunks_aborted_by_loop=aborted,
            per_chunk_latency_ms=[r["latency_ms"] for r in records if "latency_ms" in r],
            extras={
                "probes": records,
                "selected_ids": sorted(
                    p.id for p in self.probes if self.only is None or p.id in self.only
                ),
            },
        )

    async def _run_probe(
        self,
        probe: Probe,
        extractor: Any,
        templates: dict[str, str],
        model: ModelConfig,
    ) -> dict[str, Any]:
        """Run one probe; a failure becomes a record with ``error`` set."""
        from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
            ExclusionRule,
        )

        exclusions = [ExclusionRule(**e) for e in probe.entity_exclusions] or None
        passage, probe_offset = prepare_probe_passage(
            probe, self.pack_dir, seed=self.seed, pad_to_chars=self.pad_to_chars
        )
        node_templates = probe_node_templates(probe, templates["node_templates"])
        t0 = time.perf_counter()
        try:
            (
                entities,
                relationships,
                in_tok,
                out_tok,
                metrics,
            ) = await extractor.extract_single_chunk(
                chunk_content=passage,  # the spliced/padded text, not the bare probe
                node_templates_formatted=node_templates,
                edge_templates_formatted=templates["edge_templates"],
                entity_examples=templates.get("entity_examples"),
                relationship_examples=templates.get("relationship_examples"),
                entity_exclusions=exclusions,
                strict_entity_types=probe.strict_types,
                valid_entity_type_names=set(probe.entity_types) or None,
            )
        except Exception as exc:
            logger.warning("probe_failed", probe=probe.id, model=model.model_id, error=str(exc))
            return {"id": probe.id, "error": f"{type(exc).__name__}: {exc}"}
        prompt_data = metrics.get("_prompt_data") or {}
        return {
            "id": probe.id,
            "entities": entities,
            "relationships": relationships,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "finish_reason": metrics.get("finish_reason", "unknown"),
            "aborted_by_loop": bool(metrics.get("aborted_by_loop", False)),
            "parser_lines_dropped": int(metrics.get("parser_lines_dropped", 0) or 0),
            "invalid_relationship_count": int(metrics.get("invalid_relationship_count", 0) or 0),
            "raw_llm_response": metrics.get("raw_llm_response", ""),
            "sentences": metrics.get("sentences") or [],
            "relationship_instructions": prompt_data.get("relationship_instructions", ""),
            "probe_offset": probe_offset,
            "carrier_cast": list(probe.carrier_cast),
        }

    def _teardown(self, ctx: Any) -> None:
        """Disconnect and drop the per-run temp database; never raise.

        Mirrors ``ExtractionDataset.run``'s finally block: capture the database
        directory before ``disconnect`` clears the engine reference, then evict
        the cached engine and remove the directory.
        """
        try:
            from chaoscypher_cli.benchmark.extraction_dataset import _remove_temp_db_dir

            db_dir = ctx.database_dir
            ctx.disconnect()
            if db_dir.exists():
                _remove_temp_db_dir(db_dir, dataset_id=self.id)
        except Exception:  # bookkeeping must not fail a scored run
            logger.warning("probe_teardown_failed", exc_info=True)


__all__ = [
    "Probe",
    "ProbeDataset",
    "completion_counters",
    "load_probe_sections",
    "pad_with_filler",
    "splice_into_carrier",
]
