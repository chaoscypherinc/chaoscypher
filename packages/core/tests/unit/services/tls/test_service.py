# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TLS certificate generation service."""

from pathlib import Path


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
