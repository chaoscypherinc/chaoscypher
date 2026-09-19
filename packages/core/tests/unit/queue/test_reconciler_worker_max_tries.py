# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The reconciler's retry budget must track the worker's *effective* max_tries.

``max_tries`` is an operator-settable ``workers.yaml`` key. Neuron forwards the
resolved value into the worker's ``queues_config``, so the worker's own
reconcile passes judge a task against it — but Cortex's 150 s safety-net pass
used the bare ``settings.retries.*_worker_max_tries`` default. The two then
disagreed about the same task's remaining budget, and ``_handle_abandoned``'s
else-branch is a dead-letter write (``status="failed"``,
``error_type="worker_crashed"``, SREM from ``running``, 14-day TTL), not a soft
skip. An operator who raised the budget got abandoned tasks terminally failed
early by whichever pass won the reconcile lock.

The sibling knob, ``timeout``, was already fixed this way and carries a comment
saying so; ``max_tries`` was left on exactly the shape that comment calls out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core import policy
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.queue.worker_timeouts import resolve_effective_worker_max_tries


_SETTINGS_DEFAULT = 5
_RAISED_MAX_TRIES = 12


def _write_workers_yaml(data_dir: Path, body: str) -> None:
    """Write a ``workers.yaml`` into a fake data dir."""
    (data_dir / "workers.yaml").write_text(body, encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``PathSettings.data_dir`` at an empty temp directory."""
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    return tmp_path


def test_no_workers_yaml_falls_back_to_the_settings_default(data_dir: Path) -> None:
    """With no override file the settings default is the budget."""
    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _SETTINGS_DEFAULT
    )


def test_raised_override_wins_over_the_settings_default(data_dir: Path) -> None:
    """The regression: an operator's raise must reach the reconciler."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  max_tries: {_RAISED_MAX_TRIES}\n")

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _RAISED_MAX_TRIES
    )


def test_per_queue_overrides_are_independent(data_dir: Path) -> None:
    """Raising one worker's budget must not move the other's."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  max_tries: {_RAISED_MAX_TRIES}\n")

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _RAISED_MAX_TRIES
    )
    assert (
        resolve_effective_worker_max_tries(QUEUE_OPERATIONS, default=_SETTINGS_DEFAULT)
        == _SETTINGS_DEFAULT
    )


def test_override_is_clamped_to_the_shared_ceiling(data_dir: Path) -> None:
    """Resolution must clamp exactly as ``load_worker_config`` does.

    Both sides clamp with ``policy.WORKER_MAX_TRIES_MAX``; drift would put the
    reconciler's budget above the one the worker booted with.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  max_tries: 9999\n")

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == policy.WORKER_MAX_TRIES_MAX
    )


def test_override_lowered_below_default_never_shrinks_the_budget(data_dir: Path) -> None:
    """Floored at the default, mirroring the timeout resolver.

    An operator who lowers the value without restarting workers leaves the
    worker enforcing the old, higher number. Honouring the lower one here would
    terminally fail work that worker would still retry.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  max_tries: 2\n")

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _SETTINGS_DEFAULT
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        "llm_worker:\n",
        "llm_worker:\n  max_concurrent: 4\n",
        "llm_worker:\n  max_tries: true\n",
        "llm_worker:\n  max_tries: not-a-number\n",
        "- just\n- a\n- list\n",
    ],
    ids=["empty", "null-section", "other-key", "bool", "string", "non-mapping"],
)
def test_unusable_override_falls_back_to_the_settings_default(data_dir: Path, body: str) -> None:
    """Shapes that say "the worker booted on its default" resolve to it.

    YAML booleans coerce silently (``True == 1``) and would otherwise cut the
    budget to a single dispatch — rejected here exactly as Neuron rejects them.
    """
    _write_workers_yaml(data_dir, body)

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _SETTINGS_DEFAULT
    )


def test_unparseable_workers_yaml_falls_back_to_the_settings_default(data_dir: Path) -> None:
    """Deliberately NOT the fail-long policy the timeout resolver uses.

    Failing long there (the clamp ceiling) avoids requeuing live work. Failing
    long here would be a budget of 20, re-dispatching a poison task far past
    what the operator configured. The default is the pre-existing behaviour, so
    an unreadable file is never worse than before this resolver existed.
    """
    _write_workers_yaml(data_dir, "llm_worker:\n  max_tries: [unclosed\n")

    assert (
        resolve_effective_worker_max_tries(QUEUE_LLM, default=_SETTINGS_DEFAULT)
        == _SETTINGS_DEFAULT
    )


def test_unknown_queue_uses_the_supplied_default(data_dir: Path) -> None:
    """A queue with no workers.yaml section resolves to the default."""
    _write_workers_yaml(data_dir, f"llm_worker:\n  max_tries: {_RAISED_MAX_TRIES}\n")

    assert (
        resolve_effective_worker_max_tries("nope", default=_SETTINGS_DEFAULT) == _SETTINGS_DEFAULT
    )
