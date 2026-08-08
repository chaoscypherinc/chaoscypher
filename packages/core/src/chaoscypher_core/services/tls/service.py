# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-signed TLS certificate generation.

Generates RSA 4096-bit certificates with SAN for localhost and optional hostnames.
"""

from __future__ import annotations

import ipaddress
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _write_private_key(key_path: Path, data: bytes) -> None:
    """Write the private key atomically with owner-only permissions.

    Mirrors ``credentials.py::_atomic_write``: ``mkstemp`` creates the
    tempfile with mode 0600 from the start, so the unencrypted key is never
    world-readable — not even for the window between file creation and a
    later ``chmod`` (the previous ``write_bytes`` + ``chmod`` sequence
    created the file under the process umask, typically 0644). The rename
    is atomic, so a concurrent reader never sees a partial key file.
    """
    fd, tmp_path_str = tempfile.mkstemp(prefix=".server_key_", dir=str(key_path.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if os.name == "posix":
            tmp_path.chmod(0o600)
        tmp_path.replace(key_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def generate_self_signed_cert(
    *,
    cert_path: Path,
    key_path: Path,
    hostname: str | None = None,
    validity_days: int = 365,
) -> None:
    """Generate a self-signed TLS certificate and private key.

    Args:
        cert_path: Path to write the PEM certificate file.
        key_path: Path to write the PEM private key file.
        hostname: Optional additional hostname for SAN.
        validity_days: Certificate validity in days (default 365).

    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "ChaosCypher"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ChaosCypher"),
        ]
    )

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    if hostname:
        san_entries.append(x509.DNSName(hostname))

    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_private_key(
        key_path,
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
