// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LoadingState: Animated constellation loading screen.
 *
 * Renders a small network of glowing nodes drifting and pulsing
 * with faint connecting edges. Used as the universal loading indicator
 * across the entire application (Suspense fallbacks, page loads, etc.).
 */

import { useRef, useEffect } from 'react';
import { Box, Typography } from '@mui/material';
import { ChaosCypherPalette } from '../theme/palette';

const COLORS = [
  ChaosCypherPalette.primary,
  ChaosCypherPalette.secondary,
  ChaosCypherPalette.accent,
  ChaosCypherPalette.purple,
  ChaosCypherPalette.success,
  ChaosCypherPalette.info,
];

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  color: string;
  phase: number;
}

interface Edge {
  a: number;
  b: number;
}

function generateNetwork(): { particles: Particle[]; edges: Edge[] } {
  const count = 12;
  const particles: Particle[] = Array.from({ length: count }, (_, i) => ({
    x: 0.3 + Math.random() * 0.4,
    y: 0.3 + Math.random() * 0.4,
    vx: (Math.random() - 0.5) * 0.0003,
    vy: (Math.random() - 0.5) * 0.0003,
    r: 2 + Math.random() * 3,
    color: COLORS[i % COLORS.length],
    phase: Math.random() * Math.PI * 2,
  }));

  const edges: Edge[] = [];
  for (let i = 0; i < count; i++) {
    for (let j = i + 1; j < count; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      if (Math.sqrt(dx * dx + dy * dy) < 0.2) {
        edges.push({ a: i, b: j });
      }
    }
  }
  if (edges.length < 6) {
    for (let i = 0; i < count - 1 && edges.length < 8; i++) {
      edges.push({ a: i, b: i + 1 });
    }
  }

  return { particles, edges };
}

interface LoadingStateProps {
  /** Optional message to display below the animation. */
  message?: string;
  /** Minimum height of container (default: '400px'). */
  minHeight?: string;
  /** Size param (ignored — kept for backwards compatibility). */
  size?: number;
  /** Fill viewport height minus app shell (prevents jump between Suspense and page loading). */
  fullPage?: boolean;
}

/**
 * Animated constellation loading indicator.
 *
 * @example
 * if (loading) return <LoadingState message="Loading items..." />;
 */
export function LoadingState({
  message,
  minHeight = '400px',
  fullPage = false,
}: LoadingStateProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);
  const networkRef = useRef(generateNetwork());

  useEffect(() => {
    const loop = (time: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;

      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }

      ctx.clearRect(0, 0, w, h);
      const elapsed = time * 0.001;

      const { particles, edges } = networkRef.current;

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0.15 || p.x > 0.85) p.vx *= -1;
        if (p.y < 0.15 || p.y > 0.85) p.vy *= -1;
      }

      for (const edge of edges) {
        const a = particles[edge.a];
        const b = particles[edge.b];
        ctx.beginPath();
        ctx.moveTo(a.x * w, a.y * h);
        ctx.lineTo(b.x * w, b.y * h);
        ctx.strokeStyle = a.color;
        ctx.globalAlpha = 0.12 + 0.05 * Math.sin(elapsed * 0.8 + a.phase);
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.globalAlpha = 1;

      for (const p of particles) {
        const px = p.x * w;
        const py = p.y * h;
        const pulse = 1 + 0.2 * Math.sin(elapsed * 1.5 + p.phase);
        const r = p.r * pulse;

        const grad = ctx.createRadialGradient(px, py, 0, px, py, r * 4);
        grad.addColorStop(0, p.color + '30');
        grad.addColorStop(1, 'transparent');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(px, py, r * 4, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(px, py, r, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = 0.7 + 0.3 * Math.sin(elapsed * 1.5 + p.phase);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      animRef.current = requestAnimationFrame(loop);
    };

    animRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(animRef.current);
  }, []);

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: fullPage ? 'calc(100vh - 64px)' : minHeight,
        position: 'relative',
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
        }}
      />
      {message && (
        <Typography
          variant="body2"
          sx={{
            position: 'relative',
            zIndex: 1,
            color: 'rgba(255, 255, 255, 0.4)',
            fontFamily: 'monospace',
            fontSize: '0.8rem',
          }}
        >
          {message}
        </Typography>
      )}
    </Box>
  );
}
