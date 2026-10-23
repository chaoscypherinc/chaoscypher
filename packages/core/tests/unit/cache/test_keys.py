# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cache keys: volatile fields and whitespace never change a key; model build, decoding and tools do."""

from __future__ import annotations

import unicodedata
from typing import Any

from chaoscypher_core.cache.keys import (
    KEPT_MESSAGE_FIELDS,
    VOLATILE_MESSAGE_FIELDS,
    Decoding,
    completion_key,
    embedding_key,
    model_build_id,
    normalise_messages,
)


_DEC = Decoding(
    seed=42, temperature=0.0, num_ctx=32768, max_tokens=None, thinking=False, repeat_penalty=None
)
_BUILD = model_build_id("ollama", "qwen3:14b", digest="sha256:abc", server_version="0.12.1")


def _msgs(**extra: object) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are helpful.", **extra},
        {"role": "user", "content": "Who   kept\nthe accounts?"},
    ]


def _dec(**changes: Any) -> Decoding:
    base = {
        "seed": 42,
        "temperature": 0.0,
        "num_ctx": 32768,
        "max_tokens": None,
        "thinking": False,
        "repeat_penalty": None,
    }
    return Decoding(**{**base, **changes})


def test_volatile_fields_and_whitespace_do_not_change_the_key() -> None:
    a = completion_key(
        _BUILD,
        _msgs(id="m1", timestamp="2026-10-06T10:00:00Z", metadata={"x": 1}, request_id="r"),
        _DEC,
        None,
    )
    b = completion_key(
        _BUILD,
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Who kept the accounts?"},
        ],
        _DEC,
        None,
    )
    assert a == b


def test_normalised_messages_keep_the_model_input_only() -> None:
    out = normalise_messages(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "search"}}],
                "created_at": "now",
                "extra_metadata": {"x": 1},
            },
            {"role": "tool", "content": " ok ", "tool_call_id": "c1", "name": "search"},
        ]
    )
    assert out == [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search"}}]},
        {"role": "tool", "content": "ok", "tool_call_id": "c1", "name": "search"},
    ]


def test_no_volatile_field_is_ever_kept() -> None:
    assert not VOLATILE_MESSAGE_FIELDS & set(KEPT_MESSAGE_FIELDS)
    volatile = dict.fromkeys(VOLATILE_MESSAGE_FIELDS, "x")
    (kept,) = normalise_messages([{"role": "user", "content": "hi", **volatile}])
    assert set(kept) == {"role", "content"}


def test_message_order_and_roles_change_the_key() -> None:
    base = completion_key(_BUILD, _msgs(), _DEC, None)
    assert completion_key(_BUILD, list(reversed(_msgs())), _DEC, None) != base
    swapped = [{**m, "role": "user"} for m in _msgs()]
    assert completion_key(_BUILD, swapped, _DEC, None) != base


def test_nfc_and_nfd_give_one_key() -> None:
    nfd = unicodedata.normalize("NFD", "Bezúkhov")
    assert nfd != "Bezúkhov"
    assert embedding_key(_BUILD, "Bezúkhov") == embedding_key(_BUILD, nfd)
    assert completion_key(_BUILD, [{"role": "user", "content": nfd}], _DEC, None) == (
        completion_key(_BUILD, [{"role": "user", "content": "Bezúkhov"}], _DEC, None)
    )


def test_embedding_whitespace_is_significant_but_the_build_is_in_the_key() -> None:
    other = model_build_id("ollama", "qwen3:14b", digest="sha256:def", server_version="0.12.1")
    assert embedding_key(_BUILD, "a b") != embedding_key(_BUILD, "a  b")
    assert embedding_key(_BUILD, "a") != embedding_key(other, "a")


def test_model_build_decoding_and_tools_each_change_the_key() -> None:
    base = completion_key(_BUILD, _msgs(), _DEC, None)
    other_build = model_build_id(
        "ollama", "qwen3:14b", digest="sha256:def", server_version="0.12.1"
    )
    assert completion_key(other_build, _msgs(), _DEC, None) != base
    other_server = model_build_id("ollama", "qwen3:14b", digest="sha256:abc", server_version="0.13")
    assert completion_key(other_server, _msgs(), _DEC, None) != base
    assert completion_key(_BUILD, _msgs(), _DEC, [{"name": "search"}]) != base
    for change in (
        {"seed": 43},
        {"temperature": 0.3},
        {"num_ctx": 8192},
        {"max_tokens": 100},
        {"thinking": True},
        {"repeat_penalty": 1.15},
    ):
        assert completion_key(_BUILD, _msgs(), _dec(**change), None) != base, change


def test_keys_are_sha256_hex() -> None:
    key = completion_key(_BUILD, _msgs(), _DEC, None)
    assert len(key) == 64 and int(key, 16) >= 0


def test_a_build_without_a_digest_is_named_by_provider_and_model() -> None:
    assert (
        model_build_id("anthropic", "claude-sonnet-4-5", digest=None, server_version=None)
        == "anthropic:claude-sonnet-4-5"
    )
    assert _BUILD == "ollama:qwen3:14b:sha256:abc@0.12.1"
    assert (
        model_build_id("ollama", "m", digest="sha256:abc", server_version=None)
        == "ollama:m:sha256:abc"
    )


def test_every_stage_prompt_renders_to_one_key_twice() -> None:
    """A timestamp or id in a stage's prompt would make it uncacheable: render twice, keys agree.

    Every registered domain's extraction plan (and the generic fallback) is
    rendered through the extractor's own methods, both passes, by two
    extractors; the chat messages are built twice from a history whose
    messages carry different ids and timestamps.
    """
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
        build_extraction_plan,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings
    from chaoscypher_core.streaming.chat.messages import build_messages_for_llm

    settings = EngineSettings()
    registry = get_domain_registry(settings)
    names = sorted(str(info["name"]) for info in registry.list_domain_info())
    assert names, "no registered domains to render"
    chunk = "Pierre kept the accounts. Natásha sang at the ball."
    entities = [{"name": "Pierre", "type": "Character"}, {"name": "Natásha", "type": "Character"}]

    def render(name: str | None) -> list[list[dict[str, Any]]]:
        """Both passes' messages for ``name``'s plan, from a fresh extractor."""
        extractor = AIEntityExtractor(settings=settings)
        kwargs = build_extraction_plan(
            DomainStack.from_domain_name(registry, name), settings
        ).chunk_kwargs
        prompts = extractor.render_chunk_prompts(kwargs, chunk, entities)
        system = {"role": "system", "content": prompts.system_prompt}
        return [
            [system, {"role": "user", "content": prompts.entity_prompt}],
            [system, {"role": "user", "content": prompts.relationship_prompt}],
        ]

    for name in (None, *names):
        first, second = render(name), render(name)
        for a, b in zip(first, second, strict=True):
            assert completion_key(_BUILD, a, _DEC, None) == completion_key(_BUILD, b, _DEC, None), (
                name
            )

    def chat(stamp: str) -> list[dict[str, Any]]:
        """The chat stage's messages for one history, its messages stamped ``stamp``."""
        history = {
            "messages": [
                {
                    "id": f"u-{stamp}",
                    "role": "user",
                    "content": "Who is Pierre?",
                    "timestamp": stamp,
                },
                {
                    "id": f"a-{stamp}",
                    "role": "assistant",
                    "content": "A count.",
                    "timestamp": stamp,
                },
                {"id": f"q-{stamp}", "role": "user", "content": "And Natásha?", "timestamp": stamp},
            ]
        }
        return build_messages_for_llm(
            history, f"chat-{stamp}", settings=Settings()
        ).messages_for_llm

    assert completion_key(_BUILD, chat("2026-10-06T10:00:00Z"), _DEC, None) == completion_key(
        _BUILD, chat("2026-10-07T11:30:00Z"), _DEC, None
    )
