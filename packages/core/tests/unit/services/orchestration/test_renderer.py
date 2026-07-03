# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the orchestration template renderer."""

import re
from pathlib import Path

import pytest

from chaoscypher_core.app_config import Settings
from chaoscypher_core.exceptions import ConfigError
from chaoscypher_core.services.orchestration.renderer import (
    list_templates,
    render_all,
    render_template,
)


def test_list_templates_returns_known_set() -> None:
    names = set(list_templates())
    assert {
        "nginx-http.conf",
        "nginx-https.conf",
        "proxy-common.conf",
        "multi-interface-nginx.conf",
        "supervisord.conf",
        "valkey-args.txt",
    }.issubset(names)


def test_render_template_substitutes_settings_values() -> None:
    settings = Settings()
    settings.batching.max_upload_bytes = 999_999_999  # ~1 GB
    settings.timeouts.nginx_proxy_read_timeout = 222

    out = render_template("nginx-http.conf", settings)

    assert "client_max_body_size" in out
    # The proxy_read_timeout directive is in proxy-common.conf, not nginx-http.conf,
    # so we don't assert its presence here. We check it in the proxy-common test.


def test_spa_location_intercepts_upstream_errors_but_api_does_not() -> None:
    """SPA routes show the styled error.html for an upstream 5xx; /api stays JSON.

    ``proxy_intercept_errors`` must be ON only for the HTML SPA ``location /``
    (so an upstream 500 substitutes the ``error_page`` error.html instead of
    leaking the raw upstream body) and OFF for ``location /api/`` — API errors
    must keep returning the JSON envelope the frontend parses, not an HTML page.
    """
    settings = Settings()
    for template_name in ("nginx-http.conf", "nginx-https.conf"):
        out = render_template(template_name, settings)
        # Exactly one intercept directive — it lives in the SPA block, which is
        # rendered after every /api/ location.
        assert out.count("proxy_intercept_errors on;") == 1, template_name
        assert "error_page" in out and "/error.html" in out, template_name


def test_ccx_import_endpoint_gets_upload_body_limit() -> None:
    """The CCX import endpoint must allow large bodies, not the 1m API default.

    An embeddings-included ``.ccx`` is many MB / GB, so without a dedicated cap
    the import inherits ``location /api/``'s 1m server default and nginx 413s the
    upload at the edge before it reaches Cortex. It gets the same configurable
    ``max_upload_bytes`` cap the source-upload routes do.
    """
    settings = Settings()
    settings.batching.max_upload_bytes = 5 * 1024 * 1024 * 1024  # 5 GB -> "5g"
    for template_name in ("nginx-http.conf", "nginx-https.conf"):
        out = render_template(template_name, settings)
        # The server default stays small; only uploads + import lift the cap.
        assert "client_max_body_size 1m;" in out, template_name
        # A dedicated import location carries the upload-size cap.
        idx = out.find("location = /api/v1/exports/import")
        assert idx != -1, template_name
        block = out[idx : idx + 400]
        assert "client_max_body_size 5g;" in block, template_name
        if "location /api/" in out:
            assert out.index("proxy_intercept_errors on;") > out.rindex("location /api/"), (
                f"{template_name}: proxy_intercept_errors must be scoped to the SPA "
                "block, not /api/"
            )


def test_render_all_writes_every_template(tmp_path: Path) -> None:
    settings = Settings()
    written = render_all(settings, tmp_path)
    assert len(written) == len(list_templates())
    for path in written:
        assert path.exists()
        assert path.stat().st_size > 0


def test_render_template_unknown_name_raises() -> None:
    settings = Settings()
    with pytest.raises(ConfigError):
        render_template("does-not-exist.conf", settings)


def test_strict_undefined_catches_template_typos(tmp_path: Path) -> None:
    """A typo'd settings reference in a template raises immediately, not silently empties.

    This guards the most valuable property of the renderer's design: typos
    in templates fail loud (StrictUndefined) rather than producing a config
    file with empty values that fails silently in production.
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, UndefinedError

    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    # Note the typo: "batchin" instead of "batching".
    (template_dir / "typo.j2").write_text("value: {{ settings.batchin.max_upload_bytes }}")

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        autoescape=True,
    )
    settings = Settings()
    with pytest.raises(UndefinedError):
        env.get_template("typo.j2").render(settings=settings)


def test_rate_limit_disabled_skips_limit_req_directives() -> None:
    settings = Settings()
    settings.rate_limit.enabled = False
    out = render_template("nginx-http.conf", settings)
    assert "limit_req_zone" not in out


def test_rate_limit_enabled_emits_zones() -> None:
    settings = Settings()
    settings.rate_limit.enabled = True
    settings.rate_limit.login_max_requests = 7
    settings.rate_limit.api_general_max_requests = 200
    out = render_template("nginx-http.conf", settings)
    assert "rate=7r/s" in out
    assert "rate=200r/s" in out


def test_rate_limit_emits_mutations_zone_with_per_method_key_map() -> None:
    """Mutations zone gates POST/PUT/PATCH/DELETE on /api/* via a method-keyed map.

    GET/HEAD/OPTIONS resolve to the empty default key, which nginx treats as
    a bypass (no rate accounting). The zone key is `$chaoscypher_mutation_key`
    (derived from `$request_method`) rather than `$binary_remote_addr` directly
    so the same IP can read unthrottled while its mutations are capped.
    """
    settings = Settings()
    settings.rate_limit.enabled = True
    settings.rate_limit.mutations_max_requests = 15
    settings.rate_limit.mutations_burst = 25

    for template_name in ("nginx-http.conf", "nginx-https.conf", "multi-interface-nginx.conf"):
        out = render_template(template_name, settings)

        # The per-method key map must be present and route only mutating verbs
        # to the IP key. GET/HEAD/OPTIONS fall through `default ""`.
        assert "map $request_method $chaoscypher_mutation_key" in out, (
            f"{template_name}: mutations key map missing"
        )
        # Tolerate whitespace alignment differences — assert each mutating verb
        # appears on its own line followed by `$binary_remote_addr;`.
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert re.search(rf"^\s+{method}\s+\$binary_remote_addr;\s*$", out, re.MULTILINE), (
                f"{template_name}: mutations map missing {method}"
            )
        # Reads must NOT be mapped to a non-empty key (would defeat the bypass).
        for safe_method in ("GET", "HEAD", "OPTIONS"):
            assert not re.search(
                rf"^\s+{safe_method}\s+\$binary_remote_addr;\s*$", out, re.MULTILINE
            ), f"{template_name}: {safe_method} should not be in mutations map"

        # Zone declaration with the mutation key.
        assert "limit_req_zone $chaoscypher_mutation_key zone=mutations:10m rate=15r/s;" in out, (
            f"{template_name}: mutations limit_req_zone missing or wrong rate"
        )

        # Applied inside the catch-all /api/ location alongside api_general.
        assert "limit_req zone=mutations burst=25 nodelay;" in out, (
            f"{template_name}: mutations limit_req directive missing or wrong burst"
        )


def test_rate_limit_disabled_skips_mutations_zone() -> None:
    """When rate limiting is master-disabled, the mutations zone is omitted too."""
    settings = Settings()
    settings.rate_limit.enabled = False
    for template_name in ("nginx-http.conf", "nginx-https.conf", "multi-interface-nginx.conf"):
        out = render_template(template_name, settings)
        assert "zone=mutations" not in out, f"{template_name}: mutations zone leaked when disabled"
        assert "$chaoscypher_mutation_key" not in out, (
            f"{template_name}: mutations key map leaked when disabled"
        )


def test_render_proxy_common_uses_timeout_settings() -> None:
    settings = Settings()
    settings.timeouts.nginx_proxy_connect_timeout = 11
    settings.timeouts.nginx_proxy_read_timeout = 222
    settings.timeouts.nginx_proxy_send_timeout = 333
    out = render_template("proxy-common.conf", settings)
    assert "proxy_connect_timeout 11s;" in out
    assert "proxy_read_timeout 222s;" in out
    assert "proxy_send_timeout 333s;" in out


def test_render_https_template_uses_tls_cert_paths() -> None:
    settings = Settings()
    settings.tls.cert_dir = "/custom/certs"
    settings.tls.cert_filename = "mycrt.pem"
    settings.tls.key_filename = "mykey.pem"
    out = render_template("nginx-https.conf", settings)
    assert "ssl_certificate /custom/certs/mycrt.pem;" in out
    assert "ssl_certificate_key /custom/certs/mykey.pem;" in out


def test_render_supervisord_uses_shutdown_settings() -> None:
    settings = Settings()
    # Adjust ALL three (cortex/worker/compose) to satisfy the
    # docker_compose_grace >= max(cortex, worker) validator.
    settings.shutdown.cortex_shutdown_grace_seconds = 45
    settings.shutdown.worker_shutdown_grace_seconds = 50
    settings.shutdown.docker_compose_grace_seconds = 60
    settings.shutdown.supervisor_startsecs_cortex = 7
    settings.shutdown.supervisor_startsecs_neuron = 8
    out = render_template("supervisord.conf", settings)
    assert "stopwaitsecs=45" in out
    assert "stopwaitsecs=50" in out
    assert "startsecs=7" in out
    assert "startsecs=8" in out


def test_valkey_args_uses_settings() -> None:
    settings = Settings()
    settings.queue.max_memory = "512mb"
    settings.queue.tcp_keepalive_seconds = 90
    settings.queue.maxmemory_policy = "allkeys-lru"
    out = render_template("valkey-args.txt", settings)
    assert "--maxmemory 512mb" in out
    assert "--tcp-keepalive 90" in out
    assert "--maxmemory-policy allkeys-lru" in out
