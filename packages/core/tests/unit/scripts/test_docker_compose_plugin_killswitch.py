# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every plugin-loading compose service must declare the user-plugin kill switch.

Regression pin. ``docs/security/plugin-trust.md`` documents

    CHAOSCYPHER_ALLOW_USER_PLUGINS=0 docker compose up -d

as THE way to turn user-plugin discovery off. Compose only injects host
variables a service actually lists, and none of the compose files carries an
``env_file:``, so while the variable went undeclared it never reached the
container: ``user_plugins_allowed()`` read an unset variable, took its ``"1"``
default, and kept importing every ``{data-dir}/plugins/**`` Python file
in-process — with no warning, no log line and no exit code. A security control
that silently does nothing is worse than one that is absent, so the wiring is
pinned here rather than left to a reader of the YAML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parents[5]
_VAR = "CHAOSCYPHER_ALLOW_USER_PLUGINS"

# compose file -> the services that import user plugins in-process.
_PLUGIN_LOADING_SERVICES = {
    "packages/docker/docker-compose.yml": ("chaoscypher",),
    "packages/docker/multi-container/docker-compose.prod.yml": ("cortex", "neuron"),
    "packages/docker/multi-container/docker-compose.dev.yml": ("cortex", "neuron"),
}


def _environment(compose_path: str, service: str) -> list[str]:
    parsed = yaml.safe_load((_REPO_ROOT / compose_path).read_text(encoding="utf-8"))
    env = parsed["services"][service].get("environment") or []
    # Only the list form is used in this repo; a dict would silently pass below.
    assert isinstance(env, list), f"{compose_path}:{service} environment must be a list"
    return [str(entry) for entry in env]


@pytest.mark.parametrize(
    ("compose_path", "service"),
    [(path, svc) for path, svcs in _PLUGIN_LOADING_SERVICES.items() for svc in svcs],
)
def test_compose_service_declares_plugin_kill_switch(compose_path: str, service: str) -> None:
    """The variable is declared, so a host-side value is forwarded."""
    declarations = [e for e in _environment(compose_path, service) if e.startswith(f"{_VAR}=")]

    assert declarations, (
        f"{compose_path}: service '{service}' does not declare {_VAR}, so "
        f"'{_VAR}=0 docker compose up -d' cannot reach it"
    )


@pytest.mark.parametrize(
    ("compose_path", "service"),
    [(path, svc) for path, svcs in _PLUGIN_LOADING_SERVICES.items() for svc in svcs],
)
def test_kill_switch_passes_through_the_host_value(compose_path: str, service: str) -> None:
    """It interpolates the host variable rather than hardcoding a value.

    ``- CHAOSCYPHER_ALLOW_USER_PLUGINS=1`` would declare the variable and still
    ignore the operator, which is the same failure wearing a different hat.
    """
    (declaration,) = [e for e in _environment(compose_path, service) if e.startswith(f"{_VAR}=")]

    assert f"${{{_VAR}" in declaration, (
        f"{compose_path}: service '{service}' hardcodes {_VAR} instead of "
        f"interpolating the host value"
    )
