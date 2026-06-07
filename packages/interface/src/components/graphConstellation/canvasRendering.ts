// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Canvas rendering utilities for the graph constellation renderers
 * (DashboardGraph background + the per-source Knowledge map).
 *
 * Pure generator/utility functions that don't depend on React state — glow
 * sprite cache, 3D projection, deterministic node depth, and the ambient
 * particle/orb/pulse generators used by the decorative dashboard layer.
 */

import { hexToRgba } from '../../theme/cardStyles';

// Re-export for use in DashboardGraph without a second import
export { hexToRgba };

// ---------------------------------------------------------------------------
// Color palettes
// ---------------------------------------------------------------------------

const PARTICLE_COLORS = [
  '#00e5ff', // cyan
  '#ff0080', // magenta
  '#7c4dff', // purple
  '#1de9b6', // teal
  '#00e5ff',
  '#ff0080',
];

const AMBIENT_COLORS = [
  'rgba(0, 229, 255,',   // cyan
  'rgba(255, 0, 128,',   // magenta
  'rgba(124, 77, 255,',  // purple
  'rgba(29, 233, 182,',  // teal
];

// ---------------------------------------------------------------------------
// Dust particles (background parallax layer)
// ---------------------------------------------------------------------------

interface DustParticle {
  x: number;
  y: number;
  r: number;
  opacity: number;
}

/** Generates random dust particles for parallax depth effect. */
export function generateDust(count: number): DustParticle[] {
  return Array.from({ length: count }, () => ({
    x: Math.random(),
    y: Math.random(),
    r: 0.3 + Math.random() * 0.7,
    opacity: 0.015 + Math.random() * 0.025,
  }));
}

// ---------------------------------------------------------------------------
// Generative particle field (empty/loading state)
// ---------------------------------------------------------------------------

interface Particle {
  x: number;
  y: number;
  r: number;
  vx: number;
  vy: number;
  opacity: number;
  color: string;
  phase: number;
}

/** Generates a generative particle field for the empty/loading state. */
export function generateParticles(count: number): Particle[] {
  return Array.from({ length: count }, (_, i) => ({
    x: Math.random(),
    y: Math.random(),
    r: 1 + Math.random() * 3,
    vx: (Math.random() - 0.5) * 0.00018,
    vy: (Math.random() - 0.5) * 0.00018,
    opacity: 0.04 + Math.random() * 0.08,
    color: PARTICLE_COLORS[i % PARTICLE_COLORS.length],
    phase: Math.random() * Math.PI * 2,
  }));
}

// ---------------------------------------------------------------------------
// Ambient edge orbs -- soft glowing circles drifting near the margins
// ---------------------------------------------------------------------------

interface AmbientOrb {
  /** Normalized position (0-1) */
  x: number;
  y: number;
  /** Drift velocity */
  vx: number;
  vy: number;
  /** Base radius in px */
  r: number;
  /** Phase offset for pulsing */
  phase: number;
  /** Base opacity */
  opacity: number;
  /** Color string */
  color: string;
}

export function generateAmbientOrbs(count: number): AmbientOrb[] {
  return Array.from({ length: count }, () => {
    const edge = Math.random();
    let x: number, y: number;
    if (edge < 0.3) {
      x = Math.random();
      y = 0.75 + Math.random() * 0.25;
    } else if (edge < 0.5) {
      x = Math.random() * 0.25;
      y = 0.5 + Math.random() * 0.5;
    } else if (edge < 0.7) {
      x = 0.8 + Math.random() * 0.2;
      y = Math.random();
    } else {
      x = Math.random();
      y = Math.random() * 0.15;
    }

    return {
      x,
      y,
      vx: (Math.random() - 0.5) * 0.00004,
      vy: (Math.random() - 0.5) * 0.00004,
      r: 1.5 + Math.random() * 3,
      phase: Math.random() * Math.PI * 2,
      opacity: 0.06 + Math.random() * 0.1,
      color: AMBIENT_COLORS[Math.floor(Math.random() * AMBIENT_COLORS.length)],
    };
  });
}

// ---------------------------------------------------------------------------
// Pulse rings -- expanding circles that fade out
// ---------------------------------------------------------------------------

export interface PulseRing {
  x: number;
  y: number;
  born: number;
  lifetime: number;
  maxR: number;
  color: string;
}

export function spawnPulseRing(elapsed: number): PulseRing {
  const edge = Math.random();
  let x: number, y: number;
  if (edge < 0.4) {
    x = Math.random();
    y = 0.7 + Math.random() * 0.3;
  } else if (edge < 0.6) {
    x = Math.random() * 0.2;
    y = 0.3 + Math.random() * 0.5;
  } else if (edge < 0.8) {
    x = 0.85 + Math.random() * 0.15;
    y = Math.random() * 0.6;
  } else {
    x = 0.3 + Math.random() * 0.5;
    y = Math.random() * 0.12;
  }

  return {
    x,
    y,
    born: elapsed,
    lifetime: 4 + Math.random() * 4,
    maxR: 30 + Math.random() * 60,
    color: AMBIENT_COLORS[Math.floor(Math.random() * AMBIENT_COLORS.length)],
  };
}

// ---------------------------------------------------------------------------
// Glow sprite cache
// ---------------------------------------------------------------------------

const GLOW_SPRITE_SIZE = 64;
const glowSpriteCache = new Map<string, HTMLCanvasElement>();

export function getGlowSprite(color: string): HTMLCanvasElement {
  const cached = glowSpriteCache.get(color);
  if (cached) return cached;

  const size = GLOW_SPRITE_SIZE;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;

  const half = size / 2;
  const grad = ctx.createRadialGradient(half, half, 0, half, half, half);
  grad.addColorStop(0, color + '15');
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(half, half, half, 0, Math.PI * 2);
  ctx.fill();

  glowSpriteCache.set(color, canvas);
  return canvas;
}

const AMBIENT_SPRITE_SIZE = 128;
const ambientSpriteCache = new Map<string, HTMLCanvasElement>();

export function getAmbientOrbSprite(colorPrefix: string): HTMLCanvasElement {
  const cached = ambientSpriteCache.get(colorPrefix);
  if (cached) return cached;

  const size = AMBIENT_SPRITE_SIZE;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;

  const half = size / 2;
  const grad = ctx.createRadialGradient(half, half, 0, half, half, half);
  grad.addColorStop(0, colorPrefix + ' 1)');
  grad.addColorStop(0.4, colorPrefix + ' 0.3)');
  grad.addColorStop(1, colorPrefix + ' 0)');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(half, half, half, 0, Math.PI * 2);
  ctx.fill();

  ambientSpriteCache.set(colorPrefix, canvas);
  return canvas;
}

// ---------------------------------------------------------------------------
// 3D projection helpers
// ---------------------------------------------------------------------------

/** Default tilt angle -- looking down at the graph volume (radians). */
const TILT_X = 0.55; // ~31 deg
const COS_TILT = Math.cos(TILT_X);
const SIN_TILT = Math.sin(TILT_X);
/** Perspective field-of-view (smaller = more depth distortion). */
const FOV = 600;
/** Z-spread for node depth -- proportion of layout range. */
export const Z_SPREAD = 0.6;
/**
 * Near-clip denominator floor. Prevents `pScale` from going negative
 * (and crashing `ctx.arc` with a negative radius) when a node rotates
 * close enough to the camera that `tz * layoutScale <= -FOV`.
 */
const NEAR_CLIP_DENOM = FOV * 0.1;

interface Projected {
  px: number;
  py: number;
  /** Depth after projection (for sorting / alpha). */
  z: number;
  /** Perspective scale factor. */
  pScale: number;
}

/** Deterministic z-offset from a node ID string. */
export function nodeDepth(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  // Returns -0.5 to +0.5
  return (Math.abs(h) % 10000) / 10000 - 0.5;
}

/**
 * Project a 3D point (nx, ny, nz) into screen space with:
 *   1. Y-axis rotation (slow spin)
 *   2. X-axis tilt (looking down)
 *   3. Perspective division
 *
 * `cosTilt`/`sinTilt` default to the module tilt (~31°); a caller can pass a
 * steeper tilt (e.g. the dashboard) without affecting other consumers.
 */
export function project3D(
  nx: number,
  ny: number,
  nz: number,
  cosR: number,
  sinR: number,
  cx: number,
  cy: number,
  layoutScale: number,
  driftX: number,
  driftY: number,
  cosTilt: number = COS_TILT,
  sinTilt: number = SIN_TILT,
): Projected {
  // Rotate around Y axis (horizontal spin) -- affects x and z
  const rx = nx * cosR - nz * sinR;
  const rz = nx * sinR + nz * cosR;

  // Tilt around X axis (look down) -- affects y and z
  const ty = ny * cosTilt - rz * sinTilt;
  const tz = ny * sinTilt + rz * cosTilt;

  // Perspective projection with near-clip guard (see NEAR_CLIP_DENOM).
  const pScale = FOV / Math.max(NEAR_CLIP_DENOM, FOV + tz * layoutScale);
  const px = cx + rx * pScale * layoutScale + driftX;
  const py = cy + ty * pScale * layoutScale + driftY;

  return { px, py, z: tz, pScale };
}
