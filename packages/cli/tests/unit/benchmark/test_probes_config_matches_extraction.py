# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The probes config scores the same model list as the extraction config."""

from chaoscypher_cli.benchmark.config import load_config


def test_probes_and_extraction_configs_share_the_extractor_list() -> None:
    """The docs promise 'same model list'; a model added to one but not the other
    would make the leaderboard and the corpus benchmark silently disagree.
    """
    probes = [(m.provider, m.model) for m in load_config("probes").extractors or []]
    extraction = [(m.provider, m.model) for m in load_config("extraction").extractors or []]
    assert probes == extraction
