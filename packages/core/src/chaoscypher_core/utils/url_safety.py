# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""URL safety validation for SSRF protection.

Blocks cloud metadata endpoints and dangerous URL schemes. Chaos Cypher
is a local-first application that needs to reach local LLM services
(Ollama), Docker containers, and LAN resources, so private / loopback
IPs remain allowed in the default (permissive) policy. The strict
policy — used by URL-import endpoints that accept arbitrary user-supplied
URLs — additionally blocks anything that resolves *at check time* to a
private range, rejecting a public hostname that points at
``169.254.169.254`` / ``127.0.0.1``.

DNS rebinding caveat: ``validate_url_safety`` resolves the host to vet it,
but if the caller then hands the *hostname* to an HTTP client the client
re-resolves it at connect time — an attacker controlling DNS can flip the
answer between check and connect. ``validate_url_safety`` alone does NOT
close that window. Callers that need it closed must dial the vetted address
directly: use :func:`resolve_pinned_ip` to obtain a validated IP, connect to
that IP, and carry the original Host header + TLS SNI (see the HTTP request
workflow plugin for the reference implementation).

What IS blocked (default policy):
- Cloud metadata IPs (169.254.0.0/16 link-local)
- Cloud metadata hostnames (AWS/GCP/Azure/Oracle/Alibaba IBM DigitalOcean)
- Non-HTTP schemes (file://, ftp://, gopher://, data:, javascript:)

What the strict policy additionally blocks (``strict=True``):
- Any hostname that resolves to private/loopback/reserved/link-local/
  multicast/unspecified IPs (DNS rebinding defense, at check time)
"""

import ipaddress
import socket
from urllib.parse import urlparse

import structlog


logger = structlog.get_logger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}

_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.azure.internal",
    "metadata.oraclecloud.com",
    "metadata.digitalocean.com",
    "169.254.169.254",  # AWS/GCP metadata
    "100.100.100.200",  # Alibaba Cloud metadata
    "192.0.0.192",  # Oracle Cloud metadata
    "fd00:ec2::254",  # AWS IPv6 metadata
}

# Additional CIDR ranges that are always blocked regardless of policy.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (covers cloud metadata)
    ipaddress.ip_network("161.26.0.0/16"),  # IBM Cloud services
)


def _ip_is_unsafe_public_or_metadata(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if the address must be blocked in every policy.

    Covers cloud-metadata IPs (link-local ``169.254.0.0/16`` plus the
    vendor-specific ranges enumerated in ``_BLOCKED_NETWORKS``).
    """
    if ip.is_link_local:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def _ip_is_unsafe_strict(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if an IP is unsafe under the strict policy.

    Strict policy blocks loopback, private, reserved, multicast, and
    unspecified addresses — the full set of non-public destinations.
    """
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_link_local
        or any(ip in net for net in _BLOCKED_NETWORKS)
    )


def _resolve_all(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve a hostname to all of its A/AAAA addresses.

    Returns an empty list on resolution failure; callers should treat
    an empty list as "unable to validate" and reject strictly.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (OSError, UnicodeError):  # fmt: skip
        return []
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            ips.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):  # fmt: skip
            continue
    return ips


def validate_url_safety(url: str, *, strict: bool = False) -> bool:  # noqa: PLR0911
    """Check whether a URL is safe to fetch.

    Args:
        url: The URL to validate.
        strict: When True, block anything that resolves to a
            private/loopback/reserved IP (DNS rebinding defense). Use
            for endpoints that accept arbitrary user-supplied URLs.
            When False (default), allow private IPs so local services
            like Ollama remain reachable.

    Returns:
        True if safe to fetch, False if blocked.

    """
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Block non-HTTP schemes (file://, ftp://, gopher://, data:, javascript:)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        logger.debug("url_blocked_scheme", url=url, scheme=parsed.scheme)
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    hostname_lower = hostname.lower()

    # Block known cloud metadata hostnames
    if hostname_lower in _BLOCKED_HOSTS:
        logger.warning("url_blocked_cloud_metadata", url=url, hostname=hostname)
        return False

    # If the host is already a literal IP, check it directly.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _ip_is_unsafe_public_or_metadata(literal_ip):
            logger.warning("url_blocked_metadata_ip", url=url, ip=str(literal_ip))
            return False
        if strict and _ip_is_unsafe_strict(literal_ip):
            logger.warning("url_blocked_strict_ip", url=url, ip=str(literal_ip))
            return False
        return True

    # Hostname — always resolve + check every address against the
    # always-blocked list. This closes DNS rebinding for the metadata
    # IPs regardless of policy.
    resolved = _resolve_all(hostname_lower)
    if not resolved:
        # Unresolvable hostnames are rejected under strict policy; the
        # default policy tolerates them (e.g. an internal name that
        # only resolves at fetch time via custom DNS).
        if strict:
            logger.warning("url_blocked_unresolvable_strict", url=url, hostname=hostname)
            return False
        return True

    for ip in resolved:
        if _ip_is_unsafe_public_or_metadata(ip):
            logger.warning(
                "url_blocked_metadata_ip_resolved",
                url=url,
                hostname=hostname,
                ip=str(ip),
            )
            return False
        if strict and _ip_is_unsafe_strict(ip):
            logger.warning(
                "url_blocked_strict_ip_resolved",
                url=url,
                hostname=hostname,
                ip=str(ip),
            )
            return False

    return True


def resolve_pinned_ip(url: str, *, strict: bool = False) -> str | None:  # noqa: PLR0911
    """Resolve *url*'s host once and return a single validated IP to dial.

    ``validate_url_safety`` answers "is this safe?" but leaves the caller to
    hand the *hostname* to the HTTP client, which re-resolves it at connect
    time — a DNS-rebinding window where the vetted address and the dialed
    address differ. This helper instead returns the concrete IP to connect
    to. By dialing that exact IP (and carrying the original Host header + TLS
    SNI), the address that was checked is the address that is used.

    The per-IP policy is identical to ``validate_url_safety``: cloud-metadata
    targets are blocked always; ``strict`` additionally blocks
    private/loopback/reserved. The one deliberate difference is that an
    *unresolvable* host returns ``None`` even under the permissive policy —
    there is no concrete address to pin, so it cannot be dialed safely.

    Args:
        url: The URL whose host should be resolved and validated.
        strict: When True, also reject private/loopback/reserved targets.

    Returns:
        A validated IP literal (IPv4 or IPv6, unbracketed) to pin the
        connection to, or ``None`` when the URL is unsafe or unresolvable.
        Callers MUST treat ``None`` as "blocked".

    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in _ALLOWED_SCHEMES:
        logger.debug("url_blocked_scheme", url=url, scheme=parsed.scheme)
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    hostname_lower = hostname.lower()
    if hostname_lower in _BLOCKED_HOSTS:
        logger.warning("url_blocked_cloud_metadata", url=url, hostname=hostname)
        return None

    # Literal IP host — nothing to resolve; validate and pin it directly.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _ip_is_unsafe_public_or_metadata(literal_ip):
            logger.warning("url_blocked_metadata_ip", url=url, ip=str(literal_ip))
            return None
        if strict and _ip_is_unsafe_strict(literal_ip):
            logger.warning("url_blocked_strict_ip", url=url, ip=str(literal_ip))
            return None
        return str(literal_ip)

    # Hostname — resolve once and require every returned address to be safe
    # (mirrors validate_url_safety's conservative "all must pass" semantics),
    # then pin to the first. An unresolvable host cannot be pinned.
    resolved = _resolve_all(hostname_lower)
    if not resolved:
        logger.warning("url_blocked_unresolvable_pin", url=url, hostname=hostname)
        return None

    for ip in resolved:
        if _ip_is_unsafe_public_or_metadata(ip):
            logger.warning(
                "url_blocked_metadata_ip_resolved", url=url, hostname=hostname, ip=str(ip)
            )
            return None
        if strict and _ip_is_unsafe_strict(ip):
            logger.warning("url_blocked_strict_ip_resolved", url=url, hostname=hostname, ip=str(ip))
            return None

    return str(resolved[0])
