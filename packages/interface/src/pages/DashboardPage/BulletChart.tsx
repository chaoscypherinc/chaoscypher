// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { Box, Typography, Tooltip } from '@mui/material';
import { ChaosCypherPalette } from '../../theme/palette';
import { Overlays } from '../../theme/overlays';
import {
  type BandState,
  type BulletConfig,
  classify,
  fillPct,
  tickPositions,
} from './utils/bulletBands';

/**
 * Map each qualitative band state to its canonical entry in the project's
 * semantic palette. Lives here (not in `bulletBands.ts`) so the band config
 * stays pure data — the renderer owns the colour binding.
 */
const STATE_COLOR: Record<BandState, string> = {
  poor: ChaosCypherPalette.error,
  ok: ChaosCypherPalette.warning,
  good: ChaosCypherPalette.success,
};

/** Props for the metric row — a sparkline ruler with a state-coloured trail. */
interface BulletChartProps {
  /** Uppercase label rendered on the left. */
  label: string;
  /** Numeric value used for trail length and band classification. */
  value: number;
  /** Band layout + scale max. See `utils/bulletBands.ts`. */
  config: BulletConfig;
  /** Brand colour for the metric label on the left. */
  color: string;
  /** Tooltip rendered on hover. */
  tooltip: React.ReactNode;
  /**
   * Override the rendered value text. Useful when value is a raw number but
   * the desired display has a unit (e.g. "3.8%") or different precision.
   */
  displayValue?: string;
}

/**
 * Metric row: a thin ruler with band-boundary ticks, a state-coloured trail
 * running from 0 → value, and a glowing dot at the trail's tip.
 *
 * Colour encoding: the trail, dot, and value number adopt the state colour
 * (`error` / `warning` / `success` from the palette) of the band the value
 * falls into. The left-hand label keeps the metric's brand colour so the
 * row stays identifiable at a glance.
 *
 * The root element carries `data-band="<band label>"` so tests + screen
 * readers can introspect the classified band without parsing the visual.
 */
export default function BulletChart({
  label,
  value,
  config,
  color,
  tooltip,
  displayValue,
}: BulletChartProps) {
  const fill = fillPct(value, config);
  const band = classify(value, config);
  const stateColor = STATE_COLOR[band.state];
  const ticks = tickPositions(config);
  const text = displayValue ?? String(value);

  return (
    <Tooltip title={tooltip} arrow placement="bottom-start">
      <Box
        data-band={band.label}
        sx={{
          display: 'grid',
          gridTemplateColumns: '78px 1fr 60px',
          gap: '12px',
          alignItems: 'center',
          cursor: 'default',
        }}
      >
        <Typography
          sx={{
            fontSize: '9px',
            letterSpacing: '2px',
            textTransform: 'uppercase',
            color,
            opacity: 0.65,
            textAlign: 'left',
            fontWeight: 400,
          }}
        >
          {label}
        </Typography>

        <Box
          sx={{
            position: 'relative',
            height: '12px',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {/* Base ruler — faint 1-pixel line. */}
          <Box
            sx={{
              position: 'absolute',
              left: 0,
              right: 0,
              height: '1px',
              background: Overlays.light.dark,
              borderRadius: '1px',
            }}
          />

          {/* Band-boundary ticks. */}
          {ticks.map((leftPct, i) => (
            <Box
              key={i}
              data-testid="metric-tick"
              sx={{
                position: 'absolute',
                top: '2px',
                bottom: '2px',
                width: '1px',
                left: `${leftPct}%`,
                background: Overlays.lightHover.dark,
              }}
            />
          ))}

          {/* State-coloured trail from 0 → value. */}
          <Box
            data-testid="metric-trail"
            sx={{
              position: 'absolute',
              top: '50%',
              transform: 'translateY(-50%)',
              left: 0,
              height: '2px',
              borderRadius: '1px',
              background: `linear-gradient(90deg, ${stateColor}00, ${stateColor})`,
              boxShadow: `0 0 10px ${stateColor}77`,
              transition: 'width 600ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
            style={{ width: `${fill}%` }}
          />

          {/* Dot at the trail's tip. */}
          <Box
            data-testid="metric-dot"
            sx={{
              position: 'absolute',
              top: '50%',
              width: '10px',
              height: '10px',
              borderRadius: '50%',
              transform: 'translate(-50%, -50%)',
              background: stateColor,
              boxShadow: `0 0 14px ${stateColor}cc`,
              transition: 'left 600ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
            style={{ left: `${fill}%` }}
          />
        </Box>

        <Typography
          sx={{
            fontSize: 11,
            fontWeight: 400,
            color: stateColor,
            opacity: 0.9,
            ml: '10px',
          }}
        >
          {text}
        </Typography>
      </Box>
    </Tooltip>
  );
}
