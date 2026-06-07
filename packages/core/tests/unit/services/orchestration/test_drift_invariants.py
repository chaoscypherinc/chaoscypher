# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Drift invariants — rendered configs must agree with Pydantic settings.

These tests catch the original 2026-05-09 audit drifts (nginx 10g vs 500MB,
proxy 300s vs 30s, supervisor 60s vs 30s) and prevent regressions. If a
template ever reverts to a literal value that should reference settings,
one of these tests will fail with a clear "drift detected" message.
"""

from __future__ import annotations

import re

from chaoscypher_core.app_config import Settings
from chaoscypher_core.services.orchestration import render_template


def _bytes_from_size_token(token: str) -> int:
    """Parse '500m' or '2g' (nginx-style) into bytes.

    Handles 'k', 'm', 'g' suffixes (case-insensitive) and bare integers.
    """
    m = re.fullmatch(r"(\d+)([gmk]?)", token, re.IGNORECASE)
    if not m:
        msg = f"Cannot parse nginx size token: {token!r}"
        raise ValueError(msg)
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit]


def test_no_drift_nginx_upload_size_matches_batching_setting() -> None:
    """Nginx client_max_body_size on upload routes must match BatchSettings.max_upload_bytes."""
    settings = Settings()
    out = render_template("nginx-http.conf", settings)
    sizes = re.findall(r"client_max_body_size\s+(\d+[gmk]?);", out, re.IGNORECASE)
    assert sizes, "no client_max_body_size directive found in rendered nginx-http.conf"
    rendered_max = max(_bytes_from_size_token(s) for s in sizes)
    # Allow 1 MiB rounding (renderer rounds down to 'm' or 'g').
    drift = abs(rendered_max - settings.batching.max_upload_bytes)
    assert drift < 1024**2, (
        f"nginx upload size {rendered_max} drifts from "
        f"BatchSettings.max_upload_bytes {settings.batching.max_upload_bytes} "
        f"(diff {drift} bytes)"
    )


def test_no_drift_nginx_proxy_read_timeout_matches_setting() -> None:
    settings = Settings()
    out = render_template("proxy-common.conf", settings)
    m = re.search(r"proxy_read_timeout\s+(\d+)s;", out)
    assert m is not None, "proxy_read_timeout directive missing"
    assert int(m.group(1)) == settings.timeouts.nginx_proxy_read_timeout


def test_no_drift_nginx_proxy_connect_timeout_matches_setting() -> None:
    settings = Settings()
    out = render_template("proxy-common.conf", settings)
    m = re.search(r"proxy_connect_timeout\s+(\d+)s;", out)
    assert m is not None, "proxy_connect_timeout directive missing"
    assert int(m.group(1)) == settings.timeouts.nginx_proxy_connect_timeout


def test_no_drift_nginx_proxy_send_timeout_matches_setting() -> None:
    settings = Settings()
    out = render_template("proxy-common.conf", settings)
    m = re.search(r"proxy_send_timeout\s+(\d+)s;", out)
    assert m is not None, "proxy_send_timeout directive missing"
    assert int(m.group(1)) == settings.timeouts.nginx_proxy_send_timeout


def test_no_drift_supervisor_cortex_grace_matches_setting() -> None:
    settings = Settings()
    out = render_template("supervisord.conf", settings)
    block = re.search(r"\[program:cortex\][^\[]*", out, re.DOTALL)
    assert block is not None, "[program:cortex] block missing"
    m = re.search(r"stopwaitsecs=(\d+)", block.group(0))
    assert m is not None, "cortex stopwaitsecs missing"
    assert int(m.group(1)) == settings.shutdown.cortex_shutdown_grace_seconds


def test_no_drift_supervisor_neuron_grace_matches_setting() -> None:
    settings = Settings()
    out = render_template("supervisord.conf", settings)
    block = re.search(r"\[program:neuron\][^\[]*", out, re.DOTALL)
    assert block is not None, "[program:neuron] block missing"
    m = re.search(r"stopwaitsecs=(\d+)", block.group(0))
    assert m is not None, "neuron stopwaitsecs missing"
    assert int(m.group(1)) == settings.shutdown.worker_shutdown_grace_seconds


def test_no_drift_valkey_maxmemory_matches_setting() -> None:
    settings = Settings()
    out = render_template("valkey-args.txt", settings)
    m = re.search(r"--maxmemory\s+(\S+)", out)
    assert m is not None, "--maxmemory missing in valkey-args.txt"
    assert m.group(1) == settings.queue.max_memory


def test_no_drift_valkey_keepalive_matches_setting() -> None:
    settings = Settings()
    out = render_template("valkey-args.txt", settings)
    m = re.search(r"--tcp-keepalive\s+(\d+)", out)
    assert m is not None, "--tcp-keepalive missing in valkey-args.txt"
    assert int(m.group(1)) == settings.queue.tcp_keepalive_seconds


def test_no_drift_valkey_maxmemory_policy_matches_setting() -> None:
    settings = Settings()
    out = render_template("valkey-args.txt", settings)
    m = re.search(r"--maxmemory-policy\s+(\S+)", out)
    assert m is not None, "--maxmemory-policy missing in valkey-args.txt"
    assert m.group(1) == settings.queue.maxmemory_policy


def test_no_drift_nginx_https_cert_paths_match_tls_settings() -> None:
    """SSL cert paths in nginx-https.conf must derive from TLSSettings."""
    settings = Settings()
    out = render_template("nginx-https.conf", settings)
    expected_cert = f"{settings.tls.cert_dir}/{settings.tls.cert_filename}"
    expected_key = f"{settings.tls.cert_dir}/{settings.tls.key_filename}"
    assert f"ssl_certificate {expected_cert};" in out, (
        f"ssl_certificate path in nginx-https.conf does not match TLSSettings "
        f"(expected {expected_cert})"
    )
    assert f"ssl_certificate_key {expected_key};" in out, (
        f"ssl_certificate_key path in nginx-https.conf does not match TLSSettings "
        f"(expected {expected_key})"
    )
