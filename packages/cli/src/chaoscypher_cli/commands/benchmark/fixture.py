# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""bench fixture — fixture authoring guardrails."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import click

from chaoscypher_cli.benchmark.embedding_dataset import resolve_gold_entity


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet


def validate_fixture(*, queries: LabeledQuerySet, entities: list[dict[str, Any]]) -> int:
    """Validate gold entity resolution against a list of live entities.

    Returns 0 if every in-scope query's gold entities resolved, else 1.

    Args:
        queries: The labeled query set to validate.
        entities: Live entity dicts with at least ``id``, ``name``, and
            ``aliases`` keys.

    Returns:
        0 when all in-scope queries' gold entities are resolved; 1 otherwise.
    """
    total_in_scope = sum(1 for q in queries.queries if q.band != "out_of_scope")
    resolved_count = 0
    failures: list[tuple[str, list[str]]] = []
    out_of_scope = 0
    for q in queries.queries:
        if q.band == "out_of_scope":
            out_of_scope += 1
            continue
        unresolved = [g for g in q.gold_entities if resolve_gold_entity(g, entities) is None]
        if unresolved:
            failures.append((q.id, unresolved))
        else:
            resolved_count += 1

    click.echo(f"Fixture: {queries.version}")
    click.echo(f"Resolved: {resolved_count} / {total_in_scope} in-scope queries")
    click.echo(f"Skipped (out_of_scope): {out_of_scope}")
    if failures:
        click.echo("")
        click.echo("Unresolved gold entities:")
        for qid, names in failures:
            click.echo(f"  {qid}: {', '.join(names)}")
        return 1
    click.echo("All gold entities resolved.")
    return 0


@click.group("fixture")
def fixture_group() -> None:
    """Fixture-authoring helpers for benchmark datasets."""


@fixture_group.command("validate")
@click.argument("dataset_id")
@click.option(
    "--canonical-extractor",
    default="ollama/llama3.1:8b",
    show_default=True,
    help="provider/model used to build the reference graph.",
)
def fixture_validate_cmd(dataset_id: str, canonical_extractor: str) -> None:
    """Validate a dataset's queries.yaml gold entities resolve in a built graph."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_cli.benchmark.models import ModelConfig

    bundle = load_dataset_bundle(dataset_id)
    if bundle.queries is None:
        msg = f"dataset '{dataset_id}' has no queries_path; nothing to validate"
        raise click.ClickException(msg)

    provider, model = canonical_extractor.split("/", 1)
    extractor = ModelConfig(provider=provider, model=model, label=canonical_extractor)
    raw = asyncio.run(bundle.extraction_dataset.run(extractor))
    if raw.error is not None:
        msg = f"extraction failed: {raw.error}"
        raise click.ClickException(msg)

    rc = validate_fixture(queries=bundle.queries, entities=raw.entities)
    if rc != 0:
        raise click.exceptions.Exit(rc)


__all__ = ["fixture_group", "fixture_validate_cmd", "validate_fixture"]
