// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useSigmaTheme: Syncs MUI theme with sigma rendering settings.
 *
 * Watches useTheme() and updates sigma's label/edge colors and the
 * container div background to match dark/light mode.
 */

import { useEffect } from 'react';
import { useSigma } from '@react-sigma/core';
import { useTheme } from '@mui/material';
import type { NodeAttributes, EdgeAttributes } from '../types';
import { GraphColors } from '../../../theme/colors';

export function useSigmaTheme() {
  const sigma = useSigma<NodeAttributes, EdgeAttributes>();
  const theme = useTheme();

  useEffect(() => {
    const isDark = theme.palette.mode === 'dark';

    sigma.setSetting('labelColor', {
      color: isDark ? GraphColors.dark.label : GraphColors.light.label,
    });
    sigma.setSetting('edgeLabelColor', {
      color: isDark ? GraphColors.dark.edgeLabel : GraphColors.light.edgeLabel,
    });
    sigma.setSetting('defaultEdgeColor', isDark ? GraphColors.dark.edge : GraphColors.light.edge);

    // Set container background with dot-grid pattern in dark mode
    const container = sigma.getContainer();
    container.style.backgroundColor = isDark ? GraphColors.dark.background : GraphColors.light.background;
    if (isDark) {
      container.style.backgroundImage =
        'radial-gradient(circle, rgba(255, 255, 255, 0.04) 1px, transparent 1px)';
      container.style.backgroundSize = '24px 24px';
    } else {
      container.style.backgroundImage = 'none';
    }
  }, [sigma, theme.palette.mode]);
}
