// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect } from 'vitest';
import { createAppTheme } from '../appTheme';
import {
  ChaosCypherPalette,
  ChaosCypherBackground,
  ChaosCypherNeutrals,
} from '../palette';

describe('createAppTheme', () => {
  it('applies the neon brand + dark neutrals in dark mode', () => {
    const theme = createAppTheme(true);

    expect(theme.palette.mode).toBe('dark');
    expect(theme.palette.primary.main).toBe(ChaosCypherPalette.primary);
    expect(theme.palette.background.default).toBe(
      ChaosCypherBackground.dark.default,
    );
    // Dark mode keeps the calibrated slate text hierarchy.
    expect(theme.palette.text.primary).toBe(ChaosCypherNeutrals.textPrimary);
    expect(theme.palette.text.secondary).toBe(
      ChaosCypherNeutrals.textSecondary,
    );
  });

  it('uses accessible light-mode text instead of the dark slate values', () => {
    const theme = createAppTheme(false);

    expect(theme.palette.mode).toBe('light');
    expect(theme.palette.background.default).toBe(
      ChaosCypherBackground.light.default,
    );
    // The dark neutrals are near-white; applying them on the light background
    // would be an unreadable contrast failure. Light mode must NOT reuse them.
    expect(theme.palette.text.primary).not.toBe(
      ChaosCypherNeutrals.textPrimary,
    );
    expect(theme.palette.text.secondary).not.toBe(
      ChaosCypherNeutrals.textSecondary,
    );
    // MUI's light default primary text is near-black (rgba(0,0,0,0.87)).
    expect(theme.palette.text.primary.toLowerCase()).toContain('0, 0, 0');
  });
});
