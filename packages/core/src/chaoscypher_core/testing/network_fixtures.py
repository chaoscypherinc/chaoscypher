# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A unit test never reaches a model server.

2026-10-04: a full ``pytest packages/core/tests packages/cli/tests`` sent real
``/api/embed`` and ``/api/chat`` requests to the local Ollama, loading the
default embedder (and once a chat model) onto a GPU that a benchmark was
using, three times in two days. The guard is installed for the whole test
process the moment a package conftest imports :func:`ollama_offline`: a
``connect`` to the Ollama port on any host raises at once, naming the test
that was running (or the last one, when a provider's background probe fires
after its test ended), instead of silently passing on whatever the server
said. A test that needs the real server opts out with ``@pytest.mark.ollama``
(the e2e and docker tiers); a unit test fakes the provider instead.

2026-10-26: the local embedding model is the same hazard without a server. A
test that commits with default settings and no injected provider built the
local provider, which downloaded Qwen3-Embedding-0.6B (1.2 GB) from Hugging
Face into that test's temporary data directory and loaded it: eleven unit
tests did, ~19 s each, 13 GB of downloads a run, and one worker of a 2-worker
shard peaked near 8 GB (the day a CI runner killed such workers). The model guard
refuses the load (unless ``sentence_transformers.SentenceTransformer`` is
patched, as the provider's own tests do) and fails the test even when the code
under test swallowed the error; inject
:class:`chaoscypher_core.testing.embedding.FakeEmbeddingProvider` instead.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from typing import Any

import pytest


OLLAMA_PORT = 11434
_OUTSIDE = "<no test running; a background probe after the last test>"
_state: dict[str, Any] = {
    "current": _OUTSIDE,
    "allowed": False,
    "installed": False,
    "model_guard": False,
    "model_loads": [],
}


class TestReachedOllamaError(RuntimeError):
    """A test (or a probe it left behind) opened a connection to an Ollama server."""


class TestLoadedEmbeddingModelError(RuntimeError):
    """A test loaded the real local embedding model (a download into its data directory)."""


def _port(address: Any) -> int | None:
    """The port of a socket address tuple, or None when it has none."""
    try:
        return int(address[1])
    except TypeError, IndexError, ValueError:
        return None


def install_guard() -> None:
    """Wrap ``socket.connect`` and ``connect_ex`` once for the whole process."""
    if _state["installed"]:
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _refuse(address: Any) -> None:
        if _port(address) == OLLAMA_PORT and not _state["allowed"]:
            msg = f"{_state['current']} opened a connection to Ollama at {address!r}; fake the provider"
            raise TestReachedOllamaError(msg)

    def guarded_connect(self: socket.socket, address: Any) -> None:
        _refuse(address)
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _refuse(address)
        return real_connect_ex(self, address)

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[assignment]
    _state["installed"] = True


def _sentence_transformer_is_real() -> bool:
    """False while a test has patched ``sentence_transformers.SentenceTransformer`` with a fake.

    Not imported yet means nobody patched it (``unittest.mock.patch`` imports the module).
    """
    module = sys.modules.get("sentence_transformers")
    if module is None:
        return True
    cls = getattr(module, "SentenceTransformer", None)
    return isinstance(cls, type) and cls.__module__.startswith("sentence_transformers")


def install_model_guard() -> None:
    """Wrap ``LocalEmbeddingProvider._ensure_model`` once for the whole process."""
    if _state["model_guard"]:
        return
    from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider

    real_ensure_model = LocalEmbeddingProvider._ensure_model  # noqa: SLF001 - the guard wraps the load

    def guarded_ensure_model(self: LocalEmbeddingProvider) -> Any:
        if not self._model_loaded and _sentence_transformer_is_real():
            msg = (
                f"{_state['current']} loaded the local embedding model {self.model_name!r} "
                "(downloaded into its data directory); inject "
                "chaoscypher_core.testing.embedding.FakeEmbeddingProvider"
            )
            _state["model_loads"].append(msg)
            raise TestLoadedEmbeddingModelError(msg)
        return real_ensure_model(self)

    LocalEmbeddingProvider._ensure_model = guarded_ensure_model  # type: ignore[method-assign]  # noqa: SLF001
    _state["model_guard"] = True


install_guard()
install_model_guard()


@pytest.fixture(autouse=True)
def ollama_offline(request: pytest.FixtureRequest) -> Iterator[None]:
    """Name the running test for the guards; stand the Ollama one down under ``@pytest.mark.ollama``.

    Fails the test that loaded the local embedding model even when the code under test caught
    the guard's error and carried on.
    """
    _state["current"] = request.node.nodeid
    _state["allowed"] = request.node.get_closest_marker("ollama") is not None
    _state["model_loads"].clear()
    try:
        yield
    finally:
        _state["current"] = f"{request.node.nodeid} (after it ended)"
        _state["allowed"] = False
    if _state["model_loads"]:
        loads, _state["model_loads"] = _state["model_loads"], []
        pytest.fail(loads[0], pytrace=False)
