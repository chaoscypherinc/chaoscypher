# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Module-surface tests for the three neuron-local handler modules."""

from __future__ import annotations

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401


def test_chat_completion_module_exposes_register_helper() -> None:
    """chat_completion exports a register helper callable."""
    from chaoscypher_neuron.handlers import chat_completion

    register = getattr(chat_completion, "register_chat_completion_handler", None)
    assert callable(register), "register_chat_completion_handler must exist as a callable export"


def test_template_embedding_module_exposes_register_helper() -> None:
    """template_embedding exports a register helper callable."""
    from chaoscypher_neuron.handlers import template_embedding

    register = getattr(template_embedding, "register_template_embedding_handler", None)
    assert callable(register), "register_template_embedding_handler must exist as a callable export"


def test_quality_scores_module_exists_and_loads() -> None:
    """quality_scores module is importable without side effects.

    The module may expose either a register_* helper or a handler function
    directly.  Confirm at least one of the conventional names is present.
    Both the singular (register_quality_score_handler) and plural
    (register_quality_scores_handler / handle_recalculate_quality_scores)
    forms are accepted.
    """
    import chaoscypher_neuron.handlers.quality_scores as qs

    has_register = any(
        callable(getattr(qs, name, None))
        for name in (
            "register_quality_score_handler",
            "register_quality_scores_handler",
            "handle_recalculate_quality_scores",
        )
    )
    assert has_register, (
        "quality_scores module must expose either register_quality_score_handler, "
        "register_quality_scores_handler, or handle_recalculate_quality_scores "
        "as a callable"
    )


def test_all_neuron_handler_modules_have_no_module_level_side_effects(
    worker_harness: object,
) -> None:
    """Importing the three handler modules must not call queue_client.register_handlers.

    Registration belongs in setup_*_handlers, not module top level.  This test
    re-imports each handler module fresh and confirms the recording queue
    client stays empty.
    """
    import importlib

    importlib.reload(__import__("chaoscypher_neuron.handlers.chat_completion", fromlist=["_"]))
    importlib.reload(__import__("chaoscypher_neuron.handlers.template_embedding", fromlist=["_"]))
    importlib.reload(__import__("chaoscypher_neuron.handlers.quality_scores", fromlist=["_"]))

    # Access the queue recorder via duck-typing — avoids a fragile
    # cross-directory import of the fixture class itself.
    queue = getattr(worker_harness, "queue", None)
    assert queue is not None, "worker_harness fixture must expose a .queue attribute"
    registered = queue.all_registered_ops()
    assert registered == set(), (
        "Importing a handler module triggered a register_handlers call — that "
        "side effect belongs in setup_*_handlers, not module top level"
    )


def test_register_helper_signatures_are_consistent() -> None:
    """Each register_* helper accepts at least one positional argument (the service/ctx).

    Uses ``follow_wrapped=False`` and avoids evaluating string annotations so
    this test is safe under Python 3.14 deferred-annotation semantics when
    some annotation names are only available under ``TYPE_CHECKING``.
    """
    from chaoscypher_neuron.handlers.chat_completion import register_chat_completion_handler
    from chaoscypher_neuron.handlers.template_embedding import register_template_embedding_handler

    for fn in (register_chat_completion_handler, register_template_embedding_handler):
        # Introspect via __code__ to count positional parameters without
        # evaluating annotations — avoids NameError for TYPE_CHECKING-only types.
        code = fn.__code__
        # co_varnames[:co_argcount] are the positional (and positional-or-keyword)
        # parameter names, excluding *args / **kwargs.
        positional_param_count = code.co_argcount
        assert positional_param_count >= 1, (
            f"{fn.__name__} should take at least one positional argument; "
            f"found {positional_param_count} positional params in __code__"
        )
