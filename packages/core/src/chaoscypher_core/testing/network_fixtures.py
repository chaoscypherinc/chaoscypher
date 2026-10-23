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
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest


OLLAMA_PORT = 11434
_OUTSIDE = "<no test running; a background probe after the last test>"
_state: dict[str, Any] = {"current": _OUTSIDE, "allowed": False, "installed": False}


class TestReachedOllamaError(RuntimeError):
    """A test (or a probe it left behind) opened a connection to an Ollama server."""


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


install_guard()


@pytest.fixture(autouse=True)
def ollama_offline(request: pytest.FixtureRequest) -> Iterator[None]:
    """Name the running test for the guard; stand it down under ``@pytest.mark.ollama``."""
    _state["current"] = request.node.nodeid
    _state["allowed"] = request.node.get_closest_marker("ollama") is not None
    try:
        yield
    finally:
        _state["current"] = f"{request.node.nodeid} (after it ended)"
        _state["allowed"] = False
