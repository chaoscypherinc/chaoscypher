# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Only pinned decoding is cached; call sites nest and restore."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chaoscypher_core.cache.call_site import CALL_SITES, call_site, current_call_site
from chaoscypher_core.cache.keys import Decoding
from chaoscypher_core.cache.policy import cacheable, seeded
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.utils.decoding import SEED_HONOURING_PROVIDERS


def _dec(**changes: Any) -> Decoding:
    base: dict[str, Any] = {
        "seed": 42,
        "temperature": 0.0,
        "num_ctx": 32768,
        "max_tokens": None,
        "thinking": False,
        "repeat_penalty": None,
    }
    return Decoding(**{**base, **changes})


def test_pinned_decoding_on_ollama_is_cacheable_and_seeded() -> None:
    assert "ollama" in SEED_HONOURING_PROVIDERS
    assert cacheable(_dec(), "ollama") == (True, None)
    assert seeded(_dec(), "ollama") is True


def test_a_sampled_temperature_bypasses() -> None:
    assert cacheable(_dec(temperature=0.3), "ollama") == (False, "sampled")
    assert cacheable(_dec(temperature=0.7, seed=None), "anthropic") == (False, "sampled")


def test_no_seed_on_a_provider_that_honours_seeds_bypasses() -> None:
    assert cacheable(_dec(seed=None), "ollama") == (False, "unseeded")


def test_a_hosted_provider_at_zero_caches_but_its_hits_are_not_seeded() -> None:
    assert cacheable(_dec(seed=None), "anthropic") == (True, None)
    assert seeded(_dec(seed=None), "anthropic") is False
    assert seeded(_dec(seed=42), "openai") is False  # the seed is not honoured


def test_the_call_site_defaults_to_other_and_nests() -> None:
    assert current_call_site() == "other"
    with call_site("extraction"):
        assert current_call_site() == "extraction"
        with call_site("dedup"):
            assert current_call_site() == "dedup"
        assert current_call_site() == "extraction"
    assert current_call_site() == "other"


def test_the_call_site_restores_after_an_exception() -> None:
    with pytest.raises(KeyError), call_site("chat"):
        raise KeyError("boom")
    assert current_call_site() == "other"


def test_an_unknown_call_site_is_refused() -> None:
    assert "other" in CALL_SITES
    with pytest.raises(ValidationError), call_site("extracton"):
        pass


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_their_own_call_site() -> None:
    seen: dict[str, str] = {}

    async def stage(name: str) -> None:
        with call_site(name):
            await asyncio.sleep(0)
            seen[name] = current_call_site()

    await asyncio.gather(stage("chat"), stage("judge"))
    assert seen == {"chat": "chat", "judge": "judge"}
