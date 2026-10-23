# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The deduplication's output is the same under every string hash seed (audit W4-4).

``merge_entities`` built a merged entity's aliases and descriptors as ``set``
unions (``list(kept | dup)``): their order followed ``PYTHONHASHSEED``, and
it reaches the stored node's aliases and the "Also known as:" embedding text,
so two identical extractions at temperature 0 gave different node vectors
(16 of v3's 97 entities differed between seeds 1 and 2). The merge now keeps
first-seen order. Two subprocesses with different seeds must print the same
bytes: the pipeline as ``run_deduplication`` runs it, the embedding text of
every survivor, and both recipes (which hash list order since wave 4, so any
other set that leaks into a sample moves them between seeds too).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


_SCRIPT = textwrap.dedent(
    """
    import asyncio, json, logging
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    from chaoscypher_core.benchmark.extraction_recipe import extraction_recipe
    from chaoscypher_core.services.graph.resolution import resolution_recipe
    from chaoscypher_core.services.sources.engine.deduplication.embedding_generator import (
        entity_to_embedding_text,
    )
    from chaoscypher_core.services.sources.engine.extraction.extractor import run_deduplication
    from chaoscypher_core.settings import EngineSettings

    def mention(name, aliases, descriptors=()):
        return {"name": name, "type": "Character", "aliases": list(aliases),
                "descriptors": list(descriptors), "description": f"{name} in a chapter."}

    entities = [
        mention("Anna Pávlovna Schérer", ["Annette", "the hostess", "Anna Pávlovna"], ["a maid of honour"]),
        mention("Anna Pavlovna Scherer", ["Annette Schérer", "Anna", "dear Annette"], ["the hostess"]),
        mention("Mlle Schérer", ["Annette", "Anna Pávlovna Schérer"]),
        mention("Prince Vasíli Kurágin", ["Vasíli", "Prince Vasíli", "Kurágin"], ["a courtier"]),
        mention("Prince Vasili Kuragin", ["Basil", "Vasíli Kurágin"], ["Anatole's father"]),
        mention("Count Ilyá Rostóv", ["Count Rostóv", "Ilyá Andréevich", "the count"]),
        mention("Count Ilya Rostov", ["Rostóv", "Ilyá Rostóv"]),
    ]
    settings = EngineSettings()
    for domain in ("literary", "generic"):
        out, rels, _, log = asyncio.run(run_deduplication(
            entities=[dict(e, aliases=list(e["aliases"])) for e in entities],
            relationships=[{"source": 0, "target": 3, "type": "knows"}],
            detected_domain=domain, settings=settings, embedding_service=None))
        print(json.dumps([{k: e.get(k) for k in ("name", "aliases", "aliases_given", "descriptors")}
                          for e in out], ensure_ascii=False))
        print(json.dumps([entity_to_embedding_text(e) for e in out], ensure_ascii=False))
    print(extraction_recipe(settings, domain="literary"), resolution_recipe())
    """
)


def _run(seed: str) -> str:
    """The script's output under ``PYTHONHASHSEED=seed``."""
    env = {**os.environ, "PYTHONHASHSEED": seed}
    done = subprocess.run(  # noqa: S603 - the test's own interpreter and script
        [sys.executable, "-c", _SCRIPT], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout


def test_the_deduplication_and_the_recipes_do_not_depend_on_the_hash_seed() -> None:
    """Seeds 1 and 2 give byte-identical merged entities, embedding texts and recipes."""
    first, second = _run("1"), _run("2")
    assert first.count("\n") == 5
    assert first == second
