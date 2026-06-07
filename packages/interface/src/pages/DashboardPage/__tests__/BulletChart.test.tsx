// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { ChaosCypherPalette } from '../../../theme/palette';
import BulletChart from '../BulletChart';
import {
  AVG_REL_BANDS,
  DENSITY_BANDS,
  QUALITY_BANDS,
} from '../utils/bulletBands';

describe('BulletChart', () => {
  it('renders the label', () => {
    const { getByText } = render(
      <BulletChart
        label="Quality"
        value={68}
        config={QUALITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    expect(getByText('Quality')).toBeTruthy();
  });

  it('renders displayValue when provided', () => {
    const { getByText } = render(
      <BulletChart
        label="Density"
        value={3.8}
        config={DENSITY_BANDS}
        displayValue="3.8%"
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    expect(getByText('3.8%')).toBeTruthy();
  });

  it('falls back to value when displayValue is omitted', () => {
    const { getByText } = render(
      <BulletChart
        label="Avg Rel"
        value={6.1}
        config={AVG_REL_BANDS}
        color={ChaosCypherPalette.secondary}
        tooltip=""
      />,
    );
    expect(getByText('6.1')).toBeTruthy();
  });

  it('sets trail width as a percentage of scaleMax', () => {
    const { container } = render(
      <BulletChart
        label="Density"
        value={3.8}
        config={DENSITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    const trail = container.querySelector('[data-testid="metric-trail"]') as HTMLElement;
    // 3.8 / 5 = 76
    expect(trail.style.width).toBe('76%');
  });

  it('positions the marker dot at the trail tip', () => {
    const { container } = render(
      <BulletChart
        label="Density"
        value={3.8}
        config={DENSITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    const dot = container.querySelector('[data-testid="metric-dot"]') as HTMLElement;
    expect(dot.style.left).toBe('76%');
  });

  it('clamps trail at 100% when value exceeds scaleMax', () => {
    const { container } = render(
      <BulletChart
        label="Quality"
        value={150}
        config={QUALITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    const trail = container.querySelector('[data-testid="metric-trail"]') as HTMLElement;
    expect(trail.style.width).toBe('100%');
  });

  it('renders one tick per internal band boundary', () => {
    const { container } = render(
      <BulletChart
        label="Quality"
        value={68}
        config={QUALITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    const ticks = container.querySelectorAll('[data-testid="metric-tick"]');
    // Three bands → two internal boundaries (the final upperBound = scaleMax is skipped).
    expect(ticks.length).toBe(QUALITY_BANDS.bands.length - 1);
  });

  it('exposes the classified band label for screen readers and tooltips', () => {
    const { container } = render(
      <BulletChart
        label="Density"
        value={3.8}
        config={DENSITY_BANDS}
        color={ChaosCypherPalette.primary}
        tooltip=""
      />,
    );
    const root = container.querySelector('[data-band]') as HTMLElement;
    // 3.8 falls in the "Dense" band (>= 1, < 5)
    expect(root.dataset.band).toBe('Dense');
  });
});
