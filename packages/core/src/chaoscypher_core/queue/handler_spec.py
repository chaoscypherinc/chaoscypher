# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Handler registration spec with per-handler recovery policy.

Each queue handler is registered with a HandlerSpec that carries the
callable plus its retry policies. The reconciler consults
retry_on_crash when classifying abandoned tasks: handlers with
retry_on_crash=True get requeued after a worker crash (their idempotency
is asserted by the registrant), handlers with retry_on_crash=False get
marked failed. The worker consults retry_on_transient before scheduling
queue-level retries for transient exceptions and timeouts.

Bare callables passed to register_handlers are auto-wrapped with
retry_on_crash=False so existing call sites keep working while
specific handlers opt in explicitly.
"""

from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.queue.types import TaskHandler


_logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HandlerSpec:
    """Handler registration with recovery policy.

    Attributes:
        handler: The async callable that processes tasks.
        retry_on_crash: If True, the reconciler requeues tasks this
            handler was running when a worker crashed (provided attempts
            < max_tries). If False, abandoned tasks are failed with
            error_type="worker_crashed". Default False.
        retry_on_transient: If True, the queue worker retries transient
            exceptions and timeouts up to queue max_tries. Set False for
            handlers that own a domain-specific retry budget and requeue
            themselves. Default True preserves existing queue behavior.
    """

    handler: TaskHandler
    retry_on_crash: bool = False
    retry_on_transient: bool = True


HandlerLike = Union["TaskHandler", HandlerSpec]


def normalize_handler(h: HandlerLike) -> HandlerSpec:
    """Wrap a bare callable in HandlerSpec, pass through existing specs.

    Args:
        h: Either a plain async callable or a pre-built HandlerSpec.

    Returns:
        A HandlerSpec. Bare callables get retry_on_crash=False.
    """
    if isinstance(h, HandlerSpec):
        return h
    return HandlerSpec(handler=h)


# Mirrors chaoscypher_core.queue.client.TaskHandler:
#     async def __call__(self, data, *, metadata, task_id) -> Any
_REQUIRED_DISPATCHER_KWARGS = ("data", "metadata", "task_id")


def _resolve_signature(handler: Any) -> inspect.Signature | None:
    """Return ``inspect.Signature`` for ``handler`` or ``None`` if it can't be introspected.

    Handles the two non-trivial cases we hit in this codebase:

    1. ``functools.partial`` wrappers (canonical pattern in
       ``bulk_service.py`` and similar). When direct introspection fails —
       Python 3.14 + ``from __future__ import annotations`` interactions
       can raise ``NameError`` from inside ``get_annotations`` — we fall
       back to inspecting the underlying function and subtracting the
       arguments the partial has already bound.
    2. Anything else where introspection raises an unrelated exception
       (custom ``__signature__`` properties, C extensions, etc.). Returns
       ``None`` to let the caller skip-with-warning rather than crash.
    """
    try:
        return inspect.signature(handler)
    except Exception:
        pass

    if isinstance(handler, functools.partial):
        try:
            inner_sig = inspect.signature(handler.func)
        except Exception:
            return None
        return _signature_minus_partial_bindings(inner_sig, handler)

    return None


def _signature_minus_partial_bindings(
    sig: inspect.Signature, partial_obj: functools.partial[Any]
) -> inspect.Signature:
    """Subtract args bound by a ``functools.partial`` from a ``Signature``.

    Drops the leading ``len(partial_obj.args)`` positional-or-keyword
    parameters, plus any parameter whose name appears in
    ``partial_obj.keywords``. The result is the *effective* signature the
    dispatcher sees when calling the partial.
    """
    bound_positional = len(partial_obj.args)
    bound_keywords = set(partial_obj.keywords or {})

    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )

    remaining: list[inspect.Parameter] = []
    skipped_positional = 0
    for p in sig.parameters.values():
        if skipped_positional < bound_positional and p.kind in positional_kinds:
            skipped_positional += 1
            continue
        if p.name in bound_keywords:
            continue
        remaining.append(p)

    return inspect.Signature(remaining)


def validate_handler_signature(
    handler: Any,
    *,
    queue: str,
    operation: str,
) -> None:
    """Verify handler accepts the dispatcher's calling convention.

    The queue dispatcher
    (``chaoscypher_core.queue.service._execute_handler``) invokes every
    registered handler as
    ``await handler(data, metadata=metadata, task_id=task_id)``. A handler
    missing one of those parameters raises ``TypeError`` at dispatch time —
    permanent task failure, silent retry storms, sources stuck in pending.

    This validator mirrors the ``TaskHandler`` protocol from
    ``chaoscypher_core.queue.client``. It runs at registration time so any
    drift becomes a startup ``TypeError`` instead of a runtime failure that
    surfaces only on the first real document upload (cf. the regression
    fixed in commit ``5559d28b3``).

    Handlers with a ``**kwargs`` catch-all are accepted — they will receive
    any kwarg the dispatcher passes, present or future.

    ``functools.partial`` wrappers are unwrapped: the underlying function's
    signature minus the bound args is what's checked. This is the pattern
    used by ``bulk_service.py`` (``partial(bulk_*_handler, self)``).

    When introspection fails for an exotic reason (callable with a custom
    ``__signature__`` that raises, certain Python-3.14 + deferred-annotation
    edge cases), the validator logs a warning and skips — registration
    proceeds. Skip-with-warning beats killing the worker over a tooling
    issue: the runtime dispatch will still raise ``TypeError`` on a genuine
    contract violation, exactly as it did before validation existed.

    Args:
        handler: The callable being registered.
        queue: Queue name, included in error messages so operators can
            locate the offending registration site.
        operation: Operation name, ditto.

    Raises:
        TypeError: If ``handler``'s signature is introspectable AND it
            does not accept all of ``data``, ``metadata``, ``task_id``
            (either named explicitly or via ``**kwargs`` for the
            keyword-only pair).
    """
    sig = _resolve_signature(handler)
    if sig is None:
        _logger.warning(
            "queue_handler_signature_skipped",
            queue=queue,
            operation=operation,
            handler_repr=repr(handler),
            hint=(
                "Could not introspect this handler's signature; "
                "registration-time validation skipped. The handler is "
                "still registered. If it doesn't accept (data, metadata=, "
                "task_id=), the runtime dispatch will raise TypeError on "
                "first use."
            ),
        )
        return

    params = sig.parameters
    has_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    has_var_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values())

    if has_var_keyword:
        # ``**kwargs`` accepts metadata + task_id. We still require ``data``
        # be reachable: either named, accepted positionally via ``*args``,
        # or also caught by ``**kwargs``-only signatures (in which case the
        # dispatcher's positional ``data`` would still error).
        has_data = "data" in params or has_var_positional
        if not has_data:
            msg = (
                f"Queue handler {operation!r} on queue {queue!r} does not accept "
                f"the required positional 'data' parameter. The dispatcher calls "
                f"handler(data, metadata=metadata, task_id=task_id). See the "
                f"TaskHandler protocol in chaoscypher_core.queue.client."
            )
            raise TypeError(msg)
        return

    missing = [kw for kw in _REQUIRED_DISPATCHER_KWARGS if kw not in params]
    if missing:
        msg = (
            f"Queue handler {operation!r} on queue {queue!r} is missing required "
            f"parameter(s) {missing}. The dispatcher always invokes handlers as "
            f"handler(data, metadata=metadata, task_id=task_id) — every registered "
            f"handler must accept all three even when the body does not consume "
            f"one of them. See the TaskHandler protocol in "
            f"chaoscypher_core.queue.client."
        )
        raise TypeError(msg)
