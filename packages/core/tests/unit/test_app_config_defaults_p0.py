# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""P0 settings-default regression tests.

Covers two items closed in the same commit:

1. ``cookie_secure`` auto-toggle — resolves from TLS cert file presence at
   boot; explicit operator override (settings.yaml / env) always wins.

2. ``strict_schema_drift`` default flip — True so self-hosted operators see
   a clear startup error instead of silent runtime corruption.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.app_config import LocalAuthSettings, Settings, TLSSettings


# ============================================================================
# cookie_secure auto-toggle
# ============================================================================


class TestCookieSecureAutoToggle:
    """``local_auth.cookie_secure`` resolves from TLS cert files at boot."""

    def test_no_tls_files_resolves_false(self, tmp_path: Path) -> None:
        """No cert files in cert_dir → cookie_secure=False (plain-HTTP safe)."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        # No cert or key files written — cert_dir exists but is empty.

        s = Settings(
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            )
        )
        assert s.local_auth.cookie_secure is False

    def test_tls_files_present_resolves_true(self, tmp_path: Path) -> None:
        """Both cert and key present → cookie_secure=True (HTTPS deployment)."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        (cert_dir / "server.crt").write_text("cert", encoding="utf-8")
        (cert_dir / "server.key").write_text("key", encoding="utf-8")

        s = Settings(
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            )
        )
        assert s.local_auth.cookie_secure is True

    def test_only_cert_no_key_resolves_false(self, tmp_path: Path) -> None:
        """Cert present but key absent → cookie_secure=False (incomplete TLS)."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        (cert_dir / "server.crt").write_text("cert", encoding="utf-8")
        # server.key intentionally absent.

        s = Settings(
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            )
        )
        assert s.local_auth.cookie_secure is False

    def test_explicit_true_wins_over_missing_tls(self, tmp_path: Path) -> None:
        """Explicit cookie_secure=True overrides missing TLS files."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        # No TLS files — auto-detection would return False.

        s = Settings(
            local_auth=LocalAuthSettings(cookie_secure=True),
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            ),
        )
        assert s.local_auth.cookie_secure is True

    def test_explicit_false_wins_over_present_tls(self, tmp_path: Path) -> None:
        """Explicit cookie_secure=False overrides present TLS files.

        Covers the 'TLS-terminating reverse proxy' scenario where certs exist
        on disk but the operator wants to disable the Secure flag for some
        reason (e.g. split-domain routing, debugging).
        """
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        (cert_dir / "server.crt").write_text("cert", encoding="utf-8")
        (cert_dir / "server.key").write_text("key", encoding="utf-8")

        s = Settings(
            local_auth=LocalAuthSettings(cookie_secure=False),
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            ),
        )
        assert s.local_auth.cookie_secure is False

    def test_explicit_override_via_yaml(self, tmp_path: Path) -> None:
        """cookie_secure in settings.yaml wins regardless of TLS file state."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()
        # No TLS files — auto-detect would say False.

        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "LOCAL_AUTH:\n  cookie_secure: true\n",
            encoding="utf-8",
        )

        s = Settings.load_from_yaml(settings_file)
        # Override from YAML must win even with no certs on disk.
        assert s.local_auth.cookie_secure is True

    def test_cookie_secure_is_bool_not_none_after_construction(self, tmp_path: Path) -> None:
        """The resolved value is always a bool — never the sentinel None."""
        cert_dir = tmp_path / "tls"
        cert_dir.mkdir()

        s = Settings(
            tls=TLSSettings(
                cert_dir=str(cert_dir),
                cert_filename="server.crt",
                key_filename="server.key",
            )
        )
        assert isinstance(s.local_auth.cookie_secure, bool)


# ============================================================================
# strict_schema_drift default
# ============================================================================


class TestStrictSchemaDriftDefault:
    """``database.strict_schema_drift`` defaults to True after the flip."""

    def test_default_is_true(self) -> None:
        """Default must be True so operators see a boot error on drift."""
        from chaoscypher_core.app_config import DatabaseSettings

        assert DatabaseSettings().strict_schema_drift is True

    def test_settings_default_is_true(self) -> None:
        """Settings() exposes strict_schema_drift=True at the top level."""
        s = Settings()
        assert s.database.strict_schema_drift is True

    def test_explicit_false_overrides_default(self) -> None:
        """Operators can opt into lenient drift handling with an explicit False."""
        from chaoscypher_core.app_config import DatabaseSettings

        ds = DatabaseSettings(strict_schema_drift=False)
        assert ds.strict_schema_drift is False

    def test_explicit_false_via_settings(self) -> None:
        """Lenient drift can also be set on the top-level Settings model."""
        from chaoscypher_core.app_config import DatabaseSettings

        s = Settings(database=DatabaseSettings(strict_schema_drift=False))
        assert s.database.strict_schema_drift is False

    def test_explicit_false_via_yaml(self, tmp_path: Path) -> None:
        """DATABASE.strict_schema_drift: false in settings.yaml opts into lenient mode."""
        settings_file = tmp_path / "settings.yaml"
        settings_file.write_text(
            "DATABASE:\n  strict_schema_drift: false\n",
            encoding="utf-8",
        )
        s = Settings.load_from_yaml(settings_file)
        assert s.database.strict_schema_drift is False
