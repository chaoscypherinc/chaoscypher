// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Application MUI theme factory.
 *
 * Builds the root theme from the master palette (`palette.ts`) for a given
 * color mode. Extracted from `App.tsx` so the mode-dependent palette wiring
 * can be unit-tested in isolation.
 */

import { createTheme, alpha, type Theme } from '@mui/material';
import {
  ChaosCypherPalette,
  ChaosCypherBackground,
  ChaosCypherNeutrals,
} from './palette';
import { getComponentOverrides } from './componentOverrides';

/**
 * Create the root application theme for the given color mode.
 *
 * @param darkMode - true for the dark theme, false for the light theme.
 */
export function createAppTheme(darkMode: boolean): Theme {
  return createTheme({
    palette: {
      mode: darkMode ? 'dark' : 'light',
      primary: { main: ChaosCypherPalette.primary },
      secondary: { main: ChaosCypherPalette.secondary },
      error: { main: ChaosCypherPalette.error },
      warning: { main: ChaosCypherPalette.warning },
      info: { main: ChaosCypherPalette.info },
      success: { main: ChaosCypherPalette.success },
      background: darkMode
        ? ChaosCypherBackground.dark
        : ChaosCypherBackground.light,
      // The neutral slate values are calibrated for dark surfaces only (see
      // palette.ts — "primary text on dark backgrounds"). Applying them in
      // light mode renders near-white text on a near-white background, so we
      // only override text/divider in dark mode and let MUI supply its
      // accessible light-mode defaults otherwise.
      ...(darkMode
        ? {
            text: {
              primary: ChaosCypherNeutrals.textPrimary,
              secondary: ChaosCypherNeutrals.textSecondary,
              disabled: ChaosCypherNeutrals.textTertiary,
            },
            divider: alpha(ChaosCypherNeutrals.borderDivider, 0.4),
          }
        : {}),
    },
    components: getComponentOverrides(),
  });
}
