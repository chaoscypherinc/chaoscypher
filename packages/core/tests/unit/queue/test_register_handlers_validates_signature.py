# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: ``QueueClient.register_handlers`` rejects handlers that don't
satisfy the dispatcher's calling convention.

The dispatcher (``chaoscypher_core.queue.service._execute_handler``)
invokes every registered handler as
``await handler(data, metadata=metadata, task_id=task_id)``. A handler
missing one of those parameters raises ``TypeError`` at dispatch time —
permanent task failure, silent retry storms, sources stuck in pending.

To prevent the entire class of bug, registration validates the handler
signature up front and refuses to register a handler that doesn't
structurally match the ``TaskHandler`` protocol. Wiring drift becomes
a startup error instead of a runtime failure that surfaces only on
the first real document upload.

Replaces the per-service ``test_handlers_accept_dispatcher_kwargs.py``,
which only covered ``ImportOperationsService``. With registration-time
validation, every service's handlers are checked at startup with no
per-service test required.
"""

from __future__ import annotations

from typing import Any

import pytest

from chaoscypher_core.queue.client import QueueClient


# ---------------------------------------------------------------------------
# Sample handlers — synthetic, mirror real handler shapes.
# ---------------------------------------------------------------------------


async def good_handler(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {"data": data}


async def missing_task_id(
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {}


async def missing_metadata(
    data: dict[str, Any],
    task_id: str | None = None,
) -> dict[str, Any]:
    return {}


async def missing_data(
    *,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {}


async def kwargs_catchall(data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """A handler with ``**kwargs`` accepts any keyword and is valid."""
    return {}


# Handlers used to exercise functools.partial wrapping — the canonical
# pattern in bulk_service.py and similar (free function with `service` as
# first positional arg, bound via partial(fn, self) at registration time).
async def free_with_service(
    service: Any,
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {}


async def free_with_service_missing_task_id(
    service: Any,
    data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> QueueClient:
    return QueueClient()


def test_accepts_handler_with_all_three_kwargs(client: QueueClient) -> None:
    """Canonical shape registers without complaint."""
    client.register_handlers("operations", {"good_op": good_handler})

    registered = client.get_handler("operations", "good_op")
    assert registered is good_handler


def test_rejects_handler_missing_task_id(client: QueueClient) -> None:
    """The exact regression Bug 1 produced — task_id dropped from a handler."""
    with pytest.raises(TypeError, match=r"task_id"):
        client.register_handlers("operations", {"bad_op": missing_task_id})


def test_rejects_handler_missing_metadata(client: QueueClient) -> None:
    with pytest.raises(TypeError, match=r"metadata"):
        client.register_handlers("operations", {"bad_op": missing_metadata})


def test_rejects_handler_missing_data(client: QueueClient) -> None:
    with pytest.raises(TypeError, match=r"data"):
        client.register_handlers("operations", {"bad_op": missing_data})


def test_accepts_handler_with_var_keyword(client: QueueClient) -> None:
    """``**kwargs`` is structurally acceptable — it catches any kwarg the
    dispatcher passes, present and future.
    """
    client.register_handlers("operations", {"kwargs_op": kwargs_catchall})

    assert client.get_handler("operations", "kwargs_op") is kwargs_catchall


def test_error_message_identifies_queue_and_operation(client: QueueClient) -> None:
    """The error must point operators to the offending registration site."""
    with pytest.raises(TypeError) as exc_info:
        client.register_handlers("operations", {"naughty_op": missing_task_id})

    msg = str(exc_info.value)
    assert "operations" in msg
    assert "naughty_op" in msg


def test_failure_in_one_handler_does_not_corrupt_registry(client: QueueClient) -> None:
    """A bad handler in a batch must not leave the registry partially populated.

    Without this guarantee, an operator could see "registration failed" but
    end up with N-1 handlers half-registered, complicating diagnosis.
    """
    # Pre-register one good handler on a different queue.
    client.register_handlers("llm", {"existing": good_handler})

    handlers = {"good": good_handler, "broken": missing_task_id}
    with pytest.raises(TypeError):
        client.register_handlers("operations", handlers)

    # Pre-existing registrations must survive.
    assert client.get_handler("llm", "existing") is good_handler
    # Failed batch must not have partially registered the good handler from
    # the same dict — all-or-nothing semantics.
    assert client.get_handler("operations", "good") is None
    assert client.get_handler("operations", "broken") is None


def test_handler_spec_passes_through_validation(client: QueueClient) -> None:
    """``HandlerSpec``-wrapped handlers are validated identically to bare
    callables — the wrapper does not let signature drift sneak past.
    """
    from chaoscypher_core.queue.handler_spec import HandlerSpec

    bad_spec = HandlerSpec(handler=missing_task_id, retry_on_crash=True)
    with pytest.raises(TypeError, match=r"task_id"):
        client.register_handlers("operations", {"speccy": bad_spec})

    good_spec = HandlerSpec(handler=good_handler, retry_on_crash=True)
    client.register_handlers("operations", {"good_speccy": good_spec})
    assert client.get_handler("operations", "good_speccy") is good_handler


def test_accepts_functools_partial_with_self_bound(client: QueueClient) -> None:
    """``functools.partial(handler, service)`` is the canonical pattern used
    by ``bulk_service.py`` and similar: the leading positional arg is bound
    to a service instance, leaving the dispatcher kwargs in the effective
    signature.

    Regression: an earlier version of the validator crashed at neuron
    startup on these handlers because Python 3.14's ``inspect.signature``
    raised an exception (other than ``TypeError``/``ValueError``) when
    introspecting partials with deferred annotations, and the validator
    only caught ``TypeError`` and ``ValueError``.
    """
    import functools

    bound = functools.partial(free_with_service, object())
    client.register_handlers("operations", {"bulk_op": bound})

    assert client.get_handler("operations", "bulk_op") is bound


def test_rejects_partial_when_underlying_function_missing_kwarg(
    client: QueueClient,
) -> None:
    """A partial that wraps a function missing a dispatcher kwarg must still
    be rejected — the validator looks past the partial.
    """
    import functools

    bound = functools.partial(free_with_service_missing_task_id, object())
    with pytest.raises(TypeError, match=r"task_id"):
        client.register_handlers("operations", {"bulk_op": bound})


def test_accepts_real_bulk_handlers_via_partial(client: QueueClient) -> None:
    """Strict regression: the exact partial-wrapping pattern from
    ``bulk_service.py`` must register without raising.

    On 2026-05-07 the validator's first iteration crashed neuron startup
    on these handlers — Python 3.14's ``inspect.signature`` raised an
    exception (not ``TypeError`` / ``ValueError``) introspecting partials
    of free functions that use ``from __future__ import annotations``,
    and the validator only caught the narrow exception types. This test
    pins the production wiring so a re-narrowing of the catch is caught
    here before it can hit a worker startup again.
    """
    import functools

    from chaoscypher_core.operations.bulk.bulk_edge_ops import bulk_edges_handler
    from chaoscypher_core.operations.bulk.bulk_node_ops import bulk_nodes_handler
    from chaoscypher_core.operations.bulk.bulk_template_ops import (
        bulk_templates_handler,
    )

    fake_service = object()  # bulk_*_handler's first positional arg
    handlers = {
        "bulk_nodes": functools.partial(bulk_nodes_handler, fake_service),
        "bulk_edges": functools.partial(bulk_edges_handler, fake_service),
        "bulk_templates": functools.partial(bulk_templates_handler, fake_service),
    }

    # Must not raise — this is exactly what BulkOperationsService does at
    # neuron startup time.
    client.register_handlers("operations", handlers)

    for op, handler in handlers.items():
        assert client.get_handler("operations", op) is handler


def test_real_bulk_handlers_are_introspectable() -> None:
    """The bulk handlers must be introspectable, not just registerable.

    ``test_accepts_real_bulk_handlers_via_partial`` above only asserts the
    skip-with-warning path keeps startup alive. That path leaves the
    handlers UNVALIDATED — a signature drift on a bulk handler would
    surface only at first dispatch, defeating the validator's purpose.

    The handlers' modules must declare ``from __future__ import
    annotations`` so the TYPE_CHECKING-only ``BulkOperationsService``
    annotation stays as a string and ``inspect.signature`` can return a
    real ``Signature`` (with string-form annotations) instead of raising
    ``NameError`` from inside ``get_annotations``.

    We assert directly on ``_resolve_signature`` rather than capturing the
    structlog warning because that's the failure mode the warning is a
    proxy for: ``None`` from ``_resolve_signature`` is what skips
    validation.
    """
    import functools

    from chaoscypher_core.operations.bulk.bulk_edge_ops import bulk_edges_handler
    from chaoscypher_core.operations.bulk.bulk_node_ops import bulk_nodes_handler
    from chaoscypher_core.operations.bulk.bulk_template_ops import (
        bulk_templates_handler,
    )
    from chaoscypher_core.queue.handler_spec import _resolve_signature

    fake_service = object()
    handlers = {
        "bulk_nodes": functools.partial(bulk_nodes_handler, fake_service),
        "bulk_edges": functools.partial(bulk_edges_handler, fake_service),
        "bulk_templates": functools.partial(bulk_templates_handler, fake_service),
    }

    for name, handler in handlers.items():
        sig = _resolve_signature(handler)
        assert sig is not None, (
            f"_resolve_signature returned None for {name} — validator will "
            "fall into skip-with-warning path and skip signature validation "
            "for this handler. Likely cause: the handler's module imports "
            "BulkOperationsService under TYPE_CHECKING without `from "
            "__future__ import annotations`."
        )
        # And the effective signature (post-partial-binding) must expose
        # the three dispatcher parameters so real validation can run.
        params = sig.parameters
        assert "data" in params
        assert "metadata" in params
        assert "task_id" in params


def test_skips_validation_gracefully_when_signature_uninspectable(
    client: QueueClient,
) -> None:
    """If ``inspect.signature`` fails for a non-handler reason (e.g. an
    unwrappable callable, deferred-annotation issue we don't know how to
    work around), registration must NOT crash startup. The handler is
    registered with a warning instead of a hard refusal.

    Skip-with-warning is strictly better than the pre-validator behavior
    (silent runtime failure on first dispatch) for the genuinely-
    uninspectable case, while still catching the common contract drift.
    """

    class Uninspectable:
        # Force ``inspect.signature(self)`` to raise something the validator
        # didn't pre-catch. ``RuntimeError`` is unrelated to TypeError /
        # ValueError, mirroring the Python-3.14-on-partial failure mode.
        @property
        def __signature__(self) -> Any:
            msg = "synthetic introspection failure"
            raise RuntimeError(msg)

        async def __call__(
            self, data: dict[str, Any], metadata: Any = None, task_id: Any = None
        ) -> dict[str, Any]:
            return {}

    handler = Uninspectable()
    # Must not raise: validation skipped, handler still registered.
    client.register_handlers("operations", {"weird": handler})
    assert client.get_handler("operations", "weird") is handler
