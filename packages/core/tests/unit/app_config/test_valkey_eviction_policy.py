# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Defaults and YAML round-trip for ``QueueSettings.maxmemory_policy``.

Background — operability hardening (2026-05-21):
    Valkey runs with ``--maxmemory 2gb``. Under ``volatile-lru`` (the
    previous default) only TTL'd keys are eviction candidates: heartbeats
    (``queue:task:*:heartbeat``) and result records (``queue:result:*``).
    Pending-task hashes (``queue:task:*``) and pending zsets
    (``queue:{queue}:pending``) carry no TTL — so they are *exempt* from
    eviction. When the 2GB ceiling is reached, Valkey evicts the wrong
    set: heartbeats disappear (looking like dead workers, triggering
    spurious recovery storms) and result records disappear (callers
    blocking on ``GET`` time out), while the actual memory pressure —
    pending tasks — survives untouched.

    The fix flips the default to ``noeviction``: memory exhaustion now
    surfaces as loud ``OOM command not allowed`` write failures the
    operator can act on, rather than silent recovery loops. Depth
    backpressure (``QueueSettings.max_pending_queue_depth``) and AOF
    persistence cover the durability story; this setting governs how
    exhaustion presents.

These tests pin both the in-memory default (no regression to
``volatile-lru``) and end-to-end YAML decoding (the field is a plain
``str``, so YAML deserialisation must keep working for any operator
override).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.app_config import QueueSettings, Settings


def test_queue_maxmemory_policy_default_is_noeviction() -> None:
    """In-memory default must be ``noeviction``.

    Guards against accidental revert to ``volatile-lru`` (which silently
    evicts heartbeats and result records under memory pressure while
    leaving pending-task hashes — the actual offender — in place).
    """
    s = Settings()
    assert s.queue.maxmemory_policy == "noeviction"


def test_queue_settings_maxmemory_policy_default_at_field_level() -> None:
    """Same invariant, but constructed without the top-level ``Settings``.

    Catches a future regression where ``Settings.__init__`` overrides the
    field default but ``QueueSettings()`` standalone instantiation does
    not (or vice versa).
    """
    q = QueueSettings()
    assert q.maxmemory_policy == "noeviction"


def test_load_from_yaml_round_trips_maxmemory_policy_noeviction(tmp_path: Path) -> None:
    """``maxmemory_policy: noeviction`` in YAML must load back as the literal string.

    The field is a plain ``str`` (no enum, no constraint) — this test
    guards against accidentally tightening it to a Literal/StrEnum that
    would reject otherwise-valid Valkey policy strings on parse. It also
    exercises the nested ``QueueSettings`` extraction path in
    ``Settings.load_from_yaml`` (dynaconf uppercases the section name to
    ``QUEUE``).
    """
    yaml = tmp_path / "settings.yaml"
    yaml.write_text(
        """
queue:
  maxmemory_policy: noeviction
""".strip()
        + "\n",
    )

    settings = Settings.load_from_yaml(yaml)

    assert settings.queue.maxmemory_policy == "noeviction"


def test_load_from_yaml_round_trips_operator_override_maxmemory_policy(
    tmp_path: Path,
) -> None:
    """A non-default Valkey policy in YAML must round-trip without rejection.

    Operators may need to flip back to an eviction policy (e.g. for a
    single-tenant environment where loud OOM is unacceptable and lossy
    is preferred). The field must accept any Valkey policy string the
    daemon itself accepts — we do not enforce an allow-list here, both
    because Valkey's policy set evolves with releases and because the
    daemon will refuse an invalid value at startup with a clearer error.
    """
    yaml = tmp_path / "settings.yaml"
    yaml.write_text(
        """
queue:
  maxmemory_policy: allkeys-lru
""".strip()
        + "\n",
    )

    settings = Settings.load_from_yaml(yaml)

    assert settings.queue.maxmemory_policy == "allkeys-lru"
