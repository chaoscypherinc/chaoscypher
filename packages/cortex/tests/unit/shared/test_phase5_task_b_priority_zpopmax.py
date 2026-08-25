# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task B: priority convention flipped to ZPOPMAX.

Contract tests asserting that the scheduler pops the highest-scored task
first, that ``PrioritySettings`` encodes that ordering, and that no
``ZPOPMIN`` / ``zpopmin`` reference survives in the queue subsystem.

2026-08-12: three of these tests asserted on the literal *text* of
``client.py`` / ``worker.py`` (via ``Path.read_text``) or on a word in a
docstring, and each would have stayed green while the contract it names
was broken:

* ``assert src.count("float(priority) - time.time() / 1e10") >= 2`` —
  satisfied while the computed score is discarded
  (``pipeline.zadd(key, {task_id: 0.0})``), which destroys priority
  ordering AND intra-tier FIFO at once.
* ``assert "self.client.zpopmax(" in src`` — satisfied by one dead call
  left behind after the real pop moved into a ``zpopmin`` helper.
* ``assert "zpopmax" in doc`` — a word in a docstring.

They were also brittle in the other direction: extracting a
``_score(priority)`` helper broke all three with zero behavioural change.
They are now driven against a fake Valkey that keeps a real sorted set,
so the assertions are about observed scores and observed pop order.

``test_queue_subsystem_free_of_zpopmin_references`` below is deliberately
still a text scan — it is a repo-wide "no stragglers" sweep rather than a
contract test, and there is no behavioural equivalent.

2026-08-15: the intra-tier tiebreaker itself was replaced. It used to be
``time.time() / 1e10``, which sits below the score's float ULP at
priority=100 after ~142us (confirmed: two ``time.time()`` calls 50us
apart produced a bit-identical score; 200us apart did not) — a tight
synchronous loop (``enqueue_tasks_batch``) routinely lands under that
window, so a whole batch could collapse onto one score and fall back to
Valkey's lexicographic task-id tiebreak instead of enqueue order. It is
now a per-queue Valkey INCR/INCRBY sequence counter (exact and
monotonic, never collides regardless of timing) — see
``compose_pending_score`` and the ``_PENDING_SCORE_SEQ_SCALE`` comment in
``queue/client.py`` for the full bound. The fake below grew
``incr``/``incrby`` so these tests exercise the real scoring path instead
of faking a clock.

2026-08-15 review round: two follow-up fixes. (1) ``QueueWorker.
_retry_task`` also needed to move off its time-derived fraction — see
``test_retry_task_rejoins_at_the_current_tail_of_its_tier`` below and the
coherence writeup in ``queue/client.py``. (2) ``_FakeValkey.zpopmax`` now
breaks same-score ties the way real Valkey does — (score, member),
GREATEST member first — instead of Python's stable sort silently falling
back to dict insertion order; ``test_fifo_order_survives_adversarial_task_ids``
was rewritten to ids that ASCEND in enqueue order so a collapsed-score
regression is actually observable (a descending-id version could pass by
coincidence — greatest-first tiebreak plus descending ids reproduces
enqueue order even when every task shares one score).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.app_config import PrioritySettings
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.worker import QueueWorker


REPO_ROOT = Path(__file__).resolve().parents[4]
CORE_SRC = REPO_ROOT / "core" / "src" / "chaoscypher_core"
QUEUE_DIR = CORE_SRC / "queue"

_PENDING_KEY = f"queue:{QUEUE_OPERATIONS}:pending"


# ---------------------------------------------------------------------------
# Fake Valkey with a real sorted set
# ---------------------------------------------------------------------------


class _FakePipeline:
    """Records the zadd members/scores; every other verb is a no-op."""

    def __init__(self, backend: _FakeValkey) -> None:
        self._backend = backend

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._backend.zsets.setdefault(key, {}).update(mapping)

    def __getattr__(self, _name: str) -> Any:
        # hset / lpush / ltrim / expire / set ... — irrelevant here.
        return lambda *_a, **_k: None

    async def execute(self) -> list[Any]:
        return []


class _FakeValkey:
    """Minimal Valkey double backing zadd/zpopmax/incr with a real sorted set."""

    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}
        self.zpopmax_calls: list[tuple[str, int]] = []
        self._counters: dict[str, int] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def zpopmax(self, key: str, count: int = 1) -> list[tuple[bytes, float]]:
        self.zpopmax_calls.append((key, count))
        zset = self.zsets.setdefault(key, {})
        # Real Valkey's ZSET total order is (score, member) both ascending;
        # ZPOPMAX pops from the top of that order, so a same-score tie
        # breaks on the member's byte value, GREATEST first. Sorting by
        # score alone would let Python's stable sort silently fall back to
        # dict insertion order on a tie -- hiding exactly the score-collapse
        # bug this fake exists to catch (2026-08-15 review round).
        popped = sorted(zset.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:count]
        for member, _score in popped:
            del zset[member]
        return [(member.encode(), score) for member, score in popped]

    async def zpopmin(self, key: str, count: int = 1) -> list[tuple[bytes, float]]:
        msg = f"zpopmin({key!r}) called — Phase 5 flipped the queue to ZPOPMAX"
        raise AssertionError(msg)

    async def incr(self, key: str) -> int:
        """Real INCR semantics: missing key starts at 0, returns post-increment value."""
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    async def incrby(self, key: str, amount: int) -> int:
        """Real INCRBY semantics: returns the counter's value after adding amount."""
        self._counters[key] = self._counters.get(key, 0) + amount
        return self._counters[key]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """Bare (non-pipelined) ZADD — QueueWorker._retry_task calls this directly."""
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def hset(self, key: str, mapping: dict[str, str] | None = None) -> int:
        """Minimal HSET into an in-memory hash store, for _retry_task's status write."""
        fields = mapping or {}
        self._hashes.setdefault(key, {}).update(fields)
        return len(fields)

    async def hget(self, key: str, field: str) -> bytes | None:
        """Minimal HGET — _retry_task reads the task's original priority via this."""
        value = self._hashes.get(key, {}).get(field)
        return value.encode() if isinstance(value, str) else value

    async def persist(self, key: str) -> bool:
        """No-op PERSIST (this fake never sets TTLs)."""
        return True


def _make_queue_client(backend: _FakeValkey) -> QueueClient:
    client = QueueClient()
    client.client = backend  # type: ignore[assignment]
    client._connected = True
    return client


async def _drain(backend: _FakeValkey) -> list[str]:
    """Pop the pending set to exhaustion and return member IDs in pop order."""
    order: list[str] = []
    while True:
        items = await backend.zpopmax(_PENDING_KEY, count=1)
        if not items:
            return order
        order.append(items[0][0].decode())


# ---------------------------------------------------------------------------
# PrioritySettings
# ---------------------------------------------------------------------------


def test_priority_settings_ordering() -> None:
    """Interactive > background > default — matches ZPOPMAX."""
    priorities = PrioritySettings()

    assert priorities.interactive > priorities.background > priorities.default, (
        f"PrioritySettings defaults must satisfy interactive > background > default "
        f"(got interactive={priorities.interactive} background={priorities.background} "
        f"default={priorities.default}). Higher numeric priority pops first under ZPOPMAX."
    )
    assert priorities.interactive == 100
    assert priorities.default == 1


@pytest.mark.asyncio
async def test_priority_settings_tiers_pop_in_declared_order() -> None:
    """Tasks enqueued at the declared tiers pop interactive → background → default.

    Replaces a docstring word-check (``assert "zpopmax" in doc``) with the
    property that docstring was standing in for: the numeric tiers really
    do produce that dequeue order through the live enqueue path.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)
    priorities = PrioritySettings()

    # Enqueued lowest-first so pop order cannot accidentally match insertion.
    ids = {}
    for tier in ("default", "background", "interactive"):
        ids[tier] = await client.enqueue(
            QUEUE_OPERATIONS, "test_op", {}, priority=getattr(priorities, tier)
        )

    assert await _drain(backend) == [
        ids["interactive"],
        ids["background"],
        ids["default"],
    ]


# ---------------------------------------------------------------------------
# Enqueue score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_score_ranks_priority_above_enqueue_time() -> None:
    """Priority dominates the score; the seq fraction never crosses a tier.

    A score that is computed and then discarded (``zadd(key, {tid: 0.0})``)
    collapses every task onto one score and fails here.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    low = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=1)
    mid = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=50)
    high = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)

    scores = backend.zsets[_PENDING_KEY]
    assert scores[high] > scores[mid] > scores[low], "priority tier must dominate the score"
    # seq / _PENDING_SCORE_SEQ_SCALE is a sub-1 tiebreaker, so it can
    # never promote a task across a tier boundary however long the
    # counter has been running.
    for task_id, priority in ((low, 1), (mid, 50), (high, 100)):
        assert 0 < priority - scores[task_id] < 1, (
            f"seq component escaped its tier for priority={priority}"
        )
    assert await _drain(backend) == [high, mid, low]


@pytest.mark.asyncio
async def test_enqueue_score_is_fifo_within_a_priority_tier() -> None:
    """Within one tier, the earlier enqueue scores HIGHER so it pops first.

    2026-08-15: no clock injection needed any more — the tiebreaker comes
    from a real (fake-backed) Valkey INCR counter, which is exact and
    monotonic by construction, so three back-to-back enqueue() calls
    already draw three distinct, correctly-ordered seq values regardless
    of how close together in time they run.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    first = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    second = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    third = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)

    scores = backend.zsets[_PENDING_KEY]
    assert scores[first] > scores[second] > scores[third], (
        "within a priority tier the earlier enqueue must score HIGHER so it "
        "pops first under ZPOPMAX (FIFO); a '+' in place of the '-' inverts this"
    )
    assert await _drain(backend) == [first, second, third]


@pytest.mark.asyncio
async def test_fifo_order_survives_adversarial_task_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pop order follows enqueue order even when task IDs sort the opposite way.

    Forces ``generate_id()`` to hand out IDs in ASCENDING lexicographic
    order (task-aaa, task-mmm, task-zzz) while priority stays constant.
    This is the direct regression test for the filed bug: under a
    same-score collapse, real Valkey's ZSET tie-break (score, then
    member, GREATEST-first — see ``_FakeValkey.zpopmax``) would pop
    task-zzz FIRST even though task-aaa was enqueued first. Handing the
    ids out in DESCENDING order (as an earlier version of this test did)
    would have let a collapsed-score bug hide: descending ids plus a
    greatest-first tiebreak coincidentally reproduce enqueue order, so
    the test would pass whether or not the fix actually works. Ascending
    ids make the tiebreak-vs-enqueue-order distinction observable.
    """
    from chaoscypher_core.queue import client as client_mod

    backend = _FakeValkey()
    client = _make_queue_client(backend)

    adversarial_ids = iter(["task-aaa", "task-mmm", "task-zzz"])
    monkeypatch.setattr(client_mod, "generate_id", lambda: next(adversarial_ids))

    first = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    second = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    third = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)

    assert (first, second, third) == ("task-aaa", "task-mmm", "task-zzz")
    assert await _drain(backend) == [first, second, third], (
        "pop order must follow enqueue order — a same-score collapse "
        "would tiebreak to task-zzz first (greatest member), not "
        "enqueue order"
    )


@pytest.mark.asyncio
async def test_batch_enqueue_scores_match_the_single_enqueue_path() -> None:
    """``enqueue_tasks_batch`` encodes the same priority-then-FIFO ordering.

    2026-08-15: previously the whole batch could share ONE
    ``time.time()``-derived score (recomputed every loop iteration, but
    successive calls inside a tight synchronous loop routinely land
    under the ULP collision window — see
    ``test_enqueue_and_batch_never_consult_the_wall_clock`` below).
    Each item now draws its own seq from a single reserved block.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    low, high_first, high_second = await client.enqueue_tasks_batch(
        QUEUE_OPERATIONS,
        [
            {"operation": "test_op", "data": {}, "priority": 1, "metadata": {}},
            {"operation": "test_op", "data": {}, "priority": 100, "metadata": {}},
            {"operation": "test_op", "data": {}, "priority": 100, "metadata": {}},
        ],
    )

    scores = backend.zsets[_PENDING_KEY]
    assert scores[high_first] > scores[low], "priority tier must dominate the score"
    assert scores[high_first] > scores[high_second], "intra-tier FIFO must survive batching"
    assert len({scores[low], scores[high_first], scores[high_second]}) == 3, (
        "every item in the batch must get a distinct score"
    )
    assert await _drain(backend) == [high_first, high_second, low]


@pytest.mark.asyncio
async def test_batch_enqueue_scores_are_all_distinct_and_ordered() -> None:
    """Every item in a same-priority batch gets its own strictly-ordered score.

    Directly targets the filed bug's batch-collapse symptom with a batch
    big enough (10 items) that a shared score would be obvious.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    ids = await client.enqueue_tasks_batch(
        QUEUE_OPERATIONS,
        [{"operation": "test_op", "data": {}, "priority": 50, "metadata": {}} for _ in range(10)],
    )

    scores = backend.zsets[_PENDING_KEY]
    ordered_scores = [scores[task_id] for task_id in ids]
    assert ordered_scores == sorted(ordered_scores, reverse=True), (
        "batch scores must strictly decrease in insertion order"
    )
    assert len(set(ordered_scores)) == len(ordered_scores), "batch scores must all be distinct"
    assert await _drain(backend) == ids


@pytest.mark.asyncio
async def test_high_tier_beats_low_tier_even_after_many_low_tier_enqueues() -> None:
    """A fresh high-priority enqueue still outranks an 'aged' low-priority tier.

    Guards the priority-dominance bound: even after many low-tier
    enqueues have pushed that tier's seq counter far ahead, a single
    higher-tier enqueue must still score above every one of them —
    priority never loses to sequence/age.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    low_ids = [
        await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=1) for _ in range(500)
    ]
    high_id = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=2)

    scores = backend.zsets[_PENDING_KEY]
    assert scores[high_id] > max(scores[task_id] for task_id in low_ids), (
        "a higher priority tier must dominate regardless of how many "
        "sequence numbers the lower tier has consumed"
    )


@pytest.mark.asyncio
async def test_enqueue_and_batch_never_consult_the_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards against reintroducing a time.time()-based tiebreaker.

    Patches the REAL stdlib ``time.time`` to raise, so any call to it
    during ``enqueue()``/``enqueue_tasks_batch()`` fails this test loudly.
    The whole point of the seq-based fix is that these paths no longer
    need the wall clock to guarantee ordering — this is the strongest
    form of that guarantee: not "distinguishable most of the time," but
    "never asked."
    """
    import time as real_time_mod

    def _boom() -> float:
        msg = "score computation must not read the wall clock"
        raise AssertionError(msg)

    monkeypatch.setattr(real_time_mod, "time", _boom)

    backend = _FakeValkey()
    client = _make_queue_client(backend)

    first = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    second = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)
    low, high_first, high_second = await client.enqueue_tasks_batch(
        QUEUE_OPERATIONS,
        [
            {"operation": "test_op", "data": {}, "priority": 1, "metadata": {}},
            {"operation": "test_op", "data": {}, "priority": 100, "metadata": {}},
            {"operation": "test_op", "data": {}, "priority": 100, "metadata": {}},
        ],
    )

    scores = backend.zsets[_PENDING_KEY]
    assert scores[first] > scores[second], "FIFO ordering must hold without reading the clock"
    assert scores[high_first] > scores[low], "priority tier must dominate the score"
    assert scores[high_first] > scores[high_second], "intra-tier FIFO must survive batching"


# ---------------------------------------------------------------------------
# Retry scoring (2026-08-15 review round)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_task_rejoins_at_the_current_tail_of_its_tier() -> None:
    """A retry scheduled now sorts after enqueues drawn before it, before ones after.

    Regression test for the review finding that ``QueueWorker._retry_task``'s
    OLD score — ``priority - (time.time() + backoff) / 1e10`` — used a
    fraction (~0.18 at any 2026-era epoch) that is CONSTANT once written,
    while a fresh enqueue's seq-based fraction keeps growing. The two would
    eventually cross, silently flipping every outstanding retry to the
    FRONT of its tier ahead of fresh work, once the per-queue counter
    passed ~1.965e11 — well inside the documented tier-crossing ceiling,
    not a multi-year-away edge case.

    The fix draws a FRESH seq from the same per-queue counter at
    retry-schedule time, so the retry takes the queue's CURRENT TAIL
    position — same mechanism as a plain enqueue. This test proves that
    ordering directly: enqueue something, schedule a retry, enqueue
    something else, and confirm the retry pops strictly between the two.

    Eligibility (whether a popped-but-not-yet-due task is actually
    dispatched) is a SEPARATE mechanism (`_get_retry_after` / the
    `retry_after` hash field) and is unaffected by this change — this
    test only checks queue POSITION, not dispatch timing.
    """
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    before = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=50)

    # Seed the retried task's hash directly (bypassing a full
    # dispatch-fail cycle, which is out of scope for a scoring test) and
    # drive the real _retry_task scoring path against the shared backend.
    retried_id = "task-retried"
    backend._hashes[f"queue:task:{retried_id}"] = {"priority": "50"}
    worker = QueueWorker(
        client=backend,  # type: ignore[arg-type]
        queues_config={QUEUE_OPERATIONS: {"concurrency": 1, "max_tries": 3, "timeout": 60}},
        handlers={QUEUE_OPERATIONS: {}},
        poll_interval=0,
    )
    await worker._retry_task(retried_id, QUEUE_OPERATIONS, attempt=1, max_tries=3)

    after = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=50)

    assert await _drain(backend) == [before, retried_id, after], (
        "a retry must rejoin its tier at the CURRENT TAIL: after everything "
        "already enqueued, before anything enqueued afterward"
    )


# ---------------------------------------------------------------------------
# Worker poll direction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_poll_pops_with_zpopmax() -> None:
    """``_poll_queue`` reaches the pending set via ZPOPMAX, never ZPOPMIN.

    The fake raises on ``zpopmin``, so moving the real pop into a helper
    that calls it fails here even if a dead ``self.client.zpopmax(`` is
    left behind in the module text.
    """
    backend = _FakeValkey()
    config = {"concurrency": 1, "max_tries": 3, "timeout": 60}
    worker = QueueWorker(
        client=backend,  # type: ignore[arg-type]
        queues_config={QUEUE_OPERATIONS: config},
        handlers={QUEUE_OPERATIONS: {}},
        poll_interval=0,
    )
    worker._running = True

    real_zpopmax = backend.zpopmax

    async def _stop_after_one_poll(key: str, count: int = 1) -> list[tuple[bytes, float]]:
        worker._running = False
        return await real_zpopmax(key, count)

    backend.zpopmax = _stop_after_one_poll  # type: ignore[method-assign]

    await asyncio.wait_for(worker._poll_queue(QUEUE_OPERATIONS, config), timeout=5.0)

    assert backend.zpopmax_calls, "_poll_queue never popped the pending set"
    assert backend.zpopmax_calls[0][0] == _PENDING_KEY


@pytest.mark.asyncio
async def test_worker_poll_takes_the_highest_scored_task_first() -> None:
    """End to end: the task the client scored highest is the one the worker pops."""
    backend = _FakeValkey()
    client = _make_queue_client(backend)

    low = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=1)
    high = await client.enqueue(QUEUE_OPERATIONS, "test_op", {}, priority=100)

    config = {"concurrency": 1, "max_tries": 3, "timeout": 60}
    worker = QueueWorker(
        client=backend,  # type: ignore[arg-type]
        queues_config={QUEUE_OPERATIONS: config},
        handlers={QUEUE_OPERATIONS: {}},
        poll_interval=0,
    )
    worker._running = True

    popped: list[str] = []
    real_zpopmax = backend.zpopmax

    async def _record_then_stop(key: str, count: int = 1) -> list[tuple[bytes, float]]:
        items = await real_zpopmax(key, count)
        popped.extend(member.decode() for member, _ in items)
        worker._running = False
        return items

    backend.zpopmax = _record_then_stop  # type: ignore[method-assign]

    await asyncio.wait_for(worker._poll_queue(QUEUE_OPERATIONS, config), timeout=5.0)

    assert popped[:1] == [high], f"worker took {popped[:1]} before the priority-100 task {high}"
    assert low in backend.zsets[_PENDING_KEY], "the low-priority task should still be pending"


# ---------------------------------------------------------------------------
# Repo-wide straggler sweep (text scan by design — see module docstring)
# ---------------------------------------------------------------------------


def test_queue_subsystem_free_of_zpopmin_references() -> None:
    """Search the entire queue package for any ZPOPMIN/zpopmin reference."""
    offenders: list[str] = []
    for path in QUEUE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "zpopmin" in line.lower():
                offenders.append(f"{path.relative_to(CORE_SRC)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "The queue subsystem must not reference ZPOPMIN (Phase 5 flipped to ZPOPMAX):\n"
        + "\n".join(offenders)
    )
