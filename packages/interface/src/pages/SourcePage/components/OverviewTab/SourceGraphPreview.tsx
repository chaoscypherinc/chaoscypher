// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Per-source "Knowledge map" — a bounded, slowly-rotating glowing
 * constellation of this source's entities, colored by template. It's a
 * doorway: clicking anywhere opens the full interactive Sigma graph already
 * filtered to this source (`/graph?source_ids=<id>`).
 *
 * The renderer reuses the shared Canvas 2D primitives (3D projection, glow
 * sprites) that power the DashboardGraph background, scoped here to one
 * source. On hover the rotation eases to a stop and a "View full graph"
 * affordance brightens; `prefers-reduced-motion` renders a single static
 * frame. Rendered only for committed sources that produced entities.
 */

import { useEffect, useRef, useState } from 'react';
import { Box, Typography, alpha, useTheme } from '@mui/material';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import HubIcon from '@mui/icons-material/Hub';
import { useNavigate } from 'react-router';
import type { Source } from '../../../../types';
import { isSourceCommitted } from '../../../../types';
import { logger } from '../../../../utils/logger';
import { SURFACE_BG, SURFACE_BORDER } from '../../../../theme/cardStyles';
import {
  generateParticles,
  generateAmbientOrbs,
  getAmbientOrbSprite,
  getGlowSprite,
  hexToRgba,
  nodeDepth,
  project3D,
  spawnPulseRing,
  type PulseRing,
  Z_SPREAD,
} from '../../../../components/graphConstellation/canvasRendering';
import { useSourceGraphPreview } from './hooks/useSourceGraphPreview';

interface SourceGraphPreviewProps {
  source: Source;
}

/** Floor height; the card otherwise fills its (taller) grid cell on the Overview hero. */
const CARD_MIN_HEIGHT = 340;

export function SourceGraphPreview({ source }: SourceGraphPreviewProps) {
  const navigate = useNavigate();
  const theme = useTheme();

  const enabled = isSourceCommitted(source) && (source.extraction_entities_count ?? 0) > 0;
  const { nodes, edges, entityCount, relationshipCount, loading, isEmpty } = useSourceGraphPreview(
    source.id,
    enabled,
  );

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hoveredRef = useRef(false);
  const [hovered, setHovered] = useState(false);
  const rafRef = useRef(0);
  const ampRef = useRef(1); // rock amplitude, eased to 0 on hover
  const fitRef = useRef(1); // safety zoom; <1 only when nodes would clip the edge
  const lastTRef = useRef(0);
  const driftRef = useRef<{ dx: number; dy: number }[]>([]);

  // Ambient atmosphere — drifting particles + glow orbs that fill the wide
  // band around the central constellation so the hero never reads as empty.
  const particlesRef = useRef(generateParticles(56));
  const orbsRef = useRef(generateAmbientOrbs(16));
  const pulseRingsRef = useRef<PulseRing[]>([]);
  const lastPulseRef = useRef(0);

  // Stable per-node drift offsets so the constellation breathes organically.
  useEffect(() => {
    driftRef.current = nodes.map(() => ({
      dx: Math.random() * Math.PI * 2,
      dy: Math.random() * Math.PI * 2,
    }));
  }, [nodes]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reduceMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // Gentle left-right rock (not a full spin) keeps the constellation
    // broadside so it stays wide; amplitude eases to 0 on hover so it settles.
    const MAX_ROCK = 0.5; // radians (~28°)
    const ROCK_SPEED = 0.42; // ~15s period

    // Atmospheric backdrop: dim drifting particles, soft glow orbs with a
    // faint connecting network, and the occasional pulse ring. Painted behind
    // the constellation to fill the band's full width.
    const drawAmbient = (ctx: CanvasRenderingContext2D, w: number, h: number, elapsed: number) => {
      const particles = particlesRef.current;
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = 1;
        if (p.x > 1) p.x = 0;
        if (p.y < 0) p.y = 1;
        if (p.y > 1) p.y = 0;
        const a = p.opacity * 0.6 * (0.6 + 0.4 * Math.sin(elapsed * 0.5 + p.phase));
        ctx.beginPath();
        ctx.arc(p.x * w, p.y * h, p.r * 0.7, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = a;
        ctx.fill();
      }

      const orbs = orbsRef.current;
      for (const orb of orbs) {
        orb.x += orb.vx;
        orb.y += orb.vy;
        if (orb.x < -0.05) orb.x = 1.05;
        if (orb.x > 1.05) orb.x = -0.05;
        if (orb.y < -0.05) orb.y = 1.05;
        if (orb.y > 1.05) orb.y = -0.05;
        const ox = orb.x * w;
        const oy = orb.y * h;
        const pulse = 1 + 0.3 * Math.sin(elapsed * 0.6 + orb.phase);
        const r = orb.r * pulse;
        const a = orb.opacity * (0.6 + 0.4 * Math.sin(elapsed * 0.4 + orb.phase + 1));
        const glowR = r * 6;
        ctx.globalAlpha = a * 0.5;
        ctx.drawImage(getAmbientOrbSprite(orb.color), ox - glowR, oy - glowR, glowR * 2, glowR * 2);
        ctx.beginPath();
        ctx.arc(ox, oy, r, 0, Math.PI * 2);
        ctx.fillStyle = `${orb.color} ${a})`;
        ctx.fill();
      }
      // Faint orb network.
      for (let i = 0; i < orbs.length; i++) {
        for (let j = i + 1; j < orbs.length; j++) {
          const dx = (orbs[i].x - orbs[j].x) * w;
          const dy = (orbs[i].y - orbs[j].y) * h;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDist = 200;
          if (dist < maxDist) {
            ctx.beginPath();
            ctx.moveTo(orbs[i].x * w, orbs[i].y * h);
            ctx.lineTo(orbs[j].x * w, orbs[j].y * h);
            ctx.strokeStyle = `rgba(0, 229, 255, ${(1 - dist / maxDist) * 0.05})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }

      const rings = pulseRingsRef.current;
      if (elapsed - lastPulseRef.current > 3 && rings.length < 4) {
        rings.push(spawnPulseRing(elapsed));
        lastPulseRef.current = elapsed;
      }
      for (let i = rings.length - 1; i >= 0; i--) {
        const ring = rings[i];
        const age = elapsed - ring.born;
        if (age > ring.lifetime) {
          rings.splice(i, 1);
          continue;
        }
        const t = age / ring.lifetime;
        ctx.beginPath();
        ctx.arc(ring.x * w, ring.y * h, ring.maxR * t, 0, Math.PI * 2);
        ctx.strokeStyle = `${ring.color} ${0.07 * (1 - t) * (1 - t)})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    };

    const draw = (time: number) => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return; // jsdom / no-2d-context — no-op
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w === 0 || h === 0) return;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      ctx.clearRect(0, 0, w, h);
      const elapsed = time * 0.001;

      const dt = lastTRef.current ? Math.min(0.1, (time - lastTRef.current) / 1000) : 0;
      lastTRef.current = time;
      const targetAmp = hoveredRef.current ? 0 : 1;
      ampRef.current += (targetAmp - ampRef.current) * Math.min(1, dt * 3);
      const angle = Math.sin(elapsed * ROCK_SPEED) * MAX_ROCK * ampRef.current;

      // Atmosphere fills the full width first (also covers the loading state).
      drawAmbient(ctx, w, h, elapsed);

      const ns = nodes;
      const es = edges;
      if (ns.length === 0) return;

      const cx = w * 0.5;
      const cy = h * 0.5;
      // Fit the ~600×400 layout box into the (now near-square) cell with a
      // little padding, taking the limiting axis so it never overflows.
      const scale = Math.min(w / 620, h / 440);
      const cosR = Math.cos(angle);
      const sinR = Math.sin(angle);

      // Layout extent drives the z-spread for depth.
      let xMin = Infinity, xMax = -Infinity;
      for (const n of ns) {
        if (n.x < xMin) xMin = n.x;
        if (n.x > xMax) xMax = n.x;
      }
      const zSpread = (xMax - xMin || 1) * Z_SPREAD;

      // Spread the constellation horizontally to use the band's width. The
      // gentle rock keeps it broadside, so a screen-space x-stretch fills the
      // space without the disk turning edge-on. Capped so it never looks torn.
      const faceWidth = (xMax - xMin || 1) * scale;
      const stretchX = Math.min(2.4, Math.max(1, (w * 0.62) / faceWidth));

      const projected = ns.map((node, i) => {
        const drift = driftRef.current[i] || { dx: 0, dy: 0 };
        const driftX = Math.sin(elapsed * 0.3 + drift.dx) * 3 * scale;
        const driftY = Math.cos(elapsed * 0.24 + drift.dy) * 3 * scale;
        const nz = nodeDepth(node.id) * zSpread;
        const p = project3D(node.x, node.y, nz, cosR, sinR, cx, cy, scale, driftX, driftY);
        return { px: cx + (p.px - cx) * stretchX, py: p.py, z: p.z, pScale: p.pScale, idx: i };
      });

      // Safety fit: keep every node — with its radius — inside the card,
      // accounting for the x-stretch, rotation depth, perspective and drift.
      // Eased so it never snaps; stays at 1 unless something would clip.
      let maxDx = 1;
      let maxDy = 1;
      for (const p of projected) {
        const rad = ns[p.idx].radius * scale * p.pScale + 6;
        maxDx = Math.max(maxDx, Math.abs(p.px - cx) + rad);
        maxDy = Math.max(maxDy, Math.abs(p.py - cy) + rad);
      }
      const FIT_PAD = 10;
      const fitTarget = Math.min(1, (w / 2 - FIT_PAD) / maxDx, (h / 2 - FIT_PAD) / maxDy);
      fitRef.current = dt === 0 ? fitTarget : fitRef.current + (fitTarget - fitRef.current) * Math.min(1, dt * 4);
      const fit = fitRef.current;
      for (const p of projected) {
        p.px = cx + (p.px - cx) * fit;
        p.py = cy + (p.py - cy) * fit;
      }

      // Painter's algorithm: furthest (largest z) first.
      projected.sort((a, b) => b.z - a.z);

      const posMap = new Map<string, (typeof projected)[number]>();
      const colorMap = new Map<string, string>();
      const radiusMap = new Map<string, number>();
      for (const p of projected) {
        const node = ns[p.idx];
        posMap.set(node.id, p);
        colorMap.set(node.id, node.color);
        radiusMap.set(node.id, node.radius * scale * p.pScale * fit);
      }
      const minZ = projected[projected.length - 1]?.z ?? 0;
      const maxZ = projected[0]?.z ?? 0;
      const zRange = maxZ - minZ || 1;

      // Edges (alpha boosted vs. the dashboard — a single source is sparser).
      for (const edge of es) {
        const s = posMap.get(edge.source);
        const t = posMap.get(edge.target);
        if (!s || !t) continue;
        const edx = t.px - s.px;
        const edy = t.py - s.py;
        const len = Math.sqrt(edx * edx + edy * edy);
        if (len < 1) continue;
        const rS = radiusMap.get(edge.source) || 0;
        const rT = radiusMap.get(edge.target) || 0;
        if (rS + rT >= len) continue;
        const ux = edx / len;
        const uy = edy / len;
        const x1 = s.px + ux * rS;
        const y1 = s.py + uy * rS;
        const x2 = t.px - ux * rT;
        const y2 = t.py - uy * rT;
        const depth = 0.3 + 0.7 * ((maxZ - (s.z + t.z) / 2) / zRange);
        const a = Math.min(0.55, edge.opacity * 4) * depth;
        const sc = colorMap.get(edge.source) || edge.color;
        const tc = colorMap.get(edge.target) || edge.color;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        if (sc === tc) {
          ctx.strokeStyle = sc;
          ctx.globalAlpha = a;
        } else {
          const g = ctx.createLinearGradient(x1, y1, x2, y2);
          g.addColorStop(0, hexToRgba(sc, a));
          g.addColorStop(1, hexToRgba(tc, a));
          ctx.strokeStyle = g;
          ctx.globalAlpha = 1;
        }
        ctx.lineWidth = Math.max(0.5, scale * Math.min(s.pScale, t.pScale));
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // Nodes (back-to-front, with glow).
      for (const p of projected) {
        const node = ns[p.idx];
        const r = Math.max(0.5, node.radius * scale * p.pScale * fit);
        const depth = 0.3 + 0.7 * ((maxZ - p.z) / zRange);
        const alpha = Math.min(1, node.opacity * 1.6) * depth;
        if (alpha > 0.1) {
          const glowR = r * 4;
          const sprite = getGlowSprite(node.color);
          ctx.globalAlpha = alpha * 0.5;
          ctx.drawImage(sprite, p.px - glowR, p.py - glowR, glowR * 2, glowR * 2);
        }
        ctx.beginPath();
        ctx.arc(p.px, p.py, r, 0, Math.PI * 2);
        ctx.fillStyle = node.color;
        ctx.globalAlpha = alpha;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    if (reduceMotion) {
      draw(0);
      return;
    }

    const loop = (time: number) => {
      try {
        draw(time);
      } catch (err) {
        logger.error(err);
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);

    const observer = new ResizeObserver(() => {
      const c = canvasRef.current;
      if (!c) return;
      c.width = 0;
      c.height = 0;
    });
    observer.observe(canvas);

    return () => {
      observer.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      lastTRef.current = 0;
    };
    // Re-init when the rendered layout changes (new source / data arrival);
    // nodes & edges change together since they come from one memoized result.
  }, [nodes, edges]);

  if (!enabled) return null;
  if (!loading && isEmpty) return null;

  const openGraph = () => navigate(`/graph?source_ids=${source.id}`);

  return (
    <Box
      role="button"
      tabIndex={0}
      aria-label="Open the full knowledge graph for this source"
      data-testid="source-graph-preview"
      onClick={openGraph}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openGraph();
        }
      }}
      onMouseEnter={() => {
        hoveredRef.current = true;
        setHovered(true);
      }}
      onMouseLeave={() => {
        hoveredRef.current = false;
        setHovered(false);
      }}
      sx={{
        position: 'relative',
        height: '100%',
        minHeight: CARD_MIN_HEIGHT,
        borderRadius: 1.5,
        overflow: 'hidden',
        cursor: 'pointer',
        border: '1px solid',
        borderColor: hovered ? alpha(theme.palette.primary.main, 0.45) : SURFACE_BORDER,
        // Dark, translucent base (the shared surface) so the map sinks into the
        // page like the rest of the tabs; the cyan glow sits on top of it.
        background: `radial-gradient(ellipse at 50% 45%, ${alpha(
          theme.palette.primary.main,
          0.1,
        )}, rgba(0,0,0,0) 60%), ${SURFACE_BG}`,
        transition: 'border-color 0.2s',
        '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 2 },
      }}
    >
      <canvas
        ref={canvasRef}
        data-testid="source-graph-canvas"
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }}
      />

      {/* Title */}
      <Box sx={{ position: 'absolute', top: 10, left: 12, display: 'flex', alignItems: 'center', gap: 0.75 }}>
        <HubIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
        <Typography
          variant="overline"
          sx={{ fontSize: '0.62rem', letterSpacing: 1.2, opacity: 0.75, lineHeight: 1 }}
        >
          Knowledge map
        </Typography>
      </Box>

      {/* Counts */}
      {!loading && (
        <Typography
          sx={{ position: 'absolute', bottom: 10, left: 12, fontSize: '0.65rem', color: 'text.secondary' }}
        >
          {entityCount.toLocaleString()} entities &middot; {relationshipCount.toLocaleString()} relationships
        </Typography>
      )}

      {/* Click affordance */}
      <Box
        sx={{
          position: 'absolute',
          bottom: 9,
          right: 12,
          display: 'flex',
          alignItems: 'center',
          gap: 0.5,
          color: hovered ? 'primary.main' : 'text.secondary',
          opacity: hovered ? 1 : 0.55,
          transition: 'opacity 0.2s, color 0.2s',
        }}
      >
        <Typography variant="caption" sx={{ fontSize: '0.65rem' }}>
          View full graph
        </Typography>
        <OpenInFullIcon sx={{ fontSize: 13 }} />
      </Box>

      {/* Loading shimmer */}
      {loading && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Typography variant="caption" sx={{ color: 'text.secondary', opacity: 0.7 }}>
            Building map…
          </Typography>
        </Box>
      )}
    </Box>
  );
}
