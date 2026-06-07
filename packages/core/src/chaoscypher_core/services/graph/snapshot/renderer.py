# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pillow-based PNG renderer for GraphBreakdown snapshots.

Ported from the original snapshot mockup script. Produces a 1080x1080 PNG
with nebula background, phyllotactic dot clusters, and chrome.

Note: ship Inter-Light.ttf + JetBrainsMono-Regular.ttf (both OFL) to resources/.
Falls back to ImageFont.load_default() when absent.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from chaoscypher_core.services.graph.snapshot.geometry import (
    disc_positions,
    disc_radius,
    dot_variance,
    pack_circles,
)
from chaoscypher_core.services.graph.snapshot.layout import (
    compute_source_positions,
    select_layout,
)
from chaoscypher_core.services.graph.snapshot.resources import RESOURCES_DIR


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.services.graph.snapshot.models import GraphBreakdown, SourceBreakdown


__all__ = ["SnapshotRenderer"]

# ---------------------------------------------------------------------------
# Palette — matches the original mockup script's PALETTE + GID
# ---------------------------------------------------------------------------

_PALETTE: tuple[str, ...] = (
    "#00e5ff",
    "#ffaa55",
    "#d85fa5",
    "#9573e0",
    "#1de9b6",
    "#ff5f8f",
    "#5c9eff",
    "#9cff57",
    "#efebe0",
)

# Nebula atmospheric colours (hex, inner_opacity, edge_opacity)
_NEBULA_COLOURS: tuple[tuple[str, float, float], ...] = (
    ("#c24470", 0.22, 0.10),
    ("#5c4b9a", 0.20, 0.08),
    ("#b86a3a", 0.14, 0.05),
    ("#1b8370", 0.14, 0.06),
    ("#ff5f8f", 0.11, 0.04),
    ("#2a5fb0", 0.16, 0.07),
)

# Canvas dimensions
_SIZE = 1080
_BG_COLOUR = (4, 7, 20)
_CENTRE = (_SIZE // 2, int(_SIZE * 0.417))  # (540, 450) — same as mockup

# Chrome measurements (pixels / pt)
_RULE_Y = 850
_TITLE_Y = 905
_STATS_Y_LABEL = 960
_STATS_Y_VALUE = 976
_TITLE_MAX_CHARS = 28
_TITLE_FONT_SIZE = 46
_STATS_FONT_SIZE = 12
_LOGO_SIZE = 24

# Default spacing constants (from mockup)
_GALAXY_SPACING = 3.2
_SINGLE_BODY_SPACING = 3.2 * 3.2  # scale 3.2 same as mockup


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    """Convert ``'#rrggbb'`` to ``(r, g, b)`` ints."""
    h = hex_colour.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _color_idx(s: str) -> int:
    """Deterministic palette index from a string — same hash as mockup."""
    h = 0
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(h) % len(_PALETTE)


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------


def _load_fonts() -> tuple[
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
]:
    """Return ``(title_font, mono_font)``. Falls back to PIL default if TTF absent."""
    title_path = RESOURCES_DIR / "Inter-Light.ttf"
    mono_path = RESOURCES_DIR / "JetBrainsMono-Regular.ttf"
    try:
        title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(
            str(title_path), _TITLE_FONT_SIZE
        )
    except OSError:
        title_font = ImageFont.load_default()
    try:
        mono_font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(
            str(mono_path), _STATS_FONT_SIZE
        )
    except OSError:
        mono_font = ImageFont.load_default()
    return title_font, mono_font


# ---------------------------------------------------------------------------
# Background rendering helpers
# ---------------------------------------------------------------------------


def _draw_background(img: Image.Image) -> None:
    """Render nebula clouds, dust, mid-depth stars, streaks, and vignette."""
    _draw_nebula(img)
    _draw_dust_and_stars(img)
    _draw_vignette(img)


def _draw_nebula(img: Image.Image) -> None:
    """Three single-pass radial nebulae pasted as alpha overlays.

    Each nebula is a single filled circle + heavy Gaussian blur at ~10% peak
    alpha, mirroring the dashboard snapshotBackground.ts approach. Slots are
    anchored off-centre so nebulae frame the graph rather than fight it.
    """
    diagonal = math.sqrt(_SIZE * _SIZE + _SIZE * _SIZE)

    # Fixed slot anchors (top-left, right-mid, bottom-centre) with jitter
    slot_anchors = [
        (int(_SIZE * 0.18), int(_SIZE * 0.20)),
        (int(_SIZE * 0.82), int(_SIZE * 0.48)),
        (int(_SIZE * 0.48), int(_SIZE * 0.82)),
    ]

    for anchor_x, anchor_y in slot_anchors:
        colour_hex, _inner_op, _edge_op = random.choice(_NEBULA_COLOURS)  # noqa: S311
        r, g, b = _hex_to_rgb(colour_hex)

        # Jitter within slot and pick radius
        ncx = anchor_x + random.uniform(-60, 60)  # noqa: S311
        ncy = anchor_y + random.uniform(-60, 60)  # noqa: S311
        radius = diagonal * random.uniform(0.28, 0.36)  # noqa: S311

        # Build mask: filled circle at radius*0.5 then heavy blur
        peak_alpha = 0.10
        gsize = int(radius) * 2 + 8
        mask = Image.new("L", (gsize, gsize), 0)
        md = ImageDraw.Draw(mask)
        inner_r = max(1, int(radius * 0.5))
        mid = gsize // 2
        md.ellipse(
            [mid - inner_r, mid - inner_r, mid + inner_r, mid + inner_r],
            fill=255,
        )
        sigma = max(radius * 0.55, 8.0)
        mask = mask.filter(ImageFilter.GaussianBlur(radius=sigma))
        mask = mask.point(lambda p: int(p * peak_alpha))  # noqa: B023

        overlay = Image.new("RGB", (gsize, gsize), (r, g, b))
        px = int(ncx) - mid
        py = int(ncy) - mid
        img.paste(overlay, (px, py), mask)


def _draw_dust_and_stars(img: Image.Image) -> None:
    """Dust particles, mid-depth stars, and micro streaks."""
    draw = ImageDraw.Draw(img, "RGBA")

    # --- Dust ---
    n_dust = random.randint(150, 220)  # noqa: S311
    for _ in range(n_dust):
        dx = random.uniform(10, 1070)  # noqa: S311
        dy = random.uniform(10, 1070)  # noqa: S311
        dr = random.uniform(0.25, 0.55)  # noqa: S311
        dop = random.uniform(0.06, 0.22)  # noqa: S311
        alpha = int(dop * 255)
        rr = max(1, int(dr))
        draw.ellipse([dx - rr, dy - rr, dx + rr, dy + rr], fill=(239, 235, 224, alpha))

    # --- Mid-depth stars ---
    n_stars = random.randint(75, 110)  # noqa: S311
    star_palettes = [(239, 235, 224), (255, 209, 160), (168, 207, 255)]
    star_weights = [0.70, 0.16, 0.14]
    for _ in range(n_stars):
        sx = random.uniform(20, 1060)  # noqa: S311
        sy = random.uniform(20, 1060)  # noqa: S311
        sr = random.uniform(0.5, 1.35)  # noqa: S311
        sop = random.uniform(0.22, 0.60)  # noqa: S311
        roll = random.random()  # noqa: S311
        cumulative = 0.0
        scolour = star_palettes[0]
        for col, w in zip(star_palettes, star_weights, strict=False):
            cumulative += w
            if roll < cumulative:
                scolour = col
                break
        alpha = int(sop * 255)
        rr = max(1, int(sr))
        draw.ellipse([sx - rr, sy - rr, sx + rr, sy + rr], fill=(*scolour, alpha))

    # --- Micro streaks ---
    n_streaks = random.randint(6, 12)  # noqa: S311
    for _ in range(n_streaks):
        mx = random.uniform(40, 1040)  # noqa: S311
        my = random.uniform(40, 1040)  # noqa: S311
        mlen = random.uniform(10, 28)  # noqa: S311
        mangle = random.uniform(0, math.pi * 2)  # noqa: S311
        mx2 = mx + mlen * math.cos(mangle)
        my2 = my + mlen * math.sin(mangle)
        mop = random.uniform(0.20, 0.45)  # noqa: S311
        alpha = int(mop * 255)
        draw.line([(mx, my), (mx2, my2)], fill=(239, 235, 224, alpha), width=1)


def _draw_vignette(img: Image.Image) -> None:
    """Darkens extreme corners with a radial vignette."""
    vig_mask = Image.new("L", (_SIZE, _SIZE), 0)
    vd = ImageDraw.Draw(vig_mask)
    steps = 24
    for i in range(steps, 0, -1):
        frac = (steps - i) / steps
        rr = int(_SIZE * 0.36 + _SIZE * 0.36 * frac)
        # Quadratic falloff: stronger near corners
        alpha = int(0.55 * frac * frac * 255)
        vd.ellipse(
            [_SIZE // 2 - rr, _SIZE // 2 - rr, _SIZE // 2 + rr, _SIZE // 2 + rr],
            fill=alpha,
        )

    # Invert: we want the MASK to be bright at corners (outside the ellipses)
    # The ellipses above are bright at centre; we need darkness at corners.
    # Build a corner-dark overlay: paste black with the inverted mask.
    # point() on "L" images is safe (no arbitrary code — pure math expression).
    inverted_mask = vig_mask.point(lambda p: 255 - p)
    corner_overlay = Image.new("RGB", (_SIZE, _SIZE), (0, 0, 0))
    img.paste(corner_overlay, (0, 0), inverted_mask)


# ---------------------------------------------------------------------------
# Cluster rendering
# ---------------------------------------------------------------------------


def _draw_clusters(
    img: Image.Image,
    sources: list[SourceBreakdown],
    spacing: float,
) -> None:
    """Render all source clusters with glow passes and phyllotactic dots."""
    if not sources:
        return

    layout = select_layout(len(sources))
    source_positions = compute_source_positions(layout, len(sources))

    densities = [s.total_internal_links / max(s.total_entities, 1) for s in sources]
    max_density = max(densities) if densities else 1.0

    cx, cy = _CENTRE

    for idx, (src, (sx, sy)) in enumerate(zip(sources, source_positions, strict=False)):
        if not src.templates:
            continue
        total = sum(t.count for t in src.templates)
        if total == 0:
            continue

        density = src.total_internal_links / max(src.total_entities, 1)
        density_norm = math.sqrt(density / max_density) if max_density > 0 else 1.0
        glow_opacity_mul = min(1.0, 0.30 + 0.85 * density_norm)
        glow_radius_mul = 0.70 + 0.55 * density_norm

        radii = [disc_radius(t.count, spacing) for t in src.templates]
        centers = pack_circles(radii)

        # Per-source rotation for visual variety
        phase = (idx * 0.61) % (2 * math.pi)
        cos_p, sin_p = math.cos(phase), math.sin(phase)
        centers = [(x * cos_p - y * sin_p, x * sin_p + y * cos_p) for x, y in centers]

        ox = cx + int(sx)
        oy = cy + int(sy)

        _draw_source_cluster(
            img, src, centers, radii, ox, oy, spacing, glow_opacity_mul, glow_radius_mul
        )


def _draw_source_cluster(
    img: Image.Image,
    src: SourceBreakdown,
    centers: list[tuple[float, float]],
    radii: list[float],
    ox: int,
    oy: int,
    spacing: float,
    glow_opacity_mul: float,
    glow_radius_mul: float,
) -> None:
    """Render one source: wide glow, tight glow, then phyllotactic dots."""
    draw = ImageDraw.Draw(img, "RGBA")

    # --- Pass 1a: wide blurred outer glow ---
    for template, (tcx, tcy), r in zip(src.templates, centers, radii, strict=False):
        rv, gv, bv = _hex_to_rgb(template.color)
        px = ox + tcx
        py = oy + tcy
        glow_r_wide = max(r * 1.5 * glow_radius_mul, 12.0)
        op_wide = glow_opacity_mul * 0.5
        _paste_glow(img, px, py, glow_r_wide, rv, gv, bv, op_wide, blur=glow_r_wide * 0.4)

    # --- Pass 1b: tight unblurred inner glow ---
    for template, (tcx, tcy), r in zip(src.templates, centers, radii, strict=False):
        rv, gv, bv = _hex_to_rgb(template.color)
        px = ox + tcx
        py = oy + tcy
        glow_r_tight = max(r * 1.05 * glow_radius_mul, 9.0)
        op_tight = min(1.0, glow_opacity_mul * 1.1)
        _paste_glow(img, px, py, glow_r_tight, rv, gv, bv, op_tight, blur=0.0)

    # --- Pass 2: phyllotactic dots ---
    for template, (tcx, tcy), _r in zip(src.templates, centers, radii, strict=False):
        rv, gv, bv = _hex_to_rgb(template.color)
        px = ox + tcx
        py = oy + tcy
        cluster_rot = random.uniform(0, 2 * math.pi)  # noqa: S311
        for dx, dy, depth in disc_positions(template.count, spacing, rotation=cluster_rot):
            dot_r, dot_op = dot_variance(spacing * 1.35)
            depth_mul_r = 0.75 + 0.35 * depth
            depth_mul_o = 0.45 + 0.55 * depth
            final_r = max(0.5, dot_r * depth_mul_r)
            final_op = dot_op * depth_mul_o
            alpha = int(final_op * 255)
            pr = max(1, int(final_r))
            draw.ellipse(
                [px + dx - pr, py + dy - pr, px + dx + pr, py + dy + pr],
                fill=(rv, gv, bv, alpha),
            )


def _paste_glow(
    img: Image.Image,
    cx: float,
    cy: float,
    radius: float,
    r: int,
    g: int,
    b: int,
    opacity: float,
    blur: float,
) -> None:
    """Paste a soft radial glow centred at (cx, cy) onto ``img``.

    Implementation: single filled circle of radius ``radius * 0.5`` at full
    alpha, then Gaussian blur with sigma ~= radius * 0.45 so the edge
    smoothly tapers to zero. Produces correct bright-centre-fading-edge
    behaviour without the rectangular bounding-box artefact from paste-
    without-blur. ``blur`` parameter is reinterpreted as a minimum-blur
    floor; soft-core glows use a generous blur, tight glows use less.
    """
    gsize = int(radius) * 4 + 8  # oversize so blur doesn't clip
    gsize = max(gsize, 16)
    glow_mask = Image.new("L", (gsize, gsize), 0)
    gd = ImageDraw.Draw(glow_mask)
    inner_r = max(1, int(radius * 0.5))
    mid = gsize // 2
    gd.ellipse(
        [mid - inner_r, mid - inner_r, mid + inner_r, mid + inner_r],
        fill=255,
    )
    sigma = max(blur, radius * 0.45, 2.0)
    glow_mask = glow_mask.filter(ImageFilter.GaussianBlur(radius=sigma))
    # Scale by requested opacity.
    glow_mask = glow_mask.point(lambda p: int(p * opacity))
    overlay = Image.new("RGB", (gsize, gsize), (r, g, b))
    px = int(cx) - mid
    py = int(cy) - mid
    img.paste(overlay, (px, py), glow_mask)


# ---------------------------------------------------------------------------
# Chrome rendering
# ---------------------------------------------------------------------------


def _draw_chrome(
    img: Image.Image,
    breakdown: GraphBreakdown,
    title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    mono_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """Render horizontal rule, title, stats row, and top-right brand."""
    draw = ImageDraw.Draw(img, "RGBA")
    ivory = (239, 235, 224)

    _draw_corners(draw, ivory)

    # Horizontal rule
    draw.line([(72, _RULE_Y), (1008, _RULE_Y)], fill=(*ivory, int(0.15 * 255)), width=1)

    # Title
    raw_title = breakdown.title
    if raw_title is None:
        raw_title = (
            breakdown.sources[0].name if len(breakdown.sources) == 1 else breakdown.database_name
        )
    if len(raw_title) > _TITLE_MAX_CHARS:
        raw_title = raw_title[: _TITLE_MAX_CHARS - 1] + "…"
    draw.text((70, _TITLE_Y), raw_title, font=title_font, fill=(*ivory, 255))

    # Stats row
    n_templates = sum(len(s.templates) for s in breakdown.sources)
    stats_items = [
        ("ENTITIES", f"{breakdown.stats.total_nodes:,}"),
        ("LINKS", f"{breakdown.stats.total_edges:,}"),
        ("SOURCES", str(breakdown.stats.total_sources)),
        ("TEMPLATES", str(n_templates)),
    ]
    label_alpha = int(0.4 * 255)
    value_alpha = int(0.9 * 255)
    x_cursor = 72
    col_width = 160
    for label, value in stats_items:
        draw.text((x_cursor, _STATS_Y_LABEL), label, font=mono_font, fill=(*ivory, label_alpha))
        draw.text((x_cursor, _STATS_Y_VALUE), value, font=mono_font, fill=(*ivory, value_alpha))
        x_cursor += col_width

    _draw_brand(img, draw, mono_font, ivory)


def _draw_corners(draw: ImageDraw.ImageDraw, colour: tuple[int, int, int]) -> None:
    """Minimal corner bracket marks — matches SVG frame style."""
    alpha = int(0.25 * 255)
    c = (*colour, alpha)
    draw.line([(40, 48), (52, 48)], fill=c, width=1)
    draw.line([(48, 40), (48, 52)], fill=c, width=1)
    draw.line([(1028, 48), (1040, 48)], fill=c, width=1)
    draw.line([(1032, 40), (1032, 52)], fill=c, width=1)
    draw.line([(40, 1032), (52, 1032)], fill=c, width=1)
    draw.line([(48, 1028), (48, 1040)], fill=c, width=1)
    draw.line([(1028, 1032), (1040, 1032)], fill=c, width=1)
    draw.line([(1032, 1028), (1032, 1040)], fill=c, width=1)


def _draw_brand(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    mono_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    ivory: tuple[int, int, int],
) -> None:
    """Top-right brand block: logo.png + 'CHAOSCYPHER.COM' text."""
    logo_path = RESOURCES_DIR / "logo.png"
    anchor_y = 108
    text_end_x = 1008
    url_label = "CHAOSCYPHER.COM"
    brand_alpha = int(0.8 * 255)

    try:
        bbox = draw.textbbox((0, 0), url_label, font=mono_font)
        text_w = bbox[2] - bbox[0]
    except AttributeError:
        text_w = len(url_label) * 7  # fallback for default font

    logo_x = text_end_x - text_w - 4 - _LOGO_SIZE
    logo_y = anchor_y - _LOGO_SIZE // 2

    if logo_path.exists():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((_LOGO_SIZE, _LOGO_SIZE), Image.Resampling.LANCZOS)
            img.paste(logo, (int(logo_x), int(logo_y)), logo)
        except Exception:
            pass  # Logo is decorative; missing is non-fatal

    text_y = anchor_y - 6
    draw.text(
        (text_end_x - text_w, text_y),
        url_label,
        font=mono_font,
        fill=(*ivory, brand_alpha),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


class SnapshotRenderer:
    """Deterministic Pillow-based PNG renderer for a GraphBreakdown.

    Seeds ``random`` from ``hash(breakdown.generated_at.isoformat())`` at
    the start of each render so identical input produces identical output.
    """

    def render_png(self, breakdown: GraphBreakdown, out_path: Path) -> None:
        """Render a 1080x1080 PNG to ``out_path``. Creates parent dirs as needed."""
        random.seed(hash(breakdown.generated_at.isoformat()))

        out_path.parent.mkdir(parents=True, exist_ok=True)

        img = Image.new("RGB", (_SIZE, _SIZE), _BG_COLOUR)

        _draw_background(img)

        spacing = _SINGLE_BODY_SPACING if len(breakdown.sources) == 1 else _GALAXY_SPACING
        _draw_clusters(img, list(breakdown.sources), spacing)

        title_font, mono_font = _load_fonts()
        _draw_chrome(img, breakdown, title_font, mono_font)

        img.save(out_path, format="PNG", optimize=True)
