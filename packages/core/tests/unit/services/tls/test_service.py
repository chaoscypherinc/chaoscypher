# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TLS certificate generation service."""

import sys
from pathlib import Path

import pytest


class TestGenerateSelfSignedCert:
    """Tests for generate_self_signed_cert function."""

    def test_creates_cert_and_key_files(self, tmp_path: Path) -> None:
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        assert cert_path.exists()
        assert key_path.exists()

    def test_cert_is_valid_pem(self, tmp_path: Path) -> None:
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        cert_content = cert_path.read_text()
        assert "BEGIN CERTIFICATE" in cert_content

    def test_key_is_valid_pem(self, tmp_path: Path) -> None:
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        key_content = key_path.read_text()
        assert "BEGIN" in key_content
        assert "PRIVATE KEY" in key_content

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX file-mode semantics do not apply on Windows",
    )
    def test_key_file_created_with_0600(self, tmp_path: Path) -> None:
        """The private key must never be world-readable, even transiently.

        Regression: the key was written via write_bytes (process umask, often
        0644) and chmod'd to 0600 only afterwards, leaving a window where the
        unencrypted RSA key was world-readable. The file must be created with
        owner-only permissions from the start.
        """
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_key_loads_as_private_key(self, tmp_path: Path) -> None:
        """Cross-platform: the written key parses as a valid RSA private key."""
        from cryptography.hazmat.primitives import serialization

        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        assert key.key_size == 4096

    def test_regeneration_replaces_existing_key(self, tmp_path: Path) -> None:
        """Generating over an existing key/cert pair replaces both files."""
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        first_key = key_path.read_bytes()
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        assert key_path.read_bytes() != first_key
        assert "BEGIN CERTIFICATE" in cert_path.read_text()

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        """The atomic-write tempfile is renamed away, not leaked."""
        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path)
        leftovers = [
            p.name for p in tmp_path.iterdir() if p.name not in ("server.crt", "server.key")
        ]
        assert leftovers == []

    def test_custom_hostname_in_san(self, tmp_path: Path) -> None:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID

        from chaoscypher_core.services.tls.service import generate_self_signed_cert

        cert_path = tmp_path / "server.crt"
        key_path = tmp_path / "server.key"
        generate_self_signed_cert(cert_path=cert_path, key_path=key_path, hostname="myserver.local")
        cert_pem = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        dns_names = san.value.get_values_for_type(x509.DNSName)
        assert "myserver.local" in dns_names
        assert "localhost" in dns_names
