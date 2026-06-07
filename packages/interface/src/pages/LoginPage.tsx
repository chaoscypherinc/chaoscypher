// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * LoginPage — Glassmorphic login screen with animated constellation background.
 */
import { useState, useRef, useEffect, type FormEvent } from 'react';
import {
  Box,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  InputAdornment,
  IconButton,
} from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { useAuth } from '../contexts/useAuth';
import { useNavigate, useSearchParams } from 'react-router';
import { ChaosCypherPalette, ChaosCypherBackground, ChaosCypherNeutrals } from '../theme/palette';
import { ghostInputSx, ghostErrorAlertSx } from '../theme/ghostStyles';
import { getApiErrorMessage } from '../utils/errors';

// ── Constellation background ──────────────────────────────────────────────

const GLOW_SPRITE_SIZE = 64;
const constellationSpriteCache = new Map<string, HTMLCanvasElement>();

/** Get or create a cached glow sprite for a given hex color. */
function getParticleSprite(color: string): HTMLCanvasElement {
  const cached = constellationSpriteCache.get(color);
  if (cached) return cached;

  const size = GLOW_SPRITE_SIZE;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;

  const half = size / 2;
  const grad = ctx.createRadialGradient(half, half, 0, half, half, half);
  grad.addColorStop(0, color + '20');
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(half, half, half, 0, Math.PI * 2);
  ctx.fill();

  constellationSpriteCache.set(color, canvas);
  return canvas;
}

const COLORS = [
  ChaosCypherPalette.primary,
  ChaosCypherPalette.secondary,
  ChaosCypherPalette.accent,
  ChaosCypherPalette.purple,
  ChaosCypherPalette.success,
  ChaosCypherPalette.info,
];

interface Particle {
  x: number; y: number; vx: number; vy: number;
  r: number; color: string; phase: number;
}

interface Edge { a: number; b: number; }

function generateNetwork(count = 18): { particles: Particle[]; edges: Edge[] } {
  const particles: Particle[] = Array.from({ length: count }, (_, i) => ({
    x: 0.05 + Math.random() * 0.9,
    y: 0.05 + Math.random() * 0.9,
    vx: (Math.random() - 0.5) * 0.0002,
    vy: (Math.random() - 0.5) * 0.0002,
    r: 1.5 + Math.random() * 2.5,
    color: COLORS[i % COLORS.length],
    phase: Math.random() * Math.PI * 2,
  }));

  const edges: Edge[] = [];
  for (let i = 0; i < count; i++) {
    for (let j = i + 1; j < count; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      if (Math.sqrt(dx * dx + dy * dy) < 0.22) {
        edges.push({ a: i, b: j });
      }
    }
  }
  if (edges.length < 8) {
    for (let i = 0; i < count - 1 && edges.length < 10; i++) {
      edges.push({ a: i, b: i + 1 });
    }
  }
  return { particles, edges };
}

function ConstellationBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef(0);
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
        if (p.x < 0.02 || p.x > 0.98) p.vx *= -1;
        if (p.y < 0.02 || p.y > 0.98) p.vy *= -1;
      }

      for (const edge of edges) {
        const a = particles[edge.a];
        const b = particles[edge.b];
        ctx.beginPath();
        ctx.moveTo(a.x * w, a.y * h);
        ctx.lineTo(b.x * w, b.y * h);
        ctx.strokeStyle = a.color;
        ctx.globalAlpha = 0.08 + 0.04 * Math.sin(elapsed * 0.8 + a.phase);
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.globalAlpha = 1;

      for (const p of particles) {
        const px = p.x * w;
        const py = p.y * h;
        const pulse = 1 + 0.2 * Math.sin(elapsed * 1.5 + p.phase);
        const r = p.r * pulse;

        const glowR = r * 5;
        const sprite = getParticleSprite(p.color);
        const spriteSize = glowR * 2;
        ctx.drawImage(sprite, px - glowR, py - glowR, spriteSize, spriteSize);

        ctx.beginPath();
        ctx.arc(px, py, r, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = 0.5 + 0.3 * Math.sin(elapsed * 1.5 + p.phase);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      animRef.current = requestAnimationFrame(loop);
    };

    animRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(animRef.current);
  }, []);

  return (
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
  );
}

// ── LoginPage ─────────────────────────────────────────────────────────────

/** Only allow relative in-app paths as `next` — never open redirects. */
function safeNextPath(raw: string | null): string {
  if (!raw) return '/';
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/';
  return raw;
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;

    setError(null);
    setSubmitting(true);

    try {
      await login(username.trim(), password);
      const next = safeNextPath(searchParams.get('next'));
      navigate(next, { replace: true });
    } catch (err) {
      setError(getApiErrorMessage(err) || 'Invalid credentials. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box
      sx={{
        position: 'relative',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        bgcolor: ChaosCypherBackground.dark.default,
        overflow: 'hidden',
      }}
    >
      {/* Animated constellation background */}
      <ConstellationBackground />

      {/* Glassmorphic login card */}
      <Box
        sx={{
          position: 'relative',
          zIndex: 1,
          maxWidth: 420,
          width: '100%',
          mx: 3,
          p: 4,
          borderRadius: '16px',
          border: '1px solid rgba(255, 255, 255, 0.06)',
          bgcolor: 'rgba(10, 14, 23, 0.75)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), 0 0 80px rgba(0, 229, 255, 0.03)',
        }}
      >
        {/* Header */}
        <Box sx={{ textAlign: 'center', mb: 4 }}>
          <Box
            component="img"
            src="/logo.png"
            alt="Chaos Cypher"
            sx={{
              width: 72,
              height: 72,
              mb: 2,
              filter: 'drop-shadow(0 0 12px rgba(0, 229, 255, 0.3))',
            }}
          />
          <Typography
            variant="h4"
            sx={{
              fontWeight: 600,
              color: 'text.primary',
              letterSpacing: '-0.02em',
            }}
          >
            Chaos Cypher
          </Typography>
          <Typography
            sx={{
              color: ChaosCypherNeutrals.textMuted,
              fontSize: 14,
              mt: 0.5,
              letterSpacing: '0.02em',
            }}
          >
            Sign in to your knowledge engine
          </Typography>
        </Box>

        {/* Error */}
        {error && (
          <Alert
            severity="error"
            sx={{ mb: 2, ...ghostErrorAlertSx }}
            onClose={() => setError(null)}
          >
            {error}
          </Alert>
        )}

        {/* Login Form */}
        <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
          <TextField
            label="Username"
            variant="outlined"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            fullWidth
            required
            autoFocus
            autoComplete="username"
            sx={ghostInputSx}
          />
          <TextField
            label="Password"
            variant="outlined"
            type={showPassword ? 'text' : 'password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            fullWidth
            required
            autoComplete="current-password"
            sx={ghostInputSx}
            slotProps={{
              input: {
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      onClick={() => setShowPassword(!showPassword)}
                      edge="end"
                      size="small"
                      tabIndex={-1}
                      sx={{ color: 'rgba(255, 255, 255, 0.3)' }}
                    >
                      {showPassword ? <VisibilityOffIcon /> : <VisibilityIcon />}
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
          <Button
            type="submit"
            variant="outlined"
            size="large"
            fullWidth
            disabled={submitting || !username.trim() || !password}
            sx={{
              mt: 1,
              py: 1.25,
              borderRadius: '8px',
              borderColor: 'rgba(0, 229, 255, 0.3)',
              color: 'primary.main',
              bgcolor: 'rgba(0, 229, 255, 0.04)',
              fontSize: 14,
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'none',
              transition: 'all 0.2s',
              '&:hover': {
                borderColor: 'rgba(0, 229, 255, 0.6)',
                bgcolor: 'rgba(0, 229, 255, 0.08)',
                boxShadow: '0 0 20px rgba(0, 229, 255, 0.15)',
              },
              '&.Mui-disabled': {
                borderColor: 'rgba(255, 255, 255, 0.06)',
                color: 'rgba(255, 255, 255, 0.2)',
                bgcolor: 'transparent',
              },
            }}
          >
            {submitting ? <CircularProgress size={22} sx={{ color: 'primary.main' }} /> : 'Log In'}
          </Button>
        </Box>
      </Box>
    </Box>
  );
}
