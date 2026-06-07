// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useRef, useEffect, useMemo, useState } from 'react';
import { Box } from '@mui/material';
import {
  generateDust,
  generateParticles,
  generateAmbientOrbs,
  getGlowSprite,
  getAmbientOrbSprite,
  spawnPulseRing,
  hexToRgba,
  project3D,
  type PulseRing,
} from '../../components/graphConstellation/canvasRendering';
import {
  createIcosahedron,
  computeOrbitHomes,
  type Vec3,
} from './crystalGeometry';
import { useGraphData } from './useGraphData';

// --- Scene tuning -----------------------------------------------------------
/** Crystal/orbit are modelled in unit-circumradius space; this maps to pixels. */
const SCALE_FRACTION = 0.16;
/** Slow continuous yaw (rad/s) — the crystal is a solid, so a full turn is safe. */
const YAW_SPEED = 0.16;
/** Base look-down tilt + a gentle drift so different facets catch the light. */
const BASE_TILT = 0.42;
const PITCH_DRIFT_AMP = 0.16;
const PITCH_DRIFT_SPEED = 0.08;
/** Calm spring-back jiggle: small wander, springy lag, always returns to shape. */
const WANDER_AMP = 0.02;
const SPRING = 0.06;
const DAMP = 0.86;
/** Mouse parallax — camera offset in px at full deflection. */
const PARALLAX_PX = 14;
/** Crossfade speed (per frame) from loading → loaded state. */
const REVEAL_LERP = 0.02;
/**
 * Crystal brightness — held at this dim "heart" level in BOTH states (empty and
 * data-present) so it reads uniformly across the two and the orbiting data takes
 * focus once it arrives. `reveal` no longer brightens the crystal while empty; it
 * still drives the orbit/edge fade-in below.
 */
const CRYSTAL_DIM = 0.3;
/** Shell spin (rad/s) — source clumps orbit the crystal at this rate, relative to it. */
const SHELL_SPIN = 0.09;
/** Per-node wander so a clump breathes without breaking apart. */
const SHELL_WANDER = 0.03;

const CYAN: Vec3 = [0, 229, 255];
const MAGENTA: Vec3 = [255, 0, 128];

/** Crystal vertex color by world-x (cyan left → magenta right, like the logo). */
function crystalRgb(x: number): Vec3 {
  const t = Math.max(0, Math.min(1, (x + 1) / 2));
  return [
    Math.round(CYAN[0] + (MAGENTA[0] - CYAN[0]) * t),
    Math.round(CYAN[1] + (MAGENTA[1] - CYAN[1]) * t),
    Math.round(CYAN[2] + (MAGENTA[2] - CYAN[2]) * t),
  ];
}
function rgba(c: Vec3, a: number): string {
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
}

/** Deterministic pseudo-random in [0, 1) from a numeric seed (GLSL-style hash). */
function pseudoRandom(seed: number): number {
  const s = Math.sin(seed + 1) * 43758.5453;
  return s - Math.floor(s);
}

interface CrystalVertex {
  rest: Vec3;
  pos: Vec3;
  vel: Vec3;
  f1: number;
  f2: number;
  p1: number;
  p2: number;
}

/**
 * Full-bleed decorative 3D "data crystal" background.
 *
 * A single Canvas 2D scene under one slowly-tumbling camera:
 *   - The crystal — an icosahedron shaped like the ChaosCypher logo, with a
 *     bright pulsing core and a calm spring-back jiggle. It holds a steady faint
 *     "heart" brightness whether the graph is empty or loaded, and never drifts
 *     out of shape.
 *   - Once real graph data arrives, the real nodes fade in orbiting around it on
 *     mixed-inclination rings.
 *
 * Data fetch is decorative and fails soft (see useGraphData). Honors
 * prefers-reduced-motion by near-freezing the tumble/orbit.
 */
export default function DashboardGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const drawRef = useRef<((time: number) => void) | null>(null);

  // Ambient atmosphere (background depth) — dust + particles + edge glow orbs
  const dustRef = useRef(generateDust(60));
  const particlesRef = useRef(generateParticles(70));
  const ambientOrbsRef = useRef(generateAmbientOrbs(16));
  const pulseRingsRef = useRef<PulseRing[]>([]);
  const lastPulseRef = useRef(0);

  // Mouse parallax — smoothed normalized offset from center (-1..1)
  const mouseRef = useRef({ x: 0, y: 0 });
  const smoothMouseRef = useRef({ x: 0, y: 0 });

  // Crossfade + reduced-motion + latest-data, read inside the RAF closure
  const revealRef = useRef(0);
  const reduceMotionRef = useRef(false);
  const hasGraphRef = useRef(false);

  const { nodes, edges, loading } = useGraphData();
  const hasGraph = nodes.length > 0 && !loading;

  // Crystal geometry (stable) + per-vertex spring scratch state.
  // Spring state lives in lazy-init state: a stable, mutable array we animate in
  // place across frames (deterministic phases — no impurity, no ref-in-render).
  const { vertices, edges: crystalEdges } = useMemo(() => createIcosahedron(), []);
  const [crystalState] = useState<CrystalVertex[]>(() =>
    vertices.map((v, i) => ({
      rest: v,
      pos: [v[0], v[1], v[2]],
      vel: [0, 0, 0],
      f1: 0.5 + pseudoRandom(i * 12.9898) * 0.5,
      f2: 0.9 + pseudoRandom(i * 78.233) * 0.7,
      p1: pseudoRandom(i * 37.719) * Math.PI * 2,
      p2: pseudoRandom(i * 94.673) * Math.PI * 2,
    })),
  );

  // Fixed shell "home" per real node (deterministic; same-source nodes clump)
  const orbitHomes = useMemo<Vec3[]>(() => computeOrbitHomes(nodes), [nodes]);

  useEffect(() => {
    hasGraphRef.current = hasGraph;
  }, [hasGraph]);

  // prefers-reduced-motion (guarded for jsdom / older envs)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    reduceMotionRef.current = mq.matches;
    const onChange = (e: MediaQueryListEvent) => {
      reduceMotionRef.current = e.matches;
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  // Mouse parallax listener
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const target = canvas.parentElement ?? canvas;
    const onMove = (e: MouseEvent) => {
      const rect = target.getBoundingClientRect();
      mouseRef.current = {
        x: ((e.clientX - rect.left) / rect.width - 0.5) * 2,
        y: ((e.clientY - rect.top) / rect.height - 0.5) * 2,
      };
    };
    const onLeave = () => {
      mouseRef.current = { x: 0, y: 0 };
    };
    target.addEventListener('mousemove', onMove);
    target.addEventListener('mouseleave', onLeave);
    return () => {
      target.removeEventListener('mousemove', onMove);
      target.removeEventListener('mouseleave', onLeave);
    };
  }, []);

  // Draw function — rebuilt when the data it closes over changes
  useEffect(() => {
    drawRef.current = (time: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

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
      const motion = reduceMotionRef.current ? 0 : 1;

      // Smooth mouse + crossfade reveal
      const sm = smoothMouseRef.current;
      const tm = mouseRef.current;
      sm.x += (tm.x - sm.x) * 0.04;
      sm.y += (tm.y - sm.y) * 0.04;
      revealRef.current += ((hasGraphRef.current ? 1 : 0) - revealRef.current) * REVEAL_LERP;
      const reveal = revealRef.current;

      const cx = w * 0.5;
      const cy = h * 0.5;
      const scale = Math.min(w, h) * SCALE_FRACTION;
      const driftX = sm.x * PARALLAX_PX;
      const driftY = sm.y * PARALLAX_PX;

      // === Ambient atmosphere (dust + sparse particles) ===
      ctx.save();
      for (const d of dustRef.current) {
        const dx = d.x * w + Math.sin(elapsed * 0.03 + d.x * 5) * 2;
        const dy = d.y * h + Math.cos(elapsed * 0.02 + d.y * 5) * 1.5;
        ctx.beginPath();
        ctx.arc(dx, dy, d.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255,255,255,${d.opacity})`;
        ctx.fill();
      }
      for (const p of particlesRef.current) {
        p.x += p.vx * motion;
        p.y += p.vy * motion;
        if (p.x < 0) p.x = 1;
        if (p.x > 1) p.x = 0;
        if (p.y < 0) p.y = 1;
        if (p.y > 1) p.y = 0;
        const alpha = p.opacity * (0.6 + 0.4 * Math.sin(elapsed * 0.5 + p.phase));
        ctx.beginPath();
        ctx.arc(p.x * w, p.y * h, p.r * 0.6, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = alpha * 0.5;
        ctx.fill();
      }

      // Glow orbs drifting near the margins — fill the outer area so it's not plain.
      const orbs = ambientOrbsRef.current;
      for (const orb of orbs) {
        orb.x += orb.vx * motion;
        orb.y += orb.vy * motion;
        if (orb.x < -0.05) orb.x = 1.05;
        if (orb.x > 1.05) orb.x = -0.05;
        if (orb.y < -0.05) orb.y = 1.05;
        if (orb.y > 1.05) orb.y = -0.05;
        const ox = orb.x * w;
        const oy = orb.y * h;
        const op = 1 + 0.3 * Math.sin(elapsed * 0.6 + orb.phase);
        const orbR = orb.r * op;
        const oa = orb.opacity * (0.6 + 0.4 * Math.sin(elapsed * 0.4 + orb.phase + 1));
        const orbGlowR = orbR * 6;
        ctx.globalAlpha = oa * 0.5;
        ctx.drawImage(getAmbientOrbSprite(orb.color), ox - orbGlowR, oy - orbGlowR, orbGlowR * 2, orbGlowR * 2);
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.arc(ox, oy, orbR, 0, Math.PI * 2);
        ctx.fillStyle = `${orb.color} ${oa})`;
        ctx.fill();
      }
      // Faint connecting network between nearby orbs.
      for (let i = 0; i < orbs.length; i++) {
        for (let j = i + 1; j < orbs.length; j++) {
          const odx = (orbs[i].x - orbs[j].x) * w;
          const ody = (orbs[i].y - orbs[j].y) * h;
          const odist = Math.sqrt(odx * odx + ody * ody);
          if (odist < 200) {
            ctx.beginPath();
            ctx.moveTo(orbs[i].x * w, orbs[i].y * h);
            ctx.lineTo(orbs[j].x * w, orbs[j].y * h);
            ctx.strokeStyle = `rgba(0,229,255,${(1 - odist / 200) * 0.05})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
      // Occasional expanding pulse rings (skipped under reduced motion).
      const rings = pulseRingsRef.current;
      if (motion && elapsed - lastPulseRef.current > 3 && rings.length < 4) {
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
        const rt = age / ring.lifetime;
        ctx.beginPath();
        ctx.arc(ring.x * w, ring.y * h, ring.maxR * rt, 0, Math.PI * 2);
        ctx.strokeStyle = `${ring.color} ${0.07 * (1 - rt) * (1 - rt)})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      ctx.restore();

      // === Camera ===
      const yaw = elapsed * YAW_SPEED * motion;
      const cosR = Math.cos(yaw);
      const sinR = Math.sin(yaw);
      const pitch = BASE_TILT + Math.sin(elapsed * PITCH_DRIFT_SPEED) * PITCH_DRIFT_AMP * motion;
      const cosT = Math.cos(pitch);
      const sinT = Math.sin(pitch);
      const proj = (p: Vec3) =>
        project3D(p[0], p[1], p[2], cosR, sinR, cx, cy, scale, driftX, driftY, cosT, sinT);

      // === Crystal: spring jiggle + project ===
      const cVerts = crystalState.map((c) => {
        const wx = WANDER_AMP * (Math.sin(elapsed * c.f1 + c.p1) + 0.5 * Math.sin(elapsed * c.f2 + c.p2));
        const wy = WANDER_AMP * (Math.cos(elapsed * c.f1 * 0.9 + c.p2) + 0.5 * Math.cos(elapsed * c.f2 + c.p1));
        const wz = WANDER_AMP * (Math.sin(elapsed * c.f2 + c.p1) * 0.7);
        const tx = c.rest[0] + wx * motion;
        const ty = c.rest[1] + wy * motion;
        const tz = c.rest[2] + wz * motion;
        c.vel[0] = (c.vel[0] + (tx - c.pos[0]) * SPRING) * DAMP;
        c.vel[1] = (c.vel[1] + (ty - c.pos[1]) * SPRING) * DAMP;
        c.vel[2] = (c.vel[2] + (tz - c.pos[2]) * SPRING) * DAMP;
        c.pos[0] += c.vel[0];
        c.pos[1] += c.vel[1];
        c.pos[2] += c.vel[2];
        const pr = proj(c.pos);
        return { pr, color: crystalRgb(c.pos[0]) };
      });

      // Uniform brightness across empty + data-present states — held at the dim
      // "heart" level rather than brightening to full while empty (see CRYSTAL_DIM).
      const crystalAlpha = CRYSTAL_DIM;

      // === Orbiting real-data nodes (one rigid shell, spun as a body) ===
      const idToPos = new Map<string, { px: number; py: number; z: number }>();
      const shellAngle = SHELL_SPIN * elapsed * motion;
      const cosS = Math.cos(shellAngle);
      const sinS = Math.sin(shellAngle);
      const orbitNodes = reveal > 0.002
        ? nodes.map((node, i) => {
            const home = orbitHomes[i];
            // Spin the whole shell around Y so source clumps orbit the crystal together.
            const sx = home[0] * cosS + home[2] * sinS;
            const sz = -home[0] * sinS + home[2] * cosS;
            // Gentle per-node wander so a clump breathes without breaking apart.
            const wob = SHELL_WANDER * motion;
            const wp: Vec3 = [
              sx + wob * Math.sin(elapsed * 0.6 + i),
              home[1] + wob * Math.cos(elapsed * 0.5 + i * 1.3),
              sz + wob * Math.sin(elapsed * 0.7 + i * 0.7),
            ];
            const pr = proj(wp);
            idToPos.set(node.id, { px: pr.px, py: pr.py, z: pr.z });
            return { node, pr };
          })
        : [];

      // Depth range for fading (crystal + orbit)
      let zMin = Infinity;
      let zMax = -Infinity;
      for (const c of cVerts) {
        if (c.pr.z < zMin) zMin = c.pr.z;
        if (c.pr.z > zMax) zMax = c.pr.z;
      }
      for (const o of orbitNodes) {
        if (o.pr.z < zMin) zMin = o.pr.z;
        if (o.pr.z > zMax) zMax = o.pr.z;
      }
      const zRange = zMax - zMin || 1;
      const depthFade = (z: number) => 0.3 + 0.7 * ((zMax - z) / zRange);

      // === Core "heart" glow at the crystal center ===
      const core = proj([0, 0, 0]);
      const pulse = 1 + 0.16 * Math.sin(elapsed * 1.2);
      const coreR = scale * 0.9 * pulse;
      const coreGrad = ctx.createRadialGradient(core.px, core.py, 0, core.px, core.py, coreR);
      coreGrad.addColorStop(0, `rgba(200,250,255,${0.5 * crystalAlpha})`);
      coreGrad.addColorStop(0.45, `rgba(0,229,255,${0.16 * crystalAlpha})`);
      coreGrad.addColorStop(1, 'rgba(0,229,255,0)');
      ctx.fillStyle = coreGrad;
      ctx.fillRect(0, 0, w, h);

      // === Crystal edges ===
      ctx.save();
      ctx.lineWidth = 1.2;
      for (const [a, b] of crystalEdges) {
        const ca = cVerts[a];
        const cb = cVerts[b];
        const alpha = (0.15 + 0.45 * depthFade((ca.pr.z + cb.pr.z) / 2)) * crystalAlpha;
        const grad = ctx.createLinearGradient(ca.pr.px, ca.pr.py, cb.pr.px, cb.pr.py);
        grad.addColorStop(0, rgba(ca.color, alpha));
        grad.addColorStop(1, rgba(cb.color, alpha));
        ctx.strokeStyle = grad;
        ctx.beginPath();
        ctx.moveTo(ca.pr.px, ca.pr.py);
        ctx.lineTo(cb.pr.px, cb.pr.py);
        ctx.stroke();
      }
      ctx.restore();

      // === Real-data edges (very faint, fade in with reveal) ===
      if (reveal > 0.002) {
        ctx.save();
        ctx.lineWidth = 0.6;
        for (const edge of edges) {
          const s = idToPos.get(edge.source);
          const t = idToPos.get(edge.target);
          if (!s || !t) continue;
          const alpha = edge.opacity * 0.35 * depthFade((s.z + t.z) / 2) * reveal;
          ctx.strokeStyle = hexToRgba(edge.color, alpha);
          ctx.beginPath();
          ctx.moveTo(s.px, s.py);
          ctx.lineTo(t.px, t.py);
          ctx.stroke();
        }
        ctx.restore();
      }

      // === Nodes (crystal + orbit) back-to-front ===
      interface DrawNode {
        px: number;
        py: number;
        z: number;
        r: number;
        alpha: number;
        color: string;
        glowColor: string | null;
      }
      const drawNodes: DrawNode[] = [];
      for (const c of cVerts) {
        drawNodes.push({
          px: c.pr.px,
          py: c.pr.py,
          z: c.pr.z,
          r: 3 * c.pr.pScale,
          alpha: (0.55 + 0.4 * depthFade(c.pr.z)) * crystalAlpha,
          color: rgba(c.color, 1),
          glowColor: null,
        });
      }
      for (const o of orbitNodes) {
        drawNodes.push({
          px: o.pr.px,
          py: o.pr.py,
          z: o.pr.z,
          r: o.node.radius * 0.6 * o.pr.pScale,
          alpha: o.node.opacity * (0.5 + 0.5 * depthFade(o.pr.z)) * reveal,
          color: o.node.color,
          glowColor: o.node.color,
        });
      }
      drawNodes.sort((a, b) => b.z - a.z);

      ctx.save();
      for (const n of drawNodes) {
        if (n.r <= 0) continue;
        const glowR = n.r * 4;
        if (n.glowColor) {
          ctx.globalAlpha = n.alpha * 0.4;
          ctx.drawImage(getGlowSprite(n.glowColor), n.px - glowR, n.py - glowR, glowR * 2, glowR * 2);
        } else {
          const g = ctx.createRadialGradient(n.px, n.py, 0, n.px, n.py, glowR);
          g.addColorStop(0, n.color.replace(',1)', `,${(0.45 * n.alpha).toFixed(3)})`));
          g.addColorStop(1, n.color.replace(',1)', ',0)'));
          ctx.globalAlpha = 1;
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(n.px, n.py, glowR, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = n.alpha;
        ctx.fillStyle = n.color;
        ctx.beginPath();
        ctx.arc(n.px, n.py, n.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
    };
  }, [nodes, edges, orbitHomes, crystalState, crystalEdges]);

  // RAF loop
  useEffect(() => {
    const loop = (time: number) => {
      try {
        drawRef.current?.(time);
      } catch (err) {
        // A draw-time exception must not kill the RAF loop.
        console.error('DashboardGraph draw error', err);
      }
      animRef.current = requestAnimationFrame(loop);
    };
    animRef.current = requestAnimationFrame(loop);

    const canvas = canvasRef.current;
    const observer = new ResizeObserver(() => {
      const c = canvasRef.current;
      if (!c) return;
      c.width = 0;
      c.height = 0;
    });
    if (canvas) observer.observe(canvas);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(animRef.current);
    };
  }, []);

  return (
    <Box
      sx={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
      }}
    >
      <canvas
        ref={canvasRef}
        data-testid="dashboard-graph-canvas"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          display: 'block',
        }}
      />
    </Box>
  );
}
