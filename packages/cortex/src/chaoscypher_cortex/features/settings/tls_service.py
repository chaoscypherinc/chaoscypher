# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TLS certificate management for the all-in-one container."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.app_config import get_settings, reload_settings
from chaoscypher_core.services.tls.service import generate_self_signed_cert
from chaoscypher_core.utils.secure_write import atomic_secret_write


if TYPE_CHECKING:
    from chaoscypher_core.app_config import TLSSettings


logger = structlog.get_logger(__name__)


class TLSService:
    """Manages TLS certificates and Nginx configuration switching.

    Args:
        tls_settings: TLS path configuration from application settings.

    """

    def __init__(self, tls_settings: TLSSettings) -> None:
        """Initialize with TLS path settings.

        Args:
            tls_settings: TLS path configuration from application settings.

        """
        self._cert_dir = Path(tls_settings.cert_dir)
        self._cert_path = self._cert_dir / tls_settings.cert_filename
        self._key_path = self._cert_dir / tls_settings.key_filename
        self._nginx_active_conf = Path(tls_settings.nginx_active_conf)
        self._nginx_http_conf = Path(tls_settings.nginx_http_conf)
        self._nginx_https_conf = Path(tls_settings.nginx_https_conf)

    def is_enabled(self) -> bool:
        """Check if TLS is currently enabled.

        Returns:
            True if both cert and key files exist.

        """
        return self._cert_path.exists() and self._key_path.exists()

    async def enable_self_signed(self, hostname: str | None = None) -> None:
        """Generate self-signed cert and reload Nginx.

        RSA 4096-bit key generation and disk writes are offloaded to a
        thread so the event loop is not stalled on CPU or I/O work.

        Args:
            hostname: Optional additional hostname for SAN.

        """
        await asyncio.to_thread(
            generate_self_signed_cert,
            cert_path=self._cert_path,
            key_path=self._key_path,
            hostname=hostname,
        )
        self._switch_nginx_config(https=True)
        await self._refresh_settings("v1:tls_enabled")
        logger.info("tls_enabled", mode="self-signed", hostname=hostname)

    async def enable_custom(self, cert_pem: bytes, key_pem: bytes) -> None:
        """Save custom cert/key and reload Nginx.

        Disk writes go through asyncio.to_thread so a slow filesystem
        (network mount, encrypted FS) does not stall the event loop on
        the calling request.

        Args:
            cert_pem: PEM-encoded certificate bytes.
            key_pem: PEM-encoded private key bytes.

        """
        await asyncio.to_thread(self._cert_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread(self._cert_path.write_bytes, cert_pem)
        # The unencrypted key goes through the atomic 0600 helper — the old
        # write_bytes + post-hoc chmod left it umask-readable in between.
        await asyncio.to_thread(atomic_secret_write, self._key_path, key_pem, prefix=".server_key_")
        self._switch_nginx_config(https=True)
        await self._refresh_settings("v1:tls_enabled")
        logger.info("tls_enabled", mode="custom")

    async def disable(self) -> None:
        """Remove certs and switch back to HTTP.

        Unlink calls go through asyncio.to_thread for consistency with
        the sister enable_* methods, so a slow filesystem does not stall
        the event loop on the calling request.
        """
        if self._cert_path.exists():
            await asyncio.to_thread(self._cert_path.unlink)
        if self._key_path.exists():
            await asyncio.to_thread(self._key_path.unlink)
        self._switch_nginx_config(https=False)
        await self._refresh_settings("v1:tls_disabled")
        logger.info("tls_disabled")

    async def _refresh_settings(self, reason: str) -> None:
        """Re-resolve settings after a TLS change and notify workers.

        ``cookie_secure`` is auto-detected from the cert files at ``Settings``
        construction, so the cache must be invalidated once the certs are
        written or removed — otherwise session cookies keep the stale
        ``Secure`` flag until restart. Mirrors the reload + publish pattern
        used by the sibling databases and settings services.

        Args:
            reason: A short ``v1:<event>`` tag for the published message.
        """
        reload_settings()
        from chaoscypher_cortex.shared.worker_notify import publish_settings_change

        await publish_settings_change(reason)

    def _switch_nginx_config(self, *, https: bool) -> None:
        """Swap active Nginx config symlink and reload.

        Args:
            https: If True, switch to HTTPS config. Otherwise HTTP.

        """
        source = self._nginx_https_conf if https else self._nginx_http_conf
        if not source.exists():
            logger.warning("nginx_config_missing", path=str(source))
            return

        if self._nginx_active_conf.is_symlink() or self._nginx_active_conf.exists():
            self._nginx_active_conf.unlink()
        self._nginx_active_conf.symlink_to(source)

        timeout_seconds = get_settings().timeouts.tls_validation_seconds
        try:
            subprocess.run(
                ["nginx", "-s", "reload"],  # noqa: S607
                check=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
            logger.info("nginx_reloaded", config="https" if https else "http")
        except subprocess.CalledProcessError, FileNotFoundError:
            logger.warning("nginx_reload_failed", detail="Nginx may not be running")
        except subprocess.TimeoutExpired:
            logger.warning(
                "nginx_reload_timeout",
                detail=f"Nginx reload did not complete in {timeout_seconds}s",
            )
