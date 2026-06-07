# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Branded HTML error pages + content negotiation between HTML and JSON.

Used by middleware-level errors (host-header, body-size, rate-limit) that
fire BEFORE FastAPI route dispatch — the SPA's error boundary can't catch
them, so we need to serve a self-contained branded page when a browser is
the client. API clients still get the canonical JSON envelope.

Design notes:
    * Hand-rolled string interpolation (no Jinja) — keeps the middleware
      path free of template-engine startup cost and an extra dependency.
    * Every interpolated value passes through ``html.escape`` — the Host
      header is attacker-controlled by definition.
    * Colors mirror ``theme/palette.ts`` so the page reads as Chaos Cypher.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.requests import Request
    from starlette.responses import Response


# Mirrored from packages/interface/src/theme/palette.ts (ChaosCypherPalette).
# Changes to the palette should be reflected here.
_BG = "#0E1116"
_PANEL = "#161B22"
_FG = "#E6EDF3"
_MUTED = "#8B949E"
_ACCENT = "#7C5CFF"
_BORDER = "#30363D"


def render_branded_error(
    *,
    status_code: int,
    title: str,
    lead: str,
    details: Sequence[tuple[str, str]] = (),
    why: str | None = None,
    fix: Sequence[str] = (),
    http_label: str | None = None,
) -> str:
    """Render a branded HTML error page.

    Args:
        status_code: HTTP status (used only in the default ``http_label``).
        title: Page heading.
        lead: One-sentence summary directly under the heading.
        details: ``(label, value)`` rows shown in a key/value table.
        why: Optional paragraph under a "Why am I seeing this?" subheading.
        fix: Ordered list of remediation steps under "Fix it".
        http_label: Footer label (e.g. "HTTP 421 Misdirected Request"). If
            omitted, falls back to "HTTP {status_code}".

    Returns:
        A complete ``<!doctype html>`` document as a string.
    """
    label = http_label if http_label is not None else f"HTTP {status_code}"

    details_html = ""
    if details:
        rows = "".join(
            f'<tr><th scope="row">{html.escape(k)}</th><td>{html.escape(v)}</td></tr>'
            for k, v in details
        )
        details_html = f'<table class="details">{rows}</table>'

    why_html = ""
    if why:
        why_html = f"<h2>Why am I seeing this?</h2><p>{html.escape(why)}</p>"

    fix_html = ""
    if fix:
        items = "".join(f"<li>{html.escape(step)}</li>" for step in fix)
        fix_html = f"<h2>Fix it</h2><ol>{items}</ol>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Chaos Cypher</title>
<style>
  :root {{
    color-scheme: dark;
  }}
  html, body {{
    margin: 0;
    padding: 0;
    background: {_BG};
    color: {_FG};
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
    line-height: 1.55;
  }}
  .wrap {{
    max-width: 680px;
    margin: 64px auto;
    padding: 0 20px;
  }}
  .panel {{
    background: {_PANEL};
    border: 1px solid {_BORDER};
    border-radius: 12px;
    padding: 32px;
  }}
  .brand {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 24px;
  }}
  .brand img {{
    width: 32px;
    height: 32px;
  }}
  .brand span {{
    font-size: 14px;
    color: {_MUTED};
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  h1 {{
    margin: 0 0 8px;
    font-size: 24px;
    font-weight: 600;
  }}
  .lead {{
    margin: 0 0 24px;
    color: {_FG};
    font-size: 16px;
  }}
  h2 {{
    margin: 24px 0 8px;
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {_ACCENT};
    font-weight: 600;
  }}
  table.details {{
    width: 100%;
    border-collapse: collapse;
    margin: 8px 0 8px;
  }}
  table.details th, table.details td {{
    text-align: left;
    padding: 8px 0;
    border-bottom: 1px solid {_BORDER};
    font-size: 14px;
    font-weight: 400;
    vertical-align: top;
  }}
  table.details th {{
    color: {_MUTED};
    width: 30%;
    padding-right: 16px;
  }}
  ol {{
    margin: 8px 0 0;
    padding-left: 20px;
  }}
  ol li {{
    margin-bottom: 6px;
  }}
  .footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid {_BORDER};
    font-size: 12px;
    color: {_MUTED};
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel">
    <div class="brand">
      <img src="/logo.png" alt="">
      <span>Chaos Cypher</span>
    </div>
    <h1>{html.escape(title)}</h1>
    <p class="lead">{html.escape(lead)}</p>
    {details_html}
    {why_html}
    {fix_html}
    <div class="footer">{html.escape(label)}</div>
  </div>
</div>
</body>
</html>
"""


def negotiated_error_response(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    json_payload: dict[str, Any],
    html_kwargs: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Response:
    """Return HTML or JSON depending on the request's ``Accept`` header.

    HTML is served only when ``text/html`` appears in ``Accept``. Anything
    else (including a missing header, ``*/*`` from curl, or
    ``application/json`` from the SPA fetch client) gets the JSON envelope.

    Args:
        request: The incoming Starlette request.
        status_code: HTTP status to return on either branch.
        error_code: Stable error identifier (reserved for telemetry).
        json_payload: Body for the JSON branch (typically a
            ``UnifiedErrorResponse``-shaped dict).
        html_kwargs: Kwargs forwarded to ``render_branded_error``. ``status_code``
            is injected automatically; callers should NOT include it.
        headers: Optional extra response headers (e.g. ``Retry-After``).
    """
    from starlette.responses import HTMLResponse, JSONResponse

    accept = request.headers.get("accept", "").lower()
    if "text/html" in accept:
        return HTMLResponse(
            render_branded_error(status_code=status_code, **html_kwargs),
            status_code=status_code,
            headers=headers,
        )
    return JSONResponse(
        content=json_payload,
        status_code=status_code,
        headers=headers,
    )
