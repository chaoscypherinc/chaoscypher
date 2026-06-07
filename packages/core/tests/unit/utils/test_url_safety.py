# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for URL safety validation."""

from chaoscypher_core.utils.url_safety import resolve_pinned_ip, validate_url_safety


class TestValidateUrlSafety:
    """Tests for validate_url_safety()."""

    # --- Allowed URLs (local-first app) ---

    def test_allows_localhost_http(self) -> None:
        """Localhost is allowed for local LLM services."""
        assert validate_url_safety("http://localhost:11434") is True

    def test_allows_localhost_https(self) -> None:
        """HTTPS localhost is allowed."""
        assert validate_url_safety("https://localhost:8080") is True

    def test_allows_127_0_0_1(self) -> None:
        """Loopback IP is allowed for local services."""
        assert validate_url_safety("http://127.0.0.1:11434") is True

    def test_allows_private_ip_192(self) -> None:
        """Private IPs are allowed (Docker, LAN services)."""
        assert validate_url_safety("http://192.168.1.100:8080") is True

    def test_allows_private_ip_10(self) -> None:
        """10.x.x.x range is allowed."""
        assert validate_url_safety("http://10.0.0.5:11434") is True

    def test_allows_private_ip_172(self) -> None:
        """172.16.x.x range is allowed."""
        assert validate_url_safety("http://172.16.0.1:8080") is True

    def test_allows_public_url(self) -> None:
        """Normal public URLs are allowed."""
        assert validate_url_safety("https://example.com/page") is True

    def test_allows_docker_hostname(self) -> None:
        """Docker container hostnames are allowed."""
        assert validate_url_safety("http://ollama:11434") is True

    # --- Blocked: Cloud metadata ---

    def test_blocks_aws_metadata(self) -> None:
        """AWS EC2 metadata endpoint must be blocked."""
        assert validate_url_safety("http://169.254.169.254/latest/meta-data/") is False

    def test_blocks_aws_metadata_with_path(self) -> None:
        """AWS metadata with IAM credentials path."""
        assert validate_url_safety("http://169.254.169.254/latest/meta-data/iam/") is False

    def test_blocks_azure_metadata(self) -> None:
        """Azure instance metadata endpoint."""
        assert validate_url_safety("http://169.254.169.254/metadata/instance") is False

    def test_blocks_link_local_range(self) -> None:
        """Any 169.254.x.x link-local address is blocked."""
        assert validate_url_safety("http://169.254.1.1/something") is False

    # --- Blocked: Dangerous schemes ---

    def test_blocks_file_scheme(self) -> None:
        """file:// scheme can read local filesystem."""
        assert validate_url_safety("file:///etc/passwd") is False

    def test_blocks_ftp_scheme(self) -> None:
        """ftp:// scheme is not needed."""
        assert validate_url_safety("ftp://evil.com/file") is False

    def test_blocks_gopher_scheme(self) -> None:
        """gopher:// is a classic SSRF vector."""
        assert validate_url_safety("gopher://evil.com:25/") is False

    def test_blocks_data_scheme(self) -> None:
        """data: URIs should not be fetched."""
        assert validate_url_safety("data:text/html,<script>alert(1)</script>") is False

    def test_blocks_javascript_scheme(self) -> None:
        """javascript: scheme must be blocked."""
        assert validate_url_safety("javascript:alert(1)") is False

    # --- Edge cases ---

    def test_blocks_empty_string(self) -> None:
        """Empty URL is invalid."""
        assert validate_url_safety("") is False

    def test_blocks_no_scheme(self) -> None:
        """URL without scheme is invalid."""
        assert validate_url_safety("example.com/page") is False

    def test_blocks_metadata_ip_https(self) -> None:
        """Metadata IP blocked even with https."""
        assert validate_url_safety("https://169.254.169.254/") is False

    def test_allows_http_scheme(self) -> None:
        """http:// is a valid scheme."""
        assert validate_url_safety("http://example.com") is True

    def test_allows_https_scheme(self) -> None:
        """https:// is a valid scheme."""
        assert validate_url_safety("https://example.com") is True


class TestResolvePinnedIp:
    """Tests for resolve_pinned_ip() — resolve-once-and-pin SSRF helper.

    Unlike ``validate_url_safety`` (a bool check), this returns the concrete
    IP the caller should dial so the vetted address is the connected address.
    """

    def test_literal_public_ip_returned(self) -> None:
        """A safe literal public IP is returned unchanged (nothing to resolve)."""
        assert resolve_pinned_ip("http://93.184.216.34/page", strict=True) == "93.184.216.34"

    def test_literal_metadata_ip_blocked(self) -> None:
        """The cloud-metadata IP is refused in every policy."""
        assert resolve_pinned_ip("http://169.254.169.254/latest/", strict=True) is None
        assert resolve_pinned_ip("http://169.254.169.254/latest/", strict=False) is None

    def test_literal_loopback_blocked_strict(self) -> None:
        """Loopback is refused under the strict policy."""
        assert resolve_pinned_ip("http://127.0.0.1:8000/", strict=True) is None

    def test_literal_loopback_allowed_non_strict(self) -> None:
        """Loopback is pinnable under the permissive (default) policy."""
        assert resolve_pinned_ip("http://127.0.0.1:8000/", strict=False) == "127.0.0.1"

    def test_file_scheme_blocked(self) -> None:
        """Non-HTTP schemes are refused."""
        assert resolve_pinned_ip("file:///etc/passwd", strict=True) is None

    def test_empty_url_blocked(self) -> None:
        """Empty URL cannot be pinned."""
        assert resolve_pinned_ip("", strict=True) is None

    def test_localhost_hostname_blocked_strict(self) -> None:
        """A hostname resolving to loopback must not be pinnable under strict."""
        assert resolve_pinned_ip("http://localhost:11434/", strict=True) is None

    def test_localhost_hostname_pins_to_loopback_non_strict(self) -> None:
        """Under the permissive policy localhost pins to its loopback address."""
        pinned = resolve_pinned_ip("http://localhost:11434/", strict=False)
        assert pinned in {"127.0.0.1", "::1"}

    def test_unresolvable_hostname_blocked(self) -> None:
        """An unresolvable host cannot be pinned, even under the permissive policy.

        This differs from ``validate_url_safety`` (which tolerates unresolvable
        hosts in the default policy) precisely because pinning requires a
        concrete address to dial.
        """
        assert resolve_pinned_ip("http://nonexistent.invalid./", strict=False) is None
