# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP Request Plugin - Make HTTP requests with security validation.

Makes HTTP/HTTPS requests with SSRF protection and timeout handling.

Extracted from executors/http_executor.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.utils.url_safety import resolve_pinned_ip


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class RequestPlugin:
    """HTTP Request tool plugin.

    Make HTTP requests with security validation (SSRF protection).
    Supports all HTTP methods, auth, headers, and request bodies.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "http.request"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "http"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "Http"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "HTTP Request"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Make HTTP request with security validation and timeout"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Request URL (http/https)",
                },
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                    "description": "HTTP method",
                    "default": "GET",
                },
                "headers": {
                    "type": "object",
                    "description": "Request headers",
                    "additionalProperties": {"type": "string"},
                },
                "body": {"description": "Request body (object or string)"},
                "auth": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["bearer", "basic"]},
                        "credentials": {
                            "type": "string",
                            "description": "Token for bearer, or 'username:password' for basic",
                        },
                    },
                    "required": ["type", "credentials"],
                },
                "timeout": {
                    "type": "number",
                    "minimum": 1,
                    "maximum": 300,
                    "default": 60,
                    "description": "Request timeout in seconds (1-300, default 60)",
                },
            },
            "required": ["url"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for HTTP Request tool."""
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "integer",
                    "description": "HTTP status code (0 for connection errors)",
                },
                "headers": {
                    "type": "object",
                    "description": "Response headers",
                },
                "body": {
                    "description": "Response body (JSON object or string)",
                },
                "success": {
                    "type": "boolean",
                    "description": "Whether the request was successful (2xx status)",
                },
            },
            "required": ["status", "headers", "body", "success"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Execute HTTP request.

        Args:
            inputs: Tool inputs (url, method, headers, body, auth, timeout)
            context: Execution context

        Returns:
            Dictionary with status, headers, body, success

        """
        url = inputs["url"]

        # SECURITY: Resolve + validate the host ONCE and pin the connection to
        # that exact IP. Strict policy — the tool returns the full response
        # body to the workflow caller, so loopback/private-IP exfil (Ollama,
        # Valkey, neighbour containers) must be blocked in addition to cloud
        # metadata. Pinning the dialed IP (rather than re-resolving the
        # hostname at connect time) closes the DNS-rebinding window; see
        # resolve_pinned_ip.
        pinned_ip = resolve_pinned_ip(url, strict=True)
        if pinned_ip is None:
            logger.warning("ssrf_attempt_blocked", url=url)
            return {
                "status": 0,
                "headers": {},
                "body": "Security error: URL is not allowed (blocked scheme or restricted endpoint)",
                "success": False,
            }

        method = inputs.get("method", "GET")
        headers = dict(inputs.get("headers", {}))
        body = inputs.get("body")
        auth = inputs.get("auth")

        # Pin the connection to the validated IP while preserving the original
        # authority for routing (Host header) and, for TLS, the hostname for
        # SNI + certificate verification (httpcore honours the ``sni_hostname``
        # request extension; the TCP connect targets the URL host = the IP).
        original = httpx.URL(url)
        pinned_url = original.copy_with(host=pinned_ip)
        # Drop any caller-supplied Host header (case-insensitive) so ours wins.
        headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        headers["Host"] = original.netloc.decode("ascii")

        # Get timeout: use input value, fall back to settings, then 60s default
        _hard_cap = 300
        raw_timeout = inputs.get("timeout")
        if raw_timeout is None:
            # Fall back to settings-based default
            default_timeout = get_settings().web.workflow_http_default_timeout_seconds
            if context.llm_service and hasattr(context.llm_service, "settings"):
                settings = context.llm_service.settings
                if hasattr(settings, "timeouts") and hasattr(settings.timeouts, "http_request"):
                    default_timeout = float(settings.timeouts.http_request)
            effective_timeout = default_timeout
        else:
            effective_timeout = float(raw_timeout)
        # Clamp to [1, 300]
        effective_timeout = max(1.0, min(effective_timeout, float(_hard_cap)))

        # Prepare request kwargs
        request_kwargs: dict[str, Any] = {
            "method": method,
            "url": pinned_url,
            "headers": headers,
        }
        # Pin TLS SNI + cert verification to the real hostname (we dial the IP).
        if original.scheme == "https":
            request_kwargs["extensions"] = {"sni_hostname": original.host}

        # Add authentication
        if auth:
            auth_type = auth.get("type")
            credentials = auth.get("credentials")

            if auth_type == "bearer":
                request_kwargs["headers"]["Authorization"] = f"Bearer {credentials}"
            elif auth_type == "basic":
                if ":" not in credentials:
                    return {
                        "status": 0,
                        "headers": {},
                        "body": "Invalid credentials format, expected 'username:password'",
                        "success": False,
                    }
                username, password = credentials.split(":", 1)
                request_kwargs["auth"] = httpx.BasicAuth(username, password)

        # Add body
        if body:
            if isinstance(body, dict):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = body

        # Make async request. follow_redirects stays off: a 3xx Location would
        # bypass the pinned IP (and re-introduce SSRF via a redirected host).
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(effective_timeout),
                follow_redirects=False,
            ) as client:
                response = await client.request(**request_kwargs)

                # Try to parse JSON response
                try:
                    response_body = response.json()
                except ValueError:
                    response_body = response.text

                return {
                    "status": response.status_code,
                    "headers": dict(response.headers),
                    "body": response_body,
                    "success": response.is_success,
                }

        except httpx.RequestError as e:
            logger.exception(
                "http_request_failed",
                error_type=type(e).__name__,
            )
            return {"status": 0, "headers": {}, "body": str(e), "success": False}


__all__ = ["RequestPlugin"]
